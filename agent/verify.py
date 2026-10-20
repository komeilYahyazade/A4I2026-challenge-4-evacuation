"""Verify shelters against Google Maps, one named building at one address per call, and act on it.

This is where grounding changes the answer:
- Maps finds nothing at the address, or marks it permanently closed: DROP it from destinations.
- Maps knows it under another name: KEEP it and flag the registry record for update.
- Maps and FEMA disagree on wheelchair access: KEEP it and send someone to look.
- Maps says nothing: the claim stays UNRECORDED. Unknown never becomes yes or no.
- A grounded call that returns no Maps sources is not evidence, whatever its text says.
"""

import asyncio
import collections
import datetime
import os
import threading
import time
from typing import Any, Mapping, Optional

from google.adk.tools import ToolContext

from . import data
from .config import AgentConfig
from .evidence import (
    NO, RECORDED, UNRECORDED, VERIFIED_LIVE, YES, collect_maps_sources, make_fix, payload, ref, three_state,
)
from .grounding import (
    build_verify_prompt,
    extract_route,
    extract_sources,
    grounding_metadata,
    maps_tool,
    names_match,
    parse_json_block,
    retrieval_config,
    sanitize_for_storage,
)
from .reachability import MODEL_REPORTED, ROUTED

_client: Any = None

# Process-wide cap on grounded verification calls per rolling hour (MAPS_BUDGET_PER_HOUR). The agent
# runs as a single Cloud Run instance, so this bounds what anonymous traffic can spend on Google Maps.
_budget_lock = threading.Lock()
_recent_calls: "collections.deque[float]" = collections.deque()


def take_budget(n: int, per_hour: int, now: Optional[float] = None) -> int:
    """Reserve up to n grounded calls within the hourly budget; returns how many may proceed."""
    if per_hour <= 0:
        return n
    now = time.time() if now is None else now
    with _budget_lock:
        while _recent_calls and now - _recent_calls[0] > 3600:
            _recent_calls.popleft()
        allowed = max(0, min(n, per_hour - len(_recent_calls)))
        _recent_calls.extend([now] * allowed)
        return allowed


def _genai_client(cfg: AgentConfig) -> Any:
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(
            vertexai=True,
            project=cfg.project_id or None,
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        )
    return _client


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


async def ground_one(
    row: Mapping[str, Any], origin: Optional[Mapping[str, Any]], cfg: AgentConfig
) -> dict[str, Any]:
    """One grounded call about one building. Falls back to Places-only if routing is refused."""
    from google.genai import types

    prompt = build_verify_prompt(row, origin)
    want_routing = bool(origin)
    lat, lng = row.get("latitude"), row.get("longitude")
    tool_config = (
        types.ToolConfig(retrieval_config=retrieval_config(float(lat), float(lng)))
        if lat is not None and lng is not None
        else None
    )
    attempts = [True, False] if want_routing else [False]
    last_error = None
    for routing in attempts:
        config = types.GenerateContentConfig(tools=[maps_tool(routing)], tool_config=tool_config)
        try:
            resp = await asyncio.wait_for(
                _genai_client(cfg).aio.models.generate_content(
                    model=cfg.model_name, contents=prompt, config=config
                ),
                timeout=cfg.verify_timeout_s,
            )
        except Exception as exc:  # routing is newer than places; retry without it once
            last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            continue
        meta = grounding_metadata(resp)
        return {
            "text": getattr(resp, "text", None),
            "sources": extract_sources(meta),
            "route": extract_route(meta),
            "routing_requested": want_routing,
            "routing_used": routing,
            "error": None,
            "routing_error": last_error if want_routing and not routing else None,
        }
    return {
        "text": None, "sources": [], "route": None, "routing_requested": want_routing,
        "routing_used": False, "error": last_error, "routing_error": None,
    }


