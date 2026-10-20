"""POST /v1/briefs: run brief_pipeline in the background, then join facts (numbers) with the
writer's judgments (words) by id. The writer never supplies a number or an id the brief shows.

Jobs live in memory and expire (one Cloud Run instance; see BUILD_SPEC.md section 14).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from api import config, data
from api.errors import ApiError, from_exception
from contract import schemas
from readiness_agent import brief_plan, shapes
from readiness_agent.agent import brief_pipeline
from readiness_agent.tools import assessments as assess_mod
from readiness_agent.tools import bq
from readiness_agent.tools import facts as facts_mod
from readiness_agent.tools import verify as verify_tool
from readiness_agent.tools.gaps import compute_gaps

log = logging.getLogger(__name__)

APP_NAME = "readiness_brief"
JOB_TTL_S = 2 * 3600
JOB_TIMEOUT_S = 420  # background task after the 202, so Cloud Run's request timeout does not apply
DROPPED = brief_plan.DROPPED

STEP_FOR_TOOL = {
    "rank_vulnerable_tracts": ("rank_tracts", "Ranking who cannot leave"),
    "list_care_facilities": ("rank_tracts", "Listing nursing homes"),
    "get_power_dependence": ("rank_tracts", "Reading power dependence"),
    "find_candidate_shelters": ("find_shelters", "Finding candidate shelters"),
    "verify_shelter": ("verify_shelters", "Checking shelters on Google Maps"),
    "find_readiness_gaps": ("find_gaps", "Finding what to fix"),
}

TRANSPORT = "households_no_vehicle (ACS): owning no car, not lacking a ride"
DISTANCE = "Straight line unless drive_minutes is shown"
SHELTER_TO_TRACT = "Nearest tract centroid by ST_DISTANCE"
POWER_CAVEAT = "County total: how many people, not where they live."


@dataclass
class Job:
    brief_id: str
    county_fips: str
    created: float = field(default_factory=time.time)
    status: str = "running"
    progress: dict = field(default_factory=lambda: {"step": "rank_tracts", "message": "Starting", "done": 0,
                                                    "total": 0})
    brief: dict | None = None
    error: dict | None = None
    facts: dict = field(default_factory=dict)
    judgments: schemas.BriefJudgments | None = None  # kept so a refresh re-assembles without the LLM
    req: schemas.BriefRequest | None = None
    audit: dict | None = None
    step_started: dict = field(default_factory=dict)
    task: asyncio.Task | None = None


JOBS: dict[str, Job] = {}
_sessions = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=brief_pipeline, session_service=_sessions)


def _expire() -> None:
    for bid in [b for b, j in JOBS.items() if time.time() - j.created > JOB_TTL_S]:
        JOBS.pop(bid, None)


async def start(req: schemas.BriefRequest) -> dict:
    _expire()
    await asyncio.to_thread(data.county_name, req.county_fips)  # 404 outside the state
    brief_id = f"brf_{uuid.uuid4().hex[:10]}"
    job = Job(brief_id=brief_id, county_fips=req.county_fips)
    job.progress["total"] = req.options.max_shelters_to_verify
    JOBS[brief_id] = job
    job.task = asyncio.create_task(_run(job, req))
    return {"brief_id": brief_id, "status": "running", "poll_url": f"/v1/briefs/{brief_id}"}


def status(brief_id: str) -> dict | None:
    job = JOBS.get(brief_id)
    if job is None:
        return None
    return {"brief_id": job.brief_id, "status": job.status, "progress": job.progress,
            "brief": job.brief, "error": job.error}


def get_job(brief_id: str) -> Job:
    job = JOBS.get(brief_id)
    if job is None:
        raise ApiError(404, "brief_not_found", f"No brief {brief_id} (jobs live in memory and expire)")
    if job.status != "complete":
        raise ApiError(409, "brief_not_complete", f"Brief {brief_id} is {job.status}")
    return job


def _mark(job: Job, step: str) -> None:
    job.step_started.setdefault(step, time.time())


def _track_progress(job: Job, event) -> None:
    for call in event.get_function_calls() or []:
        if call.name in STEP_FOR_TOOL:
            step, message = STEP_FOR_TOOL[call.name]
            job.progress["step"], job.progress["message"] = step, message
            _mark(job, step)
            if call.name == "verify_shelter":
                job.progress["total"] = max(job.progress["total"], job.progress["done"] + 1)
    for resp in event.get_function_responses() or []:
        if resp.name == "verify_shelter":
            job.progress["done"] += 1
            job.progress["total"] = max(job.progress["total"], job.progress["done"])
    if event.author == "brief_writer" and job.progress["step"] != "write_brief":
        job.progress.update(step="write_brief", message="Writing the brief")
        _mark(job, "write_brief")


async def _run(job: Job, req: schemas.BriefRequest) -> None:
    opts = req.options
    try:
        session = await _sessions.create_session(app_name=APP_NAME, user_id="api", state={
            "brief_id": job.brief_id, "county_fips": req.county_fips, "track": req.track,
            "max_shelters_to_verify": opts.max_shelters_to_verify,
            "reachable_km": opts.reachable_km_no_vehicle, "include_routes": opts.include_routes,
            "facts_digest": "",
        })
        message = types.Content(role="user", parts=[types.Part(
            text=f"Prepare the {req.track} brief for county {req.county_fips}.")])

        async def drive():
            async for event in _runner.run_async(user_id="api", session_id=session.id, new_message=message):
                _track_progress(job, event)

        await asyncio.wait_for(drive(), timeout=JOB_TIMEOUT_S)
        final = await _sessions.get_session(app_name=APP_NAME, user_id="api", session_id=session.id)
        job.facts = facts_mod.collect(final.state)
        raw = final.state.get("judgments")
        judgments = schemas.BriefJudgments.model_validate(raw) if raw else None
        job.judgments, job.req = judgments, req
        assessed = await asyncio.to_thread(assess_mod.latest_rows, None, req.county_fips)
        brief = await asyncio.to_thread(assemble_brief, job.facts, judgments, req, assessed)
        job.brief = schemas.Brief.model_validate(brief).model_dump(mode="json")
        job.progress.update(step="complete", message="Done", done=job.progress["done"])
        job.status = "complete"
        marks = sorted(job.step_started.items(), key=lambda kv: kv[1]) + [("complete", time.time())]
        log.info("brief %s step seconds: %s", job.brief_id, {a: round(marks[i + 1][1] - t, 1)
                 for i, (a, t) in enumerate(marks[:-1])})
    except Exception as e:  # never leave a job running forever
        log.exception("brief %s failed", job.brief_id)
        err = from_exception(e)
        job.error = {"code": err.code, "message": err.message, "retryable": err.retryable}
        job.status = "failed"


async def refresh(brief_id: str) -> dict:
    """Re-assemble a finished brief with the latest field assessments, from the facts and writer
    judgments it already has. No model call and no Maps call: about one BigQuery query."""
    job = get_job(brief_id)
    if job.req is None:
        raise ApiError(409, "brief_not_refreshable", f"Brief {brief_id} predates refresh support")
    assessed = await asyncio.to_thread(assess_mod.latest_rows, None, job.county_fips)
    brief = await asyncio.to_thread(assemble_brief, job.facts, job.judgments, job.req, assessed)
    job.brief = schemas.Brief.model_validate(brief).model_dump(mode="json")
    job.audit = None  # recomputed on request
    return status(brief_id)


# ---- assembling the brief ---------------------------------------------------------------------

_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def invented_numbers(text: str, allowed: set[float]) -> list[str]:
    """Numbers in writer text that appear nowhere in the facts (ids and labels count as facts).
    Compared numerically, so "401.0" matches 401 and "4.1" matches 4.12 rounded."""
    return [m for m in _NUM.findall(text or "") if float(m.replace(",", "").rstrip(".")) not in allowed]


def _ok_text(text: str | None, allowed: set[float], where: str) -> bool:
    if not text:
        return False
    bad = invented_numbers(text, allowed)
    if bad:
        log.warning("writer %s has numbers not in facts %s: %r", where, bad, text)
    return not bad


def _fmt(v) -> str:
    if v is None:
        return "unknown"
    if isinstance(v, float):
        return f"{v:g}"
    return f"{v:,}" if isinstance(v, int) and v >= 10000 else str(v)


def _tract_reason(row: dict) -> str:
    parts = []
    if row.get("households_no_vehicle") is not None:
        parts.append(f"{_fmt(round(row['households_no_vehicle']))} households own no car")
    if row.get("nearest_wheelchair_yes_km") is not None:
        parts.append(f"the nearest shelter recorded as wheelchair accessible is "
                     f"{_fmt(row['nearest_wheelchair_yes_km'])} km away (straight line)")
    return (", and ".join(parts) or "High need score") + "."


def _care_reason(row: dict) -> str:
    km = row.get("nearest_generator_yes_km")
    tail = (f"the nearest shelter with a recorded generator is {_fmt(km)} km away (straight line)"
            if km is not None else "no shelter in the record has a generator recorded")
    return f"{_fmt(row.get('certified_beds'))} certified beds, and {tail}."


def _drivers(kind: str, row: dict) -> list[dict]:
    if kind == "tract":
        spec = [("households_no_vehicle", "households", "acs"), ("pct_no_vehicle", "%", "cdc_svi"),
                ("pop_65_plus", "people", "acs"), ("pct_disability", "%", "cdc_svi"),
                ("mobile_homes", "homes", "acs"), ("group_quarters_pop", "people", "acs"),
                ("nearest_wheelchair_yes_km", "km straight line", "fema_nss")]
    else:
        spec = [("certified_beds", "beds", "cms"), ("nearest_generator_yes_km", "km straight line", "fema_nss")]
    return [{"metric": m, "value": row.get(m), "unit": u, "source": s} for m, u, s in spec
            if row.get(m) is not None]


def _default_verdict(v: dict, shelter: dict, assessment: dict | None = None) -> str:
    """The strictest reading of the evidence. holds_up needs Maps to confirm the same building
    AND wheelchair yes, and no recorded flood exposure. Wheelchair yes comes from the federal
    record with Maps not disagreeing, or from a field assessment (the operator said a wheelchair
    user can get in alone today). A Maps 'accessible entrance' alone fills a blank record but does
    not close it. An operator saying no means do not rely on it for wheelchair users."""
    if v["status"] in DROPPED or shelter["in_surge_slosh_area"] == "yes":
        return "do_not_rely"
    field = (assessment or {}).get("wheelchair_unaccompanied")
    if field == "no":
        return "do_not_rely"
    wheelchair_yes = field == "yes" or (shelter["wheelchair_accessible"] == "yes"
                                        and v["wheelchair"]["agreement"] != "disagree")
    if v["status"] == "confirmed" and wheelchair_yes and shelter["in_100_yr_floodplain"] != "yes":
        return "holds_up"
    return "verify_before_season"


def _guard_verdict(verdict: str, v: dict, shelter: dict, sid: str, assessment: dict | None = None) -> str:
    """Maps evidence wins: a closed or missing building is never relied on, and holds_up is
    never more generous than the evidence rule. The writer may still be stricter."""
    rule = _default_verdict(v, shelter, assessment)
    forced = verdict
    if rule == "do_not_rely":
        forced = "do_not_rely"
    elif verdict == "holds_up" and rule != "holds_up":
        forced = "verify_before_season"
    if forced != verdict:
        log.warning("verdict for %s overridden %s -> %s (Maps status %s)", sid, verdict, forced, v["status"])
    return forced


def _default_verdict_reason(v: dict, shelter: dict, src_ids: list[str], assessed: bool = False) -> list[dict]:
    live = {"confirmed": "Google Maps confirms the building at this address.",
            "renamed": f"Google Maps lists this address as {v.get('maps_name')}; the record name is out of date.",
            "closed": "Google Maps lists it as permanently closed; dropped from the plan.",
            "not_found": "Google Maps finds nothing at this address; dropped from the plan.",
            "unclear": "Google Maps gave no definite answer about this building.",
            "error": "The Google Maps check failed; nothing was verified."}[v["status"]]
    rec = shelter["wheelchair_accessible"]
    hist = {"yes": "The FEMA record says it is wheelchair accessible.",
            "no": "The FEMA record says it is not wheelchair accessible.",
            "unrecorded": "FEMA never recorded wheelchair access." if assessed
            else "FEMA never recorded wheelchair access, so confirm the entrance on site."}[rec]
    return [{"text": live, "source_ids": src_ids[:1], "freshness": "live"},
            {"text": hist, "source_ids": [], "freshness": "historical"}]


FIELD_SAYS = {"yes": "a wheelchair user can get in unaccompanied today",
              "no": "a wheelchair user cannot get in unaccompanied today",
              "unclear": "it is unclear whether a wheelchair user can get in unaccompanied"}


def _field_segment(a: dict) -> dict:
    """What the operator said, in a segment of its own: freshness field, no Maps sources."""
    day = str(a.get("assessed_at") or "")[:10]
    who = a.get("assessor_role") or "the shelter operator"
    text = f"Field assessment {day}: {who} says {FIELD_SAYS[a['wheelchair_unaccompanied']]}."
    if a.get("notes"):
        text += f' In their words: "{a["notes"]}"'
    return {"text": text, "source_ids": [], "freshness": "field"}


def _field_fix(sid: str, shelter: dict, a: dict, serves: list[str]) -> dict:
    return {"priority": 0, "category": "field_assessment",
            "title": f"{shelter['name']}: the operator says {FIELD_SAYS['no']}",
            "recommendation": "Agree an unlocked, staffed accessible entrance with the operator before September, "
                              "or plan a different accessible destination for the tracts it serves.",
            "affected": {"tracts": sorted(serves), "shelters": [sid], "care_facilities": []}, "source_ids": []}


def _with_field_answers(f: dict, assessed: dict[str, dict]) -> dict:
    """A copy of the facts where each candidate shelter's wheelchair flag is what the plan should
    act on: a definite field answer replaces the FEMA flag. Used only to recompute gaps; the brief
    still shows FEMA's own flag as the historical record."""
    f2 = copy.deepcopy(f)
    for cand in f2.get("candidates", {}).values():
        for s in cand.get("shelters", []):
            s["wheelchair_accessible"] = assess_mod.effective_wheelchair(
                s["wheelchair_accessible"], assessed.get(str(s["shelter_id"])))
    return f2


