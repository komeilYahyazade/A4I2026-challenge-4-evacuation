"""verify_shelter: one Gemini call with Grounding with Google Maps per building (BUILD_SPEC.md 9.1).

Our data is the memory; Maps is the eyes. This asks Maps about ONE named building at ONE address
(the only question shape that gets definite answers), parses a fixed line format, compares the
wheelchair answer with FEMA's record deterministically, and persists only the place_id.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from google import genai
from google.adk.tools import ToolContext
from google.genai import types

from readiness_agent import attribution, config
from readiness_agent.tools import bq, facts

log = logging.getLogger(__name__)

TIMEOUT_S = 30.0
RETRY_BACKOFF_S = 2.0

STATUSES = ("confirmed", "renamed", "closed", "not_found", "unclear")
OPEN_NOW = {"yes": True, "no": False, "unknown": None}
WHEELCHAIR = ("yes", "no", "no_answer")
KEYS = ("STATUS", "MAPS_NAME", "PLACE_TYPE", "OPEN_NOW", "WHEELCHAIR_ENTRANCE", "DRIVE_MINUTES", "NOTE")
# Persist place_id only when Maps matched the building itself.
PERSIST_STATUSES = ("confirmed", "renamed", "closed")

_client: genai.Client | None = None
_semaphore: asyncio.Semaphore | None = None


def _genai() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(vertexai=True, project=config.PROJECT, location="global")
    return _client


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(config.MAX_PARALLEL_VERIFY)
    return _semaphore


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---- pure functions (unit-tested) -------------------------------------------------------------


def build_prompt(shelter: dict, origin: dict | None) -> str:
    lines = [
        "I am verifying one building for a hurricane-season planning record. Use Google Maps.",
        f'Building on file: "{shelter["name"]}", {shelter.get("address_1") or ""}, '
        f'{shelter.get("city") or ""}, FL {shelter.get("zip") or ""}. '
        f'Coordinates on file: {shelter["lat"]}, {shelter["lon"]}.',
    ]
    if origin:
        lines.append(f"Also give the driving time by car from {origin['lat']}, {origin['lon']} to this building.")
    lines += [
        "Answer with exactly these lines and nothing else:",
        "STATUS: confirmed | renamed | closed | not_found | unclear",
        "MAPS_NAME: <name on Google Maps, or none>",
        "PLACE_TYPE: <what the place is now, e.g. high school, church, community center>",
        "OPEN_NOW: yes | no | unknown",
        "WHEELCHAIR_ENTRANCE: yes | no | no_answer",
        "DRIVE_MINUTES: <number, or none>",
        "NOTE: <one sentence>",
    ]
    return "\n".join(lines)


def parse(text: str | None) -> dict:
    """Parse the fixed line format. Any missing or invalid enum line makes status 'unclear'."""
    raw = {}
    for key in KEYS:
        m = re.search(rf"^\s*\**{key}\**\s*:\s*(.*?)\s*$", text or "", re.MULTILINE | re.IGNORECASE)
        raw[key] = m.group(1).strip().strip("*").strip() if m else None

    def enum(value, allowed):
        v = (value or "").strip().lower().replace(" ", "_")
        return v if v in allowed else None

    status = enum(raw["STATUS"], STATUSES)
    open_now_key = enum(raw["OPEN_NOW"], tuple(OPEN_NOW))
    wheelchair = enum(raw["WHEELCHAIR_ENTRANCE"], WHEELCHAIR)
    parse_ok = status is not None and open_now_key is not None and wheelchair is not None

    def text_or_none(v):
        return None if v is None or v.strip().lower() in ("", "none", "unknown", "n/a") else v.strip()

    minutes = None
    if raw["DRIVE_MINUTES"]:
        m = re.search(r"\d+(\.\d+)?", raw["DRIVE_MINUTES"])
        minutes = float(m.group(0)) if m else None
    return {
        "status": status if parse_ok else "unclear",
        "maps_name": text_or_none(raw["MAPS_NAME"]),
        "place_type": text_or_none(raw["PLACE_TYPE"]),
        "open_now": OPEN_NOW.get(open_now_key) if open_now_key else None,
        "wheelchair": wheelchair or "no_answer",
        "drive_minutes": minutes,
        "note": text_or_none(raw["NOTE"]),
        "parse_ok": parse_ok,
    }


def agreement(record: str, maps: str) -> str:
    """record in {yes, no, unrecorded}; maps in {yes, no, no_answer}."""
    rec, mp = record in ("yes", "no"), maps in ("yes", "no")
    if rec and mp:
        return "agree" if record == maps else "disagree"
    if mp:
        return "record_blank"
    if rec:
        return "maps_blank"
    return "both_blank"


def _duration_seconds(d) -> float | None:
    if d is None:
        return None
    if hasattr(d, "total_seconds"):
        return d.total_seconds()
    m = re.match(r"^\s*(\d+(?:\.\d+)?)s\s*$", str(d))
    return float(m.group(1)) if m else None


def drive_from(gm, parsed: dict, from_geo_id: str | None) -> dict | None:
    """Prefer a routing chunk's duration/distance, else the parsed DRIVE_MINUTES, else None."""
    for chunk in getattr(gm, "grounding_chunks", None) or []:
        route = getattr(getattr(chunk, "maps", None), "route", None)
        secs = _duration_seconds(getattr(route, "duration", None)) if route else None
        if secs is not None:
            meters = getattr(route, "distance_meters", None)
            return {"from_geo_id": from_geo_id, "drive_minutes": round(secs / 60, 1),
                    "drive_km": round(meters / 1000, 1) if meters else None}
    if parsed["drive_minutes"] is not None:
        return {"from_geo_id": from_geo_id, "drive_minutes": parsed["drive_minutes"], "drive_km": None}
    return None


