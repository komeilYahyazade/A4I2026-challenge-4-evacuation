"""Conversational tools. Each answers one of the manager's questions with labelled evidence.

The heavy lifting (verification, the full brief) lives in verify.py and brief.py.
"""

import asyncio
from typing import Any, Optional

from google.adk.tools import ToolContext

from . import data
from .brief import _shelter_summary, tract_reasons
from .config import AgentConfig, county_fips_or_error, tract_geo_id_or_error
from .evidence import COUNTY_LEVEL, RECORDED, UNRECORDED, payload
from .reachability import assess, assumptions


async def find_county(name: str, tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """Look up a county in the loaded state by name and return its 5-digit FIPS code.

    Call this first when the manager names a county ("Lee County"). Every other tool takes the
    FIPS code. Also points live Google Maps lookups at that county.
    """
    cfg = AgentConfig.from_env()
    rows = await asyncio.to_thread(data.find_county, cfg, name)
    if rows and tool_context is not None:
        exact = [r for r in rows if r["county_name"].lower() == name.lower().replace(" county", "").strip()]
        best = (exact or rows)[0]
        tool_context.state["county_fips"] = best["county_fips"]
        tool_context.state["county_centroid"] = {"lat": best["lat"], "lng": best["lng"]}
    return payload("county_lookup", {"state": cfg.state, "matches": rows})


async def county_overview(county_fips: str) -> dict[str, Any]:
    """Shelter capacity and record quality for one county, plus power dependence.

    Answers "how many shelters do I have", "how many take a wheelchair", "how many people here
    depend on electricity for medical equipment". Every flag is split into yes, no and
    unrecorded; unrecorded is never folded into no. Capacity totals exclude implausible records.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    shelters, power = await asyncio.gather(
        asyncio.to_thread(data.county_shelters, cfg, fips),
        asyncio.to_thread(data.power_dependence, cfg, fips),
    )
    summary = _shelter_summary(shelters)
    wc = summary["pre_landfall_wheelchair"]
    return payload("county_overview", {
        "county_fips": fips,
        "shelters": {**summary, "label": RECORDED, "source": "FEMA National Shelter System"},
        "headline": f"Wheelchair access at pre-landfall shelters: {wc['yes'] + wc['no']} I can speak to; "
                    f"{wc['unrecorded']} nobody has assessed.",
        "power_dependence": None if power is None else {
            **power, "label": COUNTY_LEVEL,
            "note": "County-level only. A value of 11 means 1 to 10 (HHS suppresses small counts).",
        },
        "implausible_capacity_rows": [
            {"shelter_id": s["shelter_id"], "name": s["shelter_name"], "evacuation_capacity": s["evacuation_capacity"]}
            for s in shelters if s.get("capacity_implausible")
        ],
    })


async def rank_tracts(county_fips: str, limit: int = 10, ranking: str = "no_vehicle") -> dict[str, Any]:
    """Rank the census tracts in a county by evacuation need.

    ranking="no_vehicle" (default): who cannot leave. Tracts with the most households without a
    vehicle. ranking="may_need_help": who must leave and may need help. Most mobile homes, residents
    65+ and residents with a disability, by count. ranking="composite_v0": the original ranking,
    kept only to reproduce the before-and-after of the equity audit; do not use it to plan.
    Inputs are physical and demographic only. Each tract comes with reasons and evidence labels,
    the nearest pre-landfall shelter, and how many shelters within a 30-minute run are recorded
    wheelchair accessible, recorded not accessible, or never assessed.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    rows = await asyncio.to_thread(data.ranked_tracts, cfg, fips, limit, ranking)
    out = []
    for rank, t in enumerate(rows, start=1):
        out.append({
            "rank": rank,
            **t,
            "reasons": tract_reasons(t, focus="may_need_help" if ranking == "may_need_help" else "no_vehicle"),
            "mobility_to_nearest_pre_landfall": assess(
                t.get("km_pre_landfall_shelter"), t.get("households_no_vehicle"), t.get("households")),
        })
    return payload("tract_ranking", {"county_fips": fips, "ranking": ranking, "assumptions": assumptions(),
                                     "tracts": out})


async def shelters_for_tract(tract_geo_id: str, limit: int = 10) -> dict[str, Any]:
    """Where people in one census tract would go: nearest pre-landfall shelters with full records.

    Returns straight-line distance (a lower bound on road distance), capacity, and wheelchair,
    generator and surge status as YES, NO or UNRECORDED. Use verify_shelters on the ones you
    intend to recommend to confirm they exist and to get routed drive times.
    """
    cfg = AgentConfig.from_env()
    gid = tract_geo_id_or_error(tract_geo_id, cfg)
    rows, origin = await asyncio.gather(
        asyncio.to_thread(data.shelters_for_tract, cfg, gid, limit),
        asyncio.to_thread(data.tract_origin, cfg, gid),
    )
    wc = [r["wheelchair_status"] for r in rows]
    nearest_active = next((r for r in rows if not r.get("name_says_inactive")), rows[0] if rows else None)
    return payload("tract_destinations", {
        "tract": gid,
        "shelters": rows,
        "wheelchair": {
            "recorded_yes": wc.count("YES"), "recorded_no": wc.count("NO"), "unrecorded": wc.count("UNRECORDED"),
            "summary": f"{wc.count('YES') + wc.count('NO')} I can speak to; {wc.count('UNRECORDED')} nobody has assessed.",
            "label": UNRECORDED if wc.count("UNRECORDED") else RECORDED,
        },
        "mobility_to_nearest": assess(
            nearest_active["straight_line_km"] if nearest_active else None,
            (origin or {}).get("households_no_vehicle"), (origin or {}).get("households")),
    })


async def lookup_shelter(name: str, county_fips: str = "") -> dict[str, Any]:
    """Find shelters in the FEMA registry by (part of) their name, optionally within one county.

    Use this to get the shelter_id when the manager asks about a specific building, then call
    verify_shelters with that id to check it against Google Maps.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg) if county_fips else ""
    rows = await asyncio.to_thread(data.lookup_shelter, cfg, name, fips)
    return payload("shelter_lookup", {"matches": rows, "label": RECORDED})


async def care_facility_gaps(county_fips: str) -> dict[str, Any]:
    """Nursing homes in a county and how far each is from a pre-landfall shelter recorded with a generator.

    Answers the medically dependent questions: where the concentrations of people who cannot
    self-evacuate are, and where a generator or transfer agreement should exist before September.
    Pairs with county-level power dependence from HHS emPOWER.
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    care, power = await asyncio.gather(
        asyncio.to_thread(data.care_facilities, cfg, fips),
        asyncio.to_thread(data.power_dependence, cfg, fips),
    )
    return payload("care_facility_gaps", {
        "county_fips": fips,
        "nursing_homes": care,
        "label": RECORDED,
        "source": "CMS Nursing Home Provider Information; FEMA NSS generator_onsite",
        "note": "Distance is to shelters where a generator is recorded. Unrecorded generators are not counted as present.",
        "power_dependence": None if power is None else {**power, "label": COUNTY_LEVEL},
        "not_covered": "Assisted living facilities: no national public roster exists.",
    })


def coverage(ranked: list[dict[str, Any]], totals: dict[str, Any]) -> dict[str, Any]:
    """How much of the county's need the recommended tracts hold."""
    out: dict[str, Any] = {}
    for key in ("households_no_vehicle", "mobile_homes", "pop_65_plus"):
        covered = sum(float(r.get(key) or 0) for r in ranked)
        total = float(totals.get(key) or 0)
        out[key] = {"covered": int(round(covered)), "county_total": int(round(total)),
                    "share": round(covered / total, 4) if total else None}
    return out


async def audit_equity(county_fips: str, top_tracts: int = 15, ranking: str = "no_vehicle") -> dict[str, Any]:
    """Audit who the recommendations land on, after the fact.

    Takes the top-ranked tracts and compares their racial, ethnic and poverty composition with
    the county and the state, population-weighted, from the public Census ACS. Race is never a
    ranking input; it is used here only to check the output. Also reports coverage (what share of
    the county's car-less households, mobile homes and older residents the list reaches) and
    reporting bias (whether nearby shelters have their wheelchair field filled in less often than
    shelters statewide). Call again with ranking="composite_v0" to show the ranking before the
    audit changed it. Report the result either way, including "no difference".
    """
    cfg = AgentConfig.from_env()
    fips = county_fips_or_error(county_fips, cfg)
    ranked, totals = await asyncio.gather(
        asyncio.to_thread(data.ranked_tracts, cfg, fips, max(5, min(int(top_tracts), 100)), ranking),
        asyncio.to_thread(data.county_totals, cfg, fips),
    )
    ids = [r["geo_id"] for r in ranked]
    result = await asyncio.to_thread(data.equity_audit, cfg, fips, ids)
    rep = {r["cohort"]: r for r in result["reporting"]}
    for r in rep.values():
        r["recorded_share"] = round(r["wheelchair_recorded"] / r["shelters"], 4) if r["shelters"] else None
    near, state = rep.get("near_recommended", {}), rep.get("state", {})
    gap = None
    if near.get("recorded_share") is not None and state.get("recorded_share") is not None:
        gap = round(near["recorded_share"] - state["recorded_share"], 4)
    return payload("equity_audit", {
        "county_fips": fips,
        "ranking": ranking,
        "recommended_tracts": ids,
        "coverage": coverage(ranked, totals),
        **result,
        "reporting_gap_vs_state": gap,
        "how_to_read": (
            "demographics: population-weighted shares for the recommended tracts, the county and the state. "
            "reporting: share of nearby pre-landfall shelters whose wheelchair field is filled in. A negative "
            "reporting gap means the people we prioritize are served by worse paperwork, so any ranking built on "
            "recorded accessibility would have pushed help away from them."
        ),
    })


def _cfg_with_project() -> AgentConfig:
    """Return AgentConfig with a guaranteed GCP project_id for live BigQuery and Vertex AI calls."""
    from dataclasses import replace

    cfg = AgentConfig.from_env()
    if not cfg.project_id or cfg.project_id == "demo-project":
        cfg = replace(cfg, project_id="qwiklabs-gcp-03-7a0c015d2e8a")
    return cfg


def _resolve_county(cfg: AgentConfig, county_name_or_fips: str) -> dict[str, Any]:
    """Resolve a county name (e.g. 'Miami-Dade', 'Lee County') or 5-digit FIPS to its county row."""
    raw = (county_name_or_fips or "Miami-Dade").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 5 and digits.startswith(cfg.state_fips):
        return data.county(cfg, digits)
    rows = data.find_county(cfg, name=raw)
    if not rows:
        raise ValueError(f"County {county_name_or_fips!r} not found in {cfg.state}.")
    cleaned = raw.lower().replace(" county", "").strip()
    exact = [r for r in rows if str(r.get("county_name", "")).lower() == cleaned]
    return (exact or rows)[0]


def verify_shelter_on_google_maps(
    shelter_name: str,
    address: str,
    city: str,
    state: str = "FL",
) -> dict[str, Any]:
    """Verify one named shelter at one specific street address using live Vertex AI Google Maps Grounding.

    Queries Google Maps Places and Routing for a single facility, extracts grounding_chunks and
    grounding_supports, detects whether the current name on Google Maps differs from the FEMA
    registry name, reports wheelchair-accessible entrance metadata, and includes compliant
    <span translate="no">Google Maps</span> HTML attribution. Prefer verify_shelters when a shelter_id is known.
    """
    from .grounding import verify_place_with_google_maps

    cfg = _cfg_with_project()
    result = verify_place_with_google_maps(
        shelter_name=shelter_name,
        address=address,
        city=city,
        state=state,
        project_id=cfg.project_id,
        model_name=cfg.model_name,
    )
    return payload("shelter_maps_verification", result)


def audit_county_shelters_with_live_maps(
    county_name: str,
    max_shelters: int = 3,
) -> dict[str, Any]:
    """Audit a county's FEMA shelter registry in BigQuery and verify priority shelters live on Google Maps.

    Separates YES, NO, and UNRECORDED counts for wheelchair accessibility, backup generators, and
    storm-surge exposure so blank records are never conflated with NO. Then verifies up to
    max_shelters candidate pre-landfall shelters against live Google Maps Grounding and incorporates
    any operator-verified assessments stored in BigQuery. Prefer county_overview for standard county questions.
    """
    from .assessment_agent import get_shelter_assessments
    from .grounding import format_google_maps_attribution, verify_place_with_google_maps

    cfg = _cfg_with_project()
    county_row = _resolve_county(cfg, county_name)
    fips = county_row["county_fips"]
    resolved_name = county_row["county_name"]

    shelters = data.county_shelters(cfg, fips)
    summary = _shelter_summary(shelters)
    wc = summary["pre_landfall_wheelchair"]
    gen = summary["pre_landfall_generator"]
    surge = summary["pre_landfall_surge"]
    pre_total = max(1, summary["pre_landfall"])

    # Prioritize pre-landfall shelters that say inactive or have UNRECORDED wheelchair status
    pre_shelters = [s for s in shelters if s.get("is_pre_landfall")]
    candidates = sorted(
        pre_shelters or shelters,
        key=lambda r: (
            not bool(r.get("name_says_inactive")),
            r.get("wheelchair_status") != "UNRECORDED",
            -(int(r.get("evacuation_capacity") or 0)),
        ),
    )[: max(1, min(int(max_shelters), 8))]

    live_verifications = []
    all_sources = []
    for row in candidates:
        v = verify_place_with_google_maps(
            shelter_name=str(row.get("shelter_name") or ""),
            address=str(row.get("address_1") or ""),
            city=str(row.get("city") or ""),
            state=cfg.state,
            origin_lat=county_row.get("lat"),
            origin_lon=county_row.get("lng"),
            project_id=cfg.project_id,
            model_name=cfg.model_name,
        )
        v["shelter_id"] = row.get("shelter_id")
        v["fema_wheelchair_status"] = row.get("wheelchair_status")
        v["fema_generator_status"] = row.get("generator_status")
        v["fema_surge_status"] = row.get("surge_status")
        v["fema_evacuation_capacity"] = row.get("evacuation_capacity")
        live_verifications.append(v)
        all_sources.extend(v.get("sources") or [])

    operator_records = get_shelter_assessments(county=resolved_name, project_id=cfg.project_id)

    return payload(
        "county_shelter_live_audit",
        {
            "county_name": resolved_name,
            "county_fips": fips,
            "total_shelters": summary["shelters"],
            "pre_landfall_shelters": summary["pre_landfall"],
            "wheelchair_breakdown": {
                "yes": wc["yes"],
                "no": wc["no"],
                "unrecorded": wc["unrecorded"],
                "unrecorded_pct": round(100.0 * wc["unrecorded"] / pre_total, 1),
                "evidence_note": (
                    f"[RECORDED]/[UNRECORDED] {wc['yes']} recorded YES, {wc['no']} recorded NO, and "
                    f"{wc['unrecorded']} UNRECORDED (blank). UNRECORDED means never assessed, NOT 'no'."
                ),
            },
            "generator_breakdown": {
                "yes": gen["yes"],
                "no": gen["no"],
                "unrecorded": gen["unrecorded"],
                "unrecorded_pct": round(100.0 * gen["unrecorded"] / pre_total, 1),
            },
            "surge_breakdown": {
                "yes": surge["yes"],
                "no": surge["no"],
                "unrecorded": surge["unrecorded"],
                "unrecorded_pct": round(100.0 * surge["unrecorded"] / pre_total, 1),
            },
            "live_maps_verifications": live_verifications,
            "operator_assessments": operator_records,
            "attribution_html": format_google_maps_attribution(all_sources),
        },
    )


def build_live_county_readiness_brief(
    county_name: str = "Miami-Dade",
    verify_top_n_on_maps: int = 2,
) -> dict[str, Any]:
    """Generate a live 4-part County Evacuation Readiness Brief combining BigQuery and Google Maps Grounding.

    Queries BigQuery for top priority census tracts (households without vehicles and mobile-home/older/disabled
    residents), shelter registry gaps (separating YES, NO, and UNRECORDED), HHS emPOWER county-level power
    dependence, and CMS nursing home generator distances, then verifies the top N shelters live on Google Maps.
    Prefer build_readiness_brief for standard county readiness briefs.
    """
    from .assessment_agent import get_shelter_assessments
    from .grounding import format_google_maps_attribution, verify_place_with_google_maps

    cfg = _cfg_with_project()
    county_row = _resolve_county(cfg, county_name)
    fips = county_row["county_fips"]
    resolved_name = county_row["county_name"]

    tracts_no_veh = data.ranked_tracts(cfg, fips, 5, "no_vehicle")
    tracts_help = data.ranked_tracts(cfg, fips, 5, "may_need_help")
    shelters = data.county_shelters(cfg, fips)
    power = data.power_dependence(cfg, fips)
    care = data.care_facilities(cfg, fips)
    summary = _shelter_summary(shelters)

    top_tract = tracts_no_veh[0] if tracts_no_veh else None
    tract_candidates = (
        data.shelters_for_tract(cfg, top_tract["geo_id"], 5) if top_tract else []
    )
    origin_lat = top_tract.get("lat") if top_tract else county_row.get("lat")
    origin_lon = top_tract.get("lon") if top_tract else county_row.get("lng")

    to_verify = (tract_candidates or [s for s in shelters if s.get("is_pre_landfall")] or shelters)[
        : max(0, min(int(verify_top_n_on_maps), 6))
    ]
    live_verifications = []
    all_sources = []
    for row in to_verify:
        v = verify_place_with_google_maps(
            shelter_name=str(row.get("shelter_name") or ""),
            address=str(row.get("address_1") or ""),
            city=str(row.get("city") or ""),
            state=cfg.state,
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            project_id=cfg.project_id,
            model_name=cfg.model_name,
        )
        v["shelter_id"] = row.get("shelter_id")
        v["straight_line_km"] = row.get("straight_line_km")
        v["fema_wheelchair_status"] = row.get("wheelchair_status")
        v["fema_generator_status"] = row.get("generator_status")
        v["fema_evacuation_capacity"] = row.get("evacuation_capacity")
        live_verifications.append(v)
        all_sources.extend(v.get("sources") or [])

    operator_assessments = get_shelter_assessments(county=resolved_name, project_id=cfg.project_id)
    wc = summary["pre_landfall_wheelchair"]
    gen = summary["pre_landfall_generator"]
    surge = summary["pre_landfall_surge"]
    attr_html = format_google_maps_attribution(all_sources)

    return payload(
        "live_county_readiness_brief",
        {
            "county": {
                "name": resolved_name,
                "fips": fips,
                "lat": county_row.get("lat"),
                "lng": county_row.get("lng"),
            },
            "who_cannot_leave": {
                "label": RECORDED,
                "top_no_vehicle_tracts": [
                    {
                        "geo_id": t["geo_id"],
                        "households_no_vehicle": t.get("households_no_vehicle"),
                        "share_no_vehicle": t.get("share_no_vehicle"),
                        "total_pop": t.get("total_pop"),
                        "pop_65_plus": t.get("pop_65_plus"),
                        "pct_disability": t.get("pct_disability"),
                        "pct_mobility_modeled": t.get("pct_mobility"),
                        "km_pre_landfall_shelter": t.get("km_pre_landfall_shelter"),
                    }
                    for t in tracts_no_veh
                ],
                "top_must_leave_may_need_help_tracts": [
                    {
                        "geo_id": t["geo_id"],
                        "mobile_homes": t.get("mobile_homes"),
                        "pop_65_plus": t.get("pop_65_plus"),
                        "est_people_with_disability": t.get("est_people_with_disability"),
                        "households_no_vehicle": t.get("households_no_vehicle"),
                    }
                    for t in tracts_help
                ],
            },
            "where_they_would_go": {
                "county_shelter_summary": summary,
                "live_maps_verified_shelters": live_verifications,
                "operator_verified_assessments": operator_assessments,
                "attribution_html": attr_html,
            },
            "what_to_fix_before_season": {
                "unassessed_wheelchair_shelters_to_verify": wc["unrecorded"],
                "unassessed_generator_shelters_to_verify": gen["unrecorded"],
                "renamed_or_closed_on_maps": [
                    v
                    for v in live_verifications
                    if v.get("name_differs") or v.get("closed") in ("permanently", "temporarily") or v.get("exists") is False
                ],
                "nursing_homes_far_from_generator_shelter": [
                    c for c in care if (c.get("km_to_generator_recorded_shelter") or 999) > 15.0
                ][:10],
                "power_dependent_medicare_beneficiaries": (
                    None if power is None else {**power, "label": COUNTY_LEVEL}
                ),
            },
            "what_we_could_not_tell_you": {
                "wheelchair_unrecorded_vs_no": (
                    f"[UNRECORDED] {wc['unrecorded']} pre-landfall shelters have blank wheelchair accessibility "
                    f"(vs {wc['yes']} recorded YES and {wc['no']} recorded NO). Blank means nobody recorded it, NOT 'no'."
                ),
                "generator_unrecorded_vs_no": (
                    f"[UNRECORDED] {gen['unrecorded']} pre-landfall shelters have blank generator records "
                    f"(vs {gen['yes']} recorded YES and {gen['no']} recorded NO)."
                ),
                "surge_unrecorded_vs_no": (
                    f"[UNRECORDED] {surge['unrecorded']} pre-landfall shelters have blank storm-surge records "
                    f"(vs {surge['yes']} recorded YES and {surge['no']} recorded NO)."
                ),
                "empower_county_resolution": (
                    "[COUNTY-LEVEL] HHS emPOWER electricity-dependent Medicare counts are published only at the county "
                    "level and cannot be disaggregated to census tracts or individuals."
                ),
            },
            "attribution_html": attr_html,
        },
    )


def submit_shelter_operator_assessment(
    shelter_id: str,
    shelter_name: str,
    county: str,
    operator_statement: str,
) -> dict[str, Any]:
    """Evaluate a shelter operator's accessibility and generator statement and save it to BigQuery.

    Detects the 'chained side-door ramp / padlock / custodian escort / broken lift' trap so a shelter
    with restricted entry is marked VERIFIED_INACCESSIBLE_OR_RESTRICTED rather than accessible, generates
    a targeted follow-up question when details are ambiguous, and writes the record to BigQuery
    `evacuation_readiness.shelter_assessments`.
    """
    from .assessment_agent import evaluate_operator_statement, save_shelter_assessment_to_bq

    cfg = _cfg_with_project()
    assessment = evaluate_operator_statement(
        shelter_id=shelter_id,
        shelter_name=shelter_name,
        county=county,
        operator_statement=operator_statement,
        use_llm=True,
        project_id=cfg.project_id,
    )
    bq_result = save_shelter_assessment_to_bq(
        assessment_record=assessment,
        project_id=cfg.project_id,
        dataset=cfg.dataset,
    )
    return payload(
        "shelter_operator_assessment",
        {
            **assessment,
            "saved_to_bigquery": bq_result["saved"],
            "bigquery_table": bq_result["table_id"],
        },
    )


import re as _re

_ACTIVE_EMERGENCY_RE = _re.compile(
    r"\b("
    r"making\s+landfall\s+right\s+now|water\s+is\s+rising|dispatch\s+(?:a\s+)?(?:rescue|boat|helicopter|ambulance)|"
    r"send\s+(?:a\s+)?rescue|trapped\s+in\s+(?:my\s+house|the\s+attic|flood\s*water)|"
    r"need\s+immediate\s+rescue|evacuate\s+me\s+right\s+now|storm\s+is\s+hitting\s+us\s+now|"
    r"hurricane\s+is\s+hitting\s+right\s+now|911\s+dispatch|flooding\s+right\s+now\s+help"
    r")\b",
    _re.I,
)


def ask_evacuation_agent(
    question: str,
    county_name: str = "Miami-Dade",
) -> dict[str, Any]:
    """Answer an evacuation readiness planning question using BigQuery, Google Maps Grounding, and Gemini.

    Immediately refuses active emergency, rescue dispatch, or live storm routing requests and directs
    the caller to dial 911 or contact the County Emergency Operations Center. For pre-season planning
    questions, synthesizes evidence-labelled data ([RECORDED], [UNRECORDED], [VERIFIED LIVE], [MODELED],
    [COUNTY-LEVEL]) from BigQuery and live Google Maps Grounding. Programmatic helper; do not call from an interactive turn.
    """
    import os
    from google import genai
    from .prompts import SYSTEM_INSTRUCTION

    q_text = (question or "").strip()
    if _ACTIVE_EMERGENCY_RE.search(q_text):
        refusal = (
            "I cannot dispatch rescue services, provide live routing, or advise during an active storm emergency. "
            "If you or someone else is in immediate danger or floodwater is rising, please **call 911** immediately "
            "or contact your local County Emergency Operations Center (EOC). This tool is strictly for pre-season "
            "evacuation readiness planning using historical federal records, not active emergency response."
        )
        return payload(
            "evacuation_agent_response",
            {
                "question": q_text,
                "county_name": county_name,
                "refused_emergency": True,
                "mode": "EMERGENCY_REFUSAL",
                "answer": refusal,
                "attribution_html": "",
            },
        )

    cfg = _cfg_with_project()
    brief = build_live_county_readiness_brief(county_name=county_name, verify_top_n_on_maps=1)

    proj = cfg.project_id
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    client = genai.Client(vertexai=True, project=proj, location=location)

    prompt = (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"Live County Readiness Context ({county_name}):\n"
        f"{brief}\n\n"
        f"Planner Question: {q_text}\n\n"
        "Answer concisely with explicit evidence labels ([RECORDED], [UNRECORDED], [VERIFIED LIVE], "
        "[MODELED], [COUNTY-LEVEL]). Always distinguish UNRECORDED (blank) from NO."
    )

    answer_text = ""
    for m in (cfg.model_name, "gemini-2.5-flash"):
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
            answer_text = (getattr(resp, "text", None) or "").strip()
            if answer_text:
                break
        except Exception:
            continue

    if not answer_text:
        wc = brief["where_they_would_go"]["county_shelter_summary"]["pre_landfall_wheelchair"]
        answer_text = (
            f"For {brief['county']['name']} County ({brief['county']['fips']}): "
            f"[RECORDED] {wc['yes']} pre-landfall shelters are recorded as wheelchair accessible, "
            f"{wc['no']} are recorded as NO, and [UNRECORDED] {wc['unrecorded']} have never been assessed (blank, not NO)."
        )

    return payload(
        "evacuation_agent_response",
        {
            "question": q_text,
            "county_name": brief["county"]["name"],
            "county_fips": brief["county"]["fips"],
            "refused_emergency": False,
            "mode": "PRE_SEASON_PLANNING",
            "answer": answer_text,
            "attribution_html": brief.get("attribution_html", ""),
            "brief_summary": brief["where_they_would_go"]["county_shelter_summary"],
        },
    )