def _fallback_fix(g: dict, county: dict) -> dict:
    m, cat, sub = g["metrics"], g["category"], g.get("subtype")
    a = g["affected"]
    if cat == "no_accessible_destination":
        label = ", ".join(brief_plan.tract_label(t) for t in a["tracts"])
        if sub == "none_within_reach":
            title = f"No shelter within {_fmt(m['reachable_km'])} km of {label}"
            rec = "Arrange accessible transport or a pickup hub for households without a car."
        elif sub == "recorded_no":
            title = f"Every shelter within {_fmt(m['reachable_km'])} km of {label} is recorded as not wheelchair accessible"
            rec = "Designate or retrofit an accessible site nearby, or arrange accessible transport."
        else:
            title = f"No shelter recorded as wheelchair accessible within {_fmt(m['reachable_km'])} km of {label}"
            rec = (f"Assess the entrances of the {m['shelters_within_reach']} shelters within reach before "
                   f"September; nobody has recorded wheelchair access for {m['wheelchair_unrecorded']} of them.")
    elif cat == "unassessed_access":
        title = f"{m['shelters']} candidate shelters have no wheelchair-access answer from FEMA or Google Maps"
        rec = "Call or visit each site before September and record the answer."
    elif cat == "generator_gap":
        title = (f"{m['facilities']} nursing homes have no shelter with a recorded generator within "
                 f"{_fmt(m['threshold_km'])} km")
        rec = "Confirm generator-equipped receiving sites or transfer agreements for these facilities."
    elif cat == "shelter_in_surge_zone":
        title = f"{m['shelters']} candidate shelters are recorded in the surge zone or 100-year floodplain"
        rec = "Do not plan pre-landfall use of these sites without confirming their flood exposure."
    elif cat == "record_mismatch":
        title = f"FEMA record for {m['record_name']} does not match Google Maps"
        rec = {"renamed": f"Update the record to {m.get('maps_name')} and confirm the site agreement still stands.",
               "closed": "Remove it from the shelter plan and update the FEMA record.",
               "not_found": "Confirm on site whether the building still exists, then update the record."
               }.get(m["status"], "Confirm wheelchair access on site and correct the record.")
    else:  # medical_needs_gap
        title = (f"{_fmt(m['power_dependent_dme'])} power-dependent residents and "
                 f"{_fmt(m['medical_capacity'])} recorded medical-needs shelter spaces")
        rec = "Expand medical-needs shelter capacity or agreements before the season."
    return {"priority": 0, "category": cat, "title": title, "recommendation": rec, "affected": a, "source_ids": []}