def _tri(value: Any) -> Any:
    """Normalize a model answer to True, False or 'unknown'."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower() if value is not None else ""
    if text in ("true", "yes"):
        return True
    if text in ("false", "no"):
        return False
    return "unknown"


def _word(value: Any, allowed: tuple[str, ...]) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return text if text in allowed else "unknown"


def decide(row: Mapping[str, Any], raw: Mapping[str, Any], verified_at: Optional[str] = None) -> dict[str, Any]:
    """Pure: combine one FEMA record with one grounded Maps answer into a verdict and fixes."""
    verified_at = verified_at or _now()
    sid = str(row.get("shelter_id"))
    record_name = row.get("shelter_name")
    record = {
        "label": RECORDED,
        "source": "FEMA National Shelter System (historical registry)",
        "evacuation_capacity": row.get("evacuation_capacity"),
        "capacity_status": row.get("capacity_status") or UNRECORDED,
        "capacity_implausible": bool(row.get("capacity_implausible")),
        "usage": row.get("usage") or UNRECORDED,
        "wheelchair": three_state(row.get("wheelchair_status", row.get("wheelchair_accessible"))),
        "generator": three_state(row.get("generator_status", row.get("generator_onsite"))),
        "surge": three_state(row.get("surge_status", row.get("in_surge_slosh_area"))),
        "is_medical": bool(row.get("is_medical")),
        "name_says_inactive": bool(row.get("name_says_inactive")),
    }
    sources = list(raw.get("sources") or [])
    parsed = parse_json_block(raw.get("text")) if raw.get("text") else None
    flags: list[str] = []
    fixes: list[dict[str, Any]] = []
    subject = f"{record_name} ({sid})"

    target = ref("shelter", sid, row.get("latitude"), row.get("longitude"))

    def fix(kind: str, evidence: str, action: str, label: str) -> None:
        fixes.append(make_fix(kind, subject, evidence, action, label, target))

    live: dict[str, Any] = {
        "label": VERIFIED_LIVE,
        "verified_at": verified_at,
        "sources": sources,
        "exists": "unknown",
        "current_name": None,
        "place_type": None,
        "closed": "unknown",
        "wheelchair_entrance": "unknown",
        "drive_minutes": None,
        "road_km": None,
        "drive_basis": None,
        "notes": None,
    }

    if raw.get("skipped"):
        live.update(status="SKIPPED_BUDGET", label=UNRECORDED,
                    notes="Live check skipped: the hourly Google Maps budget for this deployment is used up.")
    elif raw.get("error"):
        live.update(status="ERROR", label=UNRECORDED, notes=raw["error"])
    elif not sources:
        live.update(status="NO_MAPS_EVIDENCE", label=UNRECORDED,
                    notes="The grounded call returned no Google Maps source; its text is not evidence.")
    elif parsed is None:
        live.update(status="UNPARSEABLE", notes="Maps sources returned but the answer could not be parsed.")
    else:
        live.update(
            status="VERIFIED",
            exists=_tri(parsed.get("exists")),
            current_name=parsed.get("current_name") or None,
            place_type=parsed.get("place_type") or None,
            closed=_word(parsed.get("closed"), ("no", "permanently", "temporarily")),
            wheelchair_entrance=_word(parsed.get("wheelchair_entrance"), ("yes", "no")),
            notes=parsed.get("notes"),
        )
        route = raw.get("route")
        if route and route.get("drive_minutes") is not None:
            live.update(drive_minutes=route["drive_minutes"], road_km=route.get("road_km"), drive_basis=ROUTED)
        elif isinstance(parsed.get("drive_minutes"), (int, float)):
            live.update(drive_minutes=float(parsed["drive_minutes"]), drive_basis=MODEL_REPORTED)

    verified = live["status"] == "VERIFIED"
    verdict, why = "KEEP_UNVERIFIED", "Google Maps could not confirm this building; the record stands but is unconfirmed."

    if verified and (live["exists"] is False or live["closed"] == "permanently"):
        verdict = "DROP"
        reason = "marks it permanently closed" if live["closed"] == "permanently" else "finds no place at this address"
        why = f"Google Maps {reason}. Removed from destinations until someone confirms it in person."
        flags.append("CLOSED" if live["closed"] == "permanently" else "NOT_FOUND")
        listed = (f"FEMA still lists {record['evacuation_capacity']} spaces"
                  if record["capacity_status"] == "RECORDED" else "FEMA lists no recorded capacity")
        fix(flags[-1], f"Google Maps {reason}; {listed}.",
            "Confirm in person or by phone, then update or retire the FEMA record.", VERIFIED_LIVE)
    elif verified and live["exists"] is True:
        verdict, why = "KEEP", "Google Maps confirms a place at this address."
        if live["closed"] == "temporarily":
            flags.append("TEMPORARILY_CLOSED")
            fix("TEMPORARILY_CLOSED", "Google Maps marks it temporarily closed.",
                "Confirm it will be available in season.", VERIFIED_LIVE)
        match = names_match(record_name, live["current_name"])
        if match is False:
            flags.append("NAME_DIFFERS")
            why += f" Known on Google Maps as '{live['current_name']}'."
            if record["name_says_inactive"]:
                # FEMA's own name says inactive and another organisation now occupies the address. A shelter
                # designation is an agreement with an organisation; it has probably lapsed. Renaming the record
                # would silently hand the designation to the new occupant, so we never suggest that here.
                flags.append("SUCCESSOR_AT_ADDRESS")
                verdict = "DROP"
                why = (f"FEMA's own record calls it inactive, and Google Maps now shows '{live['current_name']}' "
                       "at this address. Removed from destinations until the new occupant confirms an agreement.")
                fix("SUCCESSOR_AT_ADDRESS",
                    f"FEMA: '{record_name}'. Google Maps now shows '{live['current_name']}' at the address.",
                    f"Confirm with {live['current_name']} whether it agreed to serve as a pre-landfall shelter, "
                    "then update or retire the FEMA record.", VERIFIED_LIVE)
            else:
                fix("NAME_DIFFERS", f"FEMA: '{record_name}'. Google Maps: '{live['current_name']}'.",
                    "Update the registry name so a name-only lookup does not miss it.", VERIFIED_LIVE)
        if record["name_says_inactive"] and verdict == "KEEP":
            # Same organisation still there, but the record says inactive: never a confident KEEP.
            verdict = "KEEP_UNVERIFIED"
            why += " FEMA's own record calls it inactive, so it is not counted as a destination."
            fix("NAME_SAYS_INACTIVE", f"FEMA's record reads '{record_name}'; Google Maps shows it still there.",
                "Confirm with the operator whether the shelter agreement is still in force.", VERIFIED_LIVE)
    elif not verified and live["status"] in ("NO_MAPS_EVIDENCE", "ERROR"):
        fix("UNCONFIRMED", "Google Maps returned no source for this building.",
            "Confirm by phone that the building is still in use as a shelter.", UNRECORDED)

    entrance = live["wheelchair_entrance"]
    rec_wc = record["wheelchair"]
    if entrance in ("yes", "no") and rec_wc in (YES, NO) and (entrance == "yes") != (rec_wc == YES):
        flags.append("ACCESSIBILITY_CONFLICT")
        accessibility = {
            "label": VERIFIED_LIVE,
            "summary": f"FEMA records wheelchair access as {rec_wc}; Google Maps reports the entrance as {entrance}.",
        }
        fix("ACCESSIBILITY_CONFLICT", accessibility["summary"],
            "Send someone to check unaccompanied wheelchair entry before the season.", VERIFIED_LIVE)
    elif entrance in ("yes", "no") and rec_wc == UNRECORDED:
        accessibility = {
            "label": VERIFIED_LIVE,
            "summary": f"No FEMA record. Google Maps reports a wheelchair-accessible entrance: {entrance}. "
            "That is evidence about an entrance, not an ADA certification.",
        }
    elif rec_wc in (YES, NO):
        accessibility = {"label": RECORDED, "summary": f"FEMA records wheelchair access as {rec_wc}. Google Maps did not say."}
    else:
        accessibility = {"label": UNRECORDED, "summary": "Nobody has recorded whether a wheelchair user can get in, and Google Maps did not say."}
        if verdict != "DROP":
            fix("UNASSESSED_ACCESSIBILITY", "Wheelchair access is blank in FEMA and unknown on Google Maps.",
                "Ask the operator: can a wheelchair user get in unaccompanied, today?", UNRECORDED)

    if record["capacity_implausible"]:
        flags.append("CAPACITY_IMPLAUSIBLE")
    if record["name_says_inactive"]:
        flags.append("NAME_SAYS_INACTIVE")
    if record["surge"] == YES and row.get("is_pre_landfall"):
        flags.append("PRE_LANDFALL_IN_SURGE_ZONE")

    pointer = {}
    if verified and sources:
        pointer = sanitize_for_storage(sources[0])

    return {
        "shelter_id": sid,
        "shelter_name": record_name,
        "name": record_name,
        "lat": row.get("latitude"),
        "lng": row.get("longitude"),
        "address": ", ".join(str(p) for p in (row.get("address_1"), row.get("city"), row.get("zip")) if p),
        "record": record,
        "live": live,
        "accessibility": accessibility,
        "verdict": verdict,
        "why": why,
        "flags": flags,
        "fixes": fixes,
        "stored_pointer": pointer,
    }


def ledger_from(results: list[dict[str, Any]], raws: list[Mapping[str, Any]]) -> dict[str, Any]:
    changes = [
        f"{r['verdict']}: {r['shelter_name']}: {r['why']}"
        for r in results
        if r["verdict"] == "DROP" or set(r["flags"]) & {
            "NAME_DIFFERS", "ACCESSIBILITY_CONFLICT", "TEMPORARILY_CLOSED", "NAME_SAYS_INACTIVE"}
    ]
    called = [r for r in results if r["live"]["status"] != "SKIPPED_BUDGET"]
    return {
        "maps_calls": len(called),
        "skipped_budget": len(results) - len(called),
        "with_maps_sources": sum(1 for r in results if r["live"]["sources"]),
        "definite": sum(1 for r in results if r["live"]["status"] == "VERIFIED" and r["live"]["exists"] in (True, False)),
        "unknown": sum(1 for r in called if r["live"]["status"] != "VERIFIED" or r["live"]["exists"] == "unknown"),
        "dropped": sum(1 for r in results if r["verdict"] == "DROP"),
        "wheelchair_answers_from_maps": sum(1 for r in results if r["live"]["wheelchair_entrance"] in ("yes", "no")),
        "routing_requested": sum(1 for x in raws if x.get("routing_requested")),
        "routes_returned": sum(1 for r in results if r["live"]["drive_basis"] == ROUTED),
        "changes": changes,
    }


def merge_ledger(state: Any, ledger: Mapping[str, Any]) -> dict[str, Any]:
    """Accumulate the session's grounding ledger in ADK session state."""
    if state is None:
        return dict(ledger)
    prev = dict(state.get("grounding_ledger") or {})
    merged = {k: prev.get(k, 0) + v for k, v in ledger.items() if isinstance(v, int)}
    merged["changes"] = list(prev.get("changes", [])) + list(ledger.get("changes", []))
    state["grounding_ledger"] = merged
    return merged