def build_verification(shelter: dict, text: str | None, gm, from_geo_id: str | None,
                       include_route: bool, checked_at: str) -> dict:
    parsed = parse(text)
    sources, by_chunk = attribution.sources_from_metadata(gm)
    line_sources: dict[str, list[str]] = {}
    for _s, _e, seg_text, idx in attribution.supports(gm):
        key = seg_text.strip().lstrip("*").split(":", 1)[0].strip().upper()
        ids = [by_chunk[i] for i in idx if i in by_chunk]
        line_sources.setdefault(key, [])
        line_sources[key] += [i for i in ids if i not in line_sources[key]]
    place_ids = [s["id"] for s in sources if s["place_id"]]
    route_ids = [s["id"] for s in sources if not s["place_id"]]

    def cite(*keys, fallback):
        ids = [i for k in keys for i in line_sources.get(k, [])]
        return list(dict.fromkeys(ids)) or fallback

    status = parsed["status"]
    if not sources:  # no Maps grounding came back: we cannot call anything a live check
        status = "unclear"
    record = shelter["wheelchair_accessible"]
    drive = drive_from(gm, parsed, from_geo_id) if include_route and from_geo_id else None

    name, ptype = parsed["maps_name"], parsed["place_type"]
    kind = f" ({ptype})" if ptype else ""
    status_text = {
        "confirmed": f"Google Maps lists {name or 'this building'} at this address{kind}.",
        "renamed": f"Same address, now listed on Google Maps as {name}{kind}." if name
                   else f"Google Maps lists this address under a different name{kind}.",
        "closed": "Google Maps lists this place as permanently closed.",
        "not_found": "Google Maps found nothing matching this building at this address.",
        "unclear": "Google Maps did not give a definite answer about this building.",
    }[status]
    summary = [{"text": status_text, "source_ids": cite("STATUS", "MAPS_NAME", fallback=place_ids[:1]),
                "freshness": "live"}]
    if parsed["note"] and sources:
        summary.append({"text": parsed["note"], "source_ids": cite("NOTE", fallback=place_ids[:1]),
                        "freshness": "live"})
    if sources and status not in ("not_found",):
        wc_text = {"yes": "Google Maps reports a wheelchair-accessible entrance.",
                   "no": "Google Maps reports no wheelchair-accessible entrance.",
                   "no_answer": "Google Maps has no wheelchair-entrance information for this place."}
        summary.append({"text": wc_text[parsed["wheelchair"]],
                        "source_ids": cite("WHEELCHAIR_ENTRANCE", fallback=place_ids[:1])
                        if parsed["wheelchair"] != "no_answer" else [],
                        "freshness": "live"})
    if parsed["open_now"] is not None and sources:
        summary.append({"text": ("Open now" if parsed["open_now"] else "Closed now")
                        + " by Google Maps business hours (not whether the county has opened it as a shelter).",
                        "source_ids": cite("OPEN_NOW", fallback=place_ids[:1]), "freshness": "live"})
    if drive:
        km = f", {drive['drive_km']} km" if drive["drive_km"] else ""
        summary.append({"text": f"About {round(drive['drive_minutes'])} minutes by car from tract "
                                f"{from_geo_id}{km}, by Google Maps routing.",
                        "source_ids": cite("DRIVE_MINUTES", fallback=route_ids[:1]), "freshness": "live"})

    return {
        "shelter_id": shelter["shelter_id"],
        "checked_at": checked_at,
        "status": status,
        "maps_name": name,
        "record_name": shelter["name"],
        "place_type": ptype,
        "open_now": parsed["open_now"] if sources else None,
        "wheelchair": {"maps": parsed["wheelchair"] if sources else "no_answer", "record": record,
                       "agreement": agreement(record, parsed["wheelchair"] if sources else "no_answer")},
        "drive": drive,
        "summary": summary,
        "sources": sources,
    }