def _headline(p: dict, assessed: dict[str, dict] | None = None) -> str:
    assessed = assessed or {}
    dests = p["destinations"]
    kept = [d for d in dests if d["verification"]["status"] not in DROPPED]
    dropped = len(dests) - len(kept)

    def wc(d):
        return assess_mod.effective_wheelchair((d["row"] or {}).get("wheelchair_accessible", "unrecorded"),
                                               assessed.get(d["shelter_id"]))
    wc_yes = sum(1 for d in kept if wc(d) == "yes")
    unrec = sum(1 for d in kept if wc(d) == "unrecorded")
    by_phone = sum(1 for d in kept if (assessed.get(d["shelter_id"]) or {}).get("wheelchair_unaccompanied")
                   in ("yes", "no"))
    n_tracts = sum(1 for w in p["who"] if w["kind"] == "tract")
    text = (f"Of {len(dests)} shelters checked on Google Maps for the {n_tracts} least mobile tracts, "
            f"{wc_yes} are wheelchair accessible by the record or the operator and {unrec} nobody has assessed"
            if by_phone else
            f"Of {len(dests)} shelters checked on Google Maps for the {n_tracts} least mobile tracts, "
            f"{wc_yes} are recorded as wheelchair accessible and {unrec} nobody has assessed")
    if by_phone:
        text += f"; {by_phone} answered by the operator by phone"
    return text + (f"; {dropped} dropped as closed or missing." if dropped else ".")