async def verify_rows(
    rows: list[Mapping[str, Any]], origins: Mapping[str, Mapping[str, Any]], cfg: AgentConfig
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Records that contradict themselves (name says inactive) are checked first.
    rows = sorted(rows, key=lambda r: not r.get("name_says_inactive"))[: cfg.verify_max]
    sem = asyncio.Semaphore(max(1, cfg.verify_concurrency))

    async def one(row: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any]]:
        async with sem:
            raw = await ground_one(row, origins.get(str(row.get("shelter_id"))), cfg)
            return decide(row, raw), raw

    allowed = take_budget(len(rows), cfg.maps_budget_per_hour)
    skipped = [(decide(r, {"skipped": True}), {"skipped": True}) for r in rows[allowed:]]
    pairs = list(await asyncio.gather(*(one(r) for r in rows[:allowed]))) + skipped
    results = [p[0] for p in pairs]
    return results, ledger_from(results, [p[1] for p in pairs])


async def verify_shelters(
    shelter_ids: list[str],
    origin_tract_geo_id: str = "",
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Check named shelters against live Google Maps, one building per call, and decide what changes.

    Use this whenever a shelter is about to be recommended, and whenever the manager asks if a
    shelter is still there, what it is now, or whether it is open. Pass at most 8 shelter ids.
    If origin_tract_geo_id is given, also asks Google Maps routing for the drive time from that
    census tract to each shelter.

    Returns, per shelter: the FEMA record (labelled RECORDED), the live Maps result (labelled
    VERIFIED LIVE with a timestamp and the Google Maps sources to display), a verdict of KEEP,
    DROP or KEEP_UNVERIFIED with the reason, flags, and fixes for the brief. Also returns a
    grounding ledger counting what Maps could and could not confirm.
    """
    cfg = AgentConfig.from_env()
    rows = await asyncio.to_thread(data.shelters_by_id, cfg, shelter_ids)
    found = {r["shelter_id"] for r in rows}
    missing = [str(s) for s in shelter_ids if str(s) not in found]
    origins: dict[str, dict[str, Any]] = {}
    if origin_tract_geo_id:
        from .config import tract_geo_id_or_error

        o = await asyncio.to_thread(data.tract_origin, cfg, tract_geo_id_or_error(origin_tract_geo_id, cfg))
        if o and o.get("lat") is not None:
            origins = {r["shelter_id"]: {"lat": o["lat"], "lng": o["lon"]} for r in rows}
    results, ledger = await verify_rows(rows, origins, cfg)
    session_ledger = merge_ledger(tool_context.state if tool_context else None, ledger)
    if tool_context is not None and getattr(tool_context, "state", None) is not None:
        dropped = set(tool_context.state.get("dropped_shelter_ids") or [])
        dropped.update(str(r["shelter_id"]) for r in results if r.get("verdict") == "DROP")
        if dropped:
            tool_context.state["dropped_shelter_ids"] = sorted(dropped)
    return payload("shelter_verification", {
        "verified_at": _now(),
        "results": results,
        "maps_sources": collect_maps_sources(results),
        "not_in_registry": missing,
        "skipped_over_limit": [
            r["shelter_id"] for r in sorted(rows, key=lambda r: not r.get("name_says_inactive"))[cfg.verify_max:]],
        "grounding_ledger": ledger,
        "session_grounding_ledger": session_ledger,
        "display_rule": "Show each result's Google Maps sources immediately after the sentence they support.",
    })
