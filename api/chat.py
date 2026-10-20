"""POST /v1/chat: follow-up questions answered by readiness_agent (custom tools, Google Maps
grounding, BigQuery MCP). Replies come back as attributed segments; Maps-derived text always
carries its sources on the next line in the UI.
"""

from __future__ import annotations

import logging
import re
import uuid

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from api import data
from api.errors import from_exception
from contract import schemas
from readiness_agent import attribution
from readiness_agent.agent import root_agent

log = logging.getLogger(__name__)

APP_NAME = "readiness_chat"
REFUSED = re.compile(r"^\s*\**REFUSED\**\s*:\s*", re.IGNORECASE)
_sessions = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=_sessions)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _attribute(text: str, verifications: list[dict], start: int) -> tuple[list[dict], list[dict]]:
    """Model prose built from verify_shelter results: a paragraph that speaks about Google Maps or
    names a building as Maps lists it cites that verification's sources; the rest is historical."""
    sources, segments = [], []
    n = start
    per_v = []
    for v in verifications:
        mapping = {}
        for s in v.get("sources", []):
            mapping[s["id"]] = f"src_{n}"
            sources.append({**s, "id": f"src_{n}"})
            n += 1
        per_v.append((v, [mapping[s["id"]] for s in v.get("sources", []) if s.get("place_id")] or list(mapping.values())))
    for para in _paragraphs(text):
        cites = []
        for v, ids in per_v:
            names = [x for x in (v.get("maps_name"), v.get("record_name")) if x]
            if "google maps" in para.lower() or any(nm.lower() in para.lower() for nm in names):
                cites += [i for i in ids[:1] if i not in cites]
        segments.append({"text": para, "source_ids": cites, "freshness": "live" if cites else "historical"})
    return segments, sources


def _highlight(calls: list[tuple[str, dict, dict]], text: str, county_fips: str | None) -> dict:
    tracts, shelters, care = [], [], []
    for name, args, resp in calls:
        if name == "verify_shelter" and args.get("shelter_id"):
            shelters.append(str(args["shelter_id"]))
        elif name == "find_candidate_shelters":
            if args.get("geo_id"):
                tracts.append(args["geo_id"])
            shelters += [s["shelter_id"] for s in (resp.get("shelters") or []) if s.get("within_reach")][:8]
        elif name == "rank_vulnerable_tracts":
            tracts += [t["geo_id"] for t in (resp.get("tracts") or [])[:5]]
    # ids the agent names in its answer, kept only if they exist on the county map
    if county_fips:
        try:
            m = data.county_map(county_fips)
            known_t = {t["geo_id"] for t in m["tracts"]}
            known_s = {s["shelter_id"] for s in m["shelters"]}
            known_c = {c["ccn"] for c in m["care_facilities"]}
            for tok in re.findall(r"\b\d{5,11}\b", text):
                if tok in known_t:
                    tracts.append(tok)
                elif tok in known_s:
                    shelters.append(tok)
                elif tok in known_c:
                    care.append(tok)
        except Exception:
            log.exception("highlight lookup failed")
    dedup = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    return {"tracts": dedup(tracts), "shelters": dedup(shelters), "care_facilities": dedup(care)}


async def chat(req: schemas.ChatRequest) -> dict:
    session = None
    if req.session_id:
        session = await _sessions.get_session(app_name=APP_NAME, user_id="api", session_id=req.session_id)
    if session is None:
        session = await _sessions.create_session(app_name=APP_NAME, user_id="api",
                                                 session_id=f"ses_{uuid.uuid4().hex[:10]}",
                                                 state={"county_fips": req.county_fips or "",
                                                        "brief_id": req.brief_id or ""})
    delta = {"county_fips": req.county_fips} if req.county_fips else None
    message = types.Content(role="user", parts=[types.Part(text=req.message)])

    started: dict[str, float] = {}
    tool_calls, calls, final_text, grounding = [], [], "", None
    try:
        async for ev in _runner.run_async(user_id="api", session_id=session.id, new_message=message,
                                          state_delta=delta):
            for c in ev.get_function_calls() or []:
                started[c.id or c.name] = ev.timestamp
                calls.append([c.name, dict(c.args or {}), {}])
            for r in ev.get_function_responses() or []:
                t0 = started.get(r.id or r.name, ev.timestamp)
                tool_calls.append({"name": r.name, "duration_ms": int(max(0.0, ev.timestamp - t0) * 1000)})
                for entry in calls:
                    if entry[0] == r.name and not entry[2]:
                        entry[2] = r.response if isinstance(r.response, dict) else {}
                        break
            if ev.grounding_metadata is not None:
                grounding = ev.grounding_metadata
            if ev.is_final_response() and ev.content and ev.content.parts:
                final_text = "".join(p.text or "" for p in ev.content.parts if not getattr(p, "thought", False))
    except Exception as e:
        raise from_exception(e) from e

    refused = bool(REFUSED.match(final_text))
    text = REFUSED.sub("", final_text).strip() or "I could not produce an answer."
    if grounding is not None and getattr(grounding, "grounding_chunks", None):
        segments, sources = attribution.segments_from_response(text, grounding)
    else:
        verifs = [resp for name, _a, resp in calls if name == "verify_shelter" and resp.get("sources")]
        segments, sources = _attribute(text, verifs, 1)
    return {"session_id": session.id, "reply": segments, "sources": sources,
            "highlight": _highlight([tuple(c) for c in calls], text, req.county_fips),
            "tool_calls": tool_calls, "refused": refused}