def assemble_brief(f: dict, j: schemas.BriefJudgments | None, req: schemas.BriefRequest,
                   assessed_rows: list[dict] | None = None) -> dict:
    """Join facts (numbers) with the writer's judgments (words). assessed_rows are the latest field
    assessments for the county (sql/15_latest_assessments.sql); with none, the brief is exactly the
    facts-plus-writer brief. With some, the shelters they cover get rule-based verdicts and wording,
    and the gaps they change are recomputed and worded by us, so nothing the writer said goes stale."""
    assessed_rows = assessed_rows or []
    assessed = {str(r["shelter_id"]): assess_mod.shape(r) for r in assessed_rows}
    p = brief_plan.plan(f)
    allowed = brief_plan.numbers_in_facts(f, p)
    smap = p["source_map"]
    allowed_src = set(p["allowed_source_ids"])
    county = p["county"]

    known_tracts = {t["geo_id"] for t in (next(iter(f.get("tracts", {}).values()), {}) or {}).get("tracts", [])}
    known_tracts |= {k for k in f.get("candidates", {}) if not k.startswith("county:")}
    known_shelters = {s["shelter_id"] for c in f.get("candidates", {}).values() for s in c["shelters"]}
    known_shelters |= set(f.get("verifications", {}))
    known_care = {c["ccn"] for c in (next(iter(f.get("care", {}).values()), {}) or {}).get("facilities", [])}

    def keep_ids(ids, known, what):
        bad = [i for i in ids if i not in known]
        if bad:
            log.warning("dropped invented %s ids from writer: %s", what, bad)
        return [i for i in ids if i in known]

    def keep_sources(ids):
        return [i for i in ids if i in allowed_src]

    reasons = {r.id: r.reason for r in (j.tract_reasons if j else [])}
    unknown_reason_ids = set(reasons) - {w["id"] for w in p["who"]}
    if unknown_reason_ids:
        log.warning("dropped writer reasons for ids not in the brief: %s", sorted(unknown_reason_ids))

    who = []
    for rank, w in enumerate(p["who"], 1):
        row = w["row"]
        fallback = _tract_reason(row) if w["kind"] == "tract" else _care_reason(row)
        reason = reasons.get(w["id"])
        who.append({
            "rank": rank, "kind": w["kind"], "id": w["id"],
            "label": brief_plan.tract_label(w["id"]) if w["kind"] == "tract" else (row["name"] or "").title(),
            "lat": row["lat"], "lon": row["lon"],
            "people": row.get("total_pop") if w["kind"] == "tract" else row.get("certified_beds"),
            "drivers": _drivers(w["kind"], row),
            "reason": reason if _ok_text(reason, allowed, f"reason {w['id']}") else fallback,
        })

    verdicts = {d.shelter_id: d for d in (j.destination_verdicts if j else [])}
    sources_all: list[dict] = []
    destinations = []
    for d in p["destinations"]:
        sid, v = d["shelter_id"], copy.deepcopy(d["verification"])
        for src in v["sources"]:
            src["id"] = smap[(sid, src["id"])]
        local = {k[1]: g for k, g in smap.items() if k[0] == sid}
        for seg in v["summary"]:
            seg["source_ids"] = [local.get(i, i) for i in seg["source_ids"]]
        sources_all += v["sources"]
        row = d["row"] or verify_tool.load_shelter(sid)
        shelter = shapes.shelter(row)
        shelter["place_id"] = next((s["place_id"] for s in v["sources"] if s["place_id"]), shelter["place_id"])
        src_ids = [s["id"] for s in v["sources"]]
        a = assessed.get(sid)
        shelter["field_assessment"] = a
        if a:  # the writer judged without this evidence: the rule decides, in our words
            destinations.append({"shelter": shelter, "serves": d["serves"], "straight_line_km": d["straight_line_km"],
                                 "verification": v, "verdict": _default_verdict(v, shelter, a),
                                 "verdict_reason": [*_default_verdict_reason(v, shelter, src_ids, assessed=True),
                                                    _field_segment(a)]})
            continue
        jv = verdicts.get(sid)
        verdict = _guard_verdict(jv.verdict, v, shelter, sid) if jv else _default_verdict(v, shelter)
        reason = [{"text": s.text, "source_ids": keep_sources(s.source_ids), "freshness": s.freshness}
                  for s in (jv.verdict_reason if jv else [])]
        reason = [s for s in reason if _ok_text(s["text"], allowed, f"verdict {sid}")]
        for s in reason:  # a live claim must cite this shelter's Maps sources
            if s["freshness"] == "live" and not s["source_ids"]:
                s["source_ids"] = src_ids[:1]
            if s["freshness"] == "live" and not s["source_ids"]:
                s["freshness"] = "historical"
        if not reason or verdict != (jv.verdict if jv else verdict):
            reason = _default_verdict_reason(v, shelter, src_ids)
        elif src_ids and not any(s["freshness"] == "live" for s in reason):
            reason.insert(0, _default_verdict_reason(v, shelter, src_ids)[0])
        destinations.append({"shelter": shelter, "serves": d["serves"], "straight_line_km": d["straight_line_km"],
                             "verification": v, "verdict": verdict, "verdict_reason": reason})

    gaps = (next(iter(f.get("gaps", {}).values()), {}) or {}).get("gaps", [])
    candidate_ids = {str(s["shelter_id"]) for c in f.get("candidates", {}).values() for s in c["shelters"]}
    touched = (set(assessed) & (candidate_ids | set(f.get("verifications", {}))))
    stale = set()
    if touched:  # access gaps change when an operator answers; recompute and word them ourselves
        stale = {"no_accessible_destination", "unassessed_access"}
        gaps = compute_gaps(_with_field_answers(f, assessed), brief_plan.reachable_km(f))
    fixes = []
    for fx in (j.fixes if j else []):
        if fx.category in stale:
            continue
        if not (_ok_text(fx.title, allowed, "fix title") and _ok_text(fx.recommendation, allowed, "fix text")):
            continue
        fixes.append({"priority": fx.priority, "category": fx.category, "title": fx.title,
                      "recommendation": fx.recommendation,
                      "affected": {"tracts": keep_ids(fx.affected.tracts, known_tracts, "tract"),
                                   "shelters": keep_ids(fx.affected.shelters, known_shelters, "shelter"),
                                   "care_facilities": keep_ids(fx.affected.care_facilities, known_care, "ccn")},
                      "source_ids": keep_sources(fx.source_ids)})
    covered = {fx["category"] for fx in fixes}
    for g in gaps:  # every gap category gets at least one fix, worded by us if the writer missed it
        if g["category"] in stale:  # recomputed after a field answer: one fix per gap, as the writer gave
            fixes.append(_fallback_fix(g, county))
        elif g["category"] not in covered:
            fixes.append(_fallback_fix(g, county))
            covered.add(g["category"])
    for d in destinations:  # an operator said no: that is a fix of its own, first in line
        a = d["shelter"].get("field_assessment")
        if a and a["wheelchair_unaccompanied"] == "no" and d["verification"]["status"] not in DROPPED:
            # -1 sorts ahead of the writer's priority 1 (0 means "unranked" and sorts last below)
            fixes.append({**_field_fix(d["shelter"]["shelter_id"], d["shelter"], a, d["serves"]), "priority": -1})
    fixes.sort(key=lambda fx: (fx["priority"] or 99))
    for i, fx in enumerate(fixes, 1):
        fx["priority"] = i

    texts = {u.topic.strip().lower(): u for u in (j.unknowns_text if j else [])}
    unknowns = []
    closed_by_phone = assess_mod.closes_unrecorded(assessed_rows)
    both_blank_answered = sum(1 for d in destinations
                              if d["verification"]["wheelchair"]["agreement"] == "both_blank"
                              and (d["shelter"].get("field_assessment") or {}).get("wheelchair_unaccompanied")
                              in ("yes", "no"))
    for u in p["unknowns"]:
        why, how = brief_plan.UNKNOWN_DEFAULTS[u["topic"]]
        ju = texts.get(u["topic"].lower())
        adjust = {"Wheelchair access": closed_by_phone,
                  "Wheelchair access after Google Maps": both_blank_answered}.get(u["topic"], 0)
        if adjust and u["count"] is not None:  # the writer's text quotes the old count: use ours
            u = {**u, "count": max(0, u["count"] - adjust)}
            how = f"{how} {adjust} answered by phone so far."
        elif ju and _ok_text(ju.why, allowed, "unknown why") and _ok_text(ju.how_to_close, allowed, "unknown how"):
            why, how = ju.why, ju.how_to_close
        unknowns.append({**u, "why": why, "how_to_close": how})

    if touched or closed_by_phone:
        headline = _headline(p, assessed)
    else:
        headline = j.headline if j and _ok_text(j.headline, allowed, "headline") else _headline(p)
    power = p["power"]
    return {
        "county": {"county_fips": req.county_fips, "name": county.get("name") or req.county_fips,
                   "state": config.STATE},
        "track": req.track,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": config.MODEL,
        "headline": headline,
        "assumptions": {"reachable_km_no_vehicle": brief_plan.reachable_km(f), "transport_measure": TRANSPORT,
                        "distance": DISTANCE, "shelter_to_tract": SHELTER_TO_TRACT,
                        "need_score": brief_plan.config.NEED_SCORE_FORMULA},
        "who_cannot_leave": who,
        "power_dependence": ({"power_dependent_dme": power["power_dependent_dme"],
                              "is_censored": bool(power["is_censored"]), "caveat": POWER_CAVEAT}
                             if power else None),
        "destinations": destinations,
        "fixes": fixes,
        "unknowns": unknowns,
        "sources": sorted(sources_all, key=lambda s: int(s["id"][4:])),
    }


# ---- equity audit -----------------------------------------------------------------------------

AUDIT_COMPARES = "Tracts in who_cannot_leave against every tract in the county and the state"


def audit(brief_id: str) -> dict:
    """Recommended vs county vs state. Race and ethnicity rows are audit only, never inputs. The
    `finding` is written by a person, so the API leaves it null."""
    job = get_job(brief_id)
    if job.audit is None:
        geo_ids = [w["id"] for w in job.brief["who_cannot_leave"] if w["kind"] == "tract"]
        rows = bq.run("08_equity_audit.sql", geo_ids=geo_ids, county_fips=job.county_fips,
                      reachable_km=job.brief["assumptions"]["reachable_km_no_vehicle"])
        job.audit = {"brief_id": brief_id, "compares": AUDIT_COMPARES, "finding": None,
                     "rows": [{k: r[k] for k in ("metric", "unit", "recommended", "county", "state",
                                                 "used_as_input")} for r in rows]}
    return job.audit