def error_verification(shelter: dict, checked_at: str, message: str) -> dict:
    return {
        "shelter_id": shelter["shelter_id"], "checked_at": checked_at, "status": "error",
        "maps_name": None, "record_name": shelter["name"], "place_type": None, "open_now": None,
        "wheelchair": {"maps": "no_answer", "record": shelter["wheelchair_accessible"],
                       "agreement": agreement(shelter["wheelchair_accessible"], "no_answer")},
        "drive": None,
        "summary": [{"text": f"The Google Maps check failed ({message}); nothing was verified.",
                     "source_ids": [], "freshness": "live"}],
        "sources": [],
    }


# ---- the grounded call ------------------------------------------------------------------------


async def _generate(prompt: str, lat: float, lon: float, include_route: bool):
    maps_tool = types.Tool(google_maps=types.GoogleMaps(
        grounding_types=types.GoogleMapsGroundingTypes(
            places=types.GoogleMapsPlaces(),
            routing=types.GoogleMapsRouting() if include_route else None,
        )))
    cfg = types.GenerateContentConfig(
        tools=[maps_tool],
        tool_config=types.ToolConfig(retrieval_config=types.RetrievalConfig(
            lat_lng=types.LatLng(latitude=lat, longitude=lon))),
        temperature=0,
    )
    for attempt in range(2):
        try:
            return await asyncio.wait_for(
                _genai().aio.models.generate_content(model=config.MODEL, contents=prompt, config=cfg),
                timeout=TIMEOUT_S)
        except Exception as e:  # one retry with backoff on 429 / 503
            code = getattr(e, "code", None)
            if attempt == 0 and (code in (429, 503) or "RESOURCE_EXHAUSTED" in str(e)):
                await asyncio.sleep(RETRY_BACKOFF_S)
                continue
            raise


def _persist_place(shelter_id: str, sources: list[dict]) -> None:
    place = next((s["place_id"] for s in sources if s["place_id"]), None)
    if place:
        bq.run("10_merge_place.sql", shelter_id=shelter_id, place_id=place)


async def verify(shelter: dict, origin: dict | None, include_route: bool = True) -> dict:
    """Verify one shelter row (from sql/09_shelter_detail.sql). Never raises."""
    checked_at = now_iso()
    route = include_route and origin is not None
    try:
        async with _sem():
            resp = await _generate(build_prompt(shelter, origin if route else None),
                                   shelter["lat"], shelter["lon"], route)
        cand = (resp.candidates or [None])[0]
        gm = getattr(cand, "grounding_metadata", None)
        v = build_verification(shelter, resp.text, gm, origin["geo_id"] if route else None, route, checked_at)
    except TimeoutError:
        return error_verification(shelter, checked_at, "timed out")
    except Exception as e:
        log.exception("verify_shelter %s failed", shelter.get("shelter_id"))
        return error_verification(shelter, checked_at, type(e).__name__)
    if v["status"] in PERSIST_STATUSES:
        try:
            await asyncio.to_thread(_persist_place, shelter["shelter_id"], v["sources"])
        except Exception:
            log.exception("could not persist place_id for %s", shelter["shelter_id"])
    return v


def load_shelter(shelter_id: str) -> dict | None:
    rows = bq.run("09_shelter_detail.sql", shelter_id=str(shelter_id))
    return rows[0] if rows else None


def load_tract(geo_id: str | None) -> dict | None:
    if not geo_id:
        return None
    rows = bq.run("11_tract_point.sql", geo_id=geo_id)
    return rows[0] if rows else None


# ---- the ADK tool ----------------------------------------------------------------------------


async def verify_shelter(shelter_id: str, tool_context: ToolContext, from_geo_id: str | None = None,
                         include_route: bool = True) -> dict:
    """Check ONE shelter building on Google Maps right now: does it still exist at its address,
    was it renamed or permanently closed, what is it now, does Maps report a wheelchair-accessible
    entrance, and (if from_geo_id is given) the drive time by car from that tract's centroid.

    Use this for any "is it still there / what is it now / can a wheelchair user get in" question
    about a named shelter. Never use it for area questions. "Open now" means business hours only.

    Args:
        shelter_id: FEMA shelter_id of the building to check.
        from_geo_id: optional 11-digit census tract id to route from.
        include_route: ask Google Maps routing for a drive time (only with from_geo_id).

    Returns:
        A ShelterVerification: status (confirmed | renamed | closed | not_found | unclear | error),
        maps_name, record_name, place_type, open_now, wheelchair {maps, record, agreement},
        drive {from_geo_id, drive_minutes, drive_km} or null, summary segments with source ids,
        and Google Maps sources. All of it is live as of checked_at.
    """
    shelter = await asyncio.to_thread(load_shelter, shelter_id)
    if shelter is None:
        return {"status": "error", "shelter_id": shelter_id, "message": f"No shelter {shelter_id} on file."}
    origin = await asyncio.to_thread(load_tract, from_geo_id) if from_geo_id else None
    v = await verify(shelter, origin, include_route)
    facts.put(tool_context, "verifications", str(shelter_id), v)
    return v
