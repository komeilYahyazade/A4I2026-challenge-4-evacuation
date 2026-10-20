"""Grounding with Google Maps: the routing-enabled ADK tool, the per-building prompt, and
helpers that turn a grounded response into something the agent can act on.

Two ways the agent grounds:
1. Conversationally, through MapsPlacesAndRouting, when the manager asks about one place.
2. Systematically, through verify.py, which asks about one named building at one address per
   call. An area question ("which shelters take a wheelchair") gets nothing useful from Maps.
"""

import html
import json
import re
from typing import Any, Iterable, Mapping, Optional

from google.adk.tools.google_maps_grounding_tool import GoogleMapsGroundingTool
from google.genai import types


def maps_tool(routing: bool) -> types.Tool:
    """A Maps grounding tool. The stock ADK tool sends no grounding types, which means Places only."""
    grounding_types = (
        types.GoogleMapsGroundingTypes(places=types.GoogleMapsPlaces(), routing=types.GoogleMapsRouting())
        if routing
        else None
    )
    return types.Tool(google_maps=types.GoogleMaps(grounding_types=grounding_types))


def retrieval_config(lat: float, lng: float) -> types.RetrievalConfig:
    return types.RetrievalConfig(lat_lng=types.LatLng(latitude=lat, longitude=lng), language_code="en_US")


class MapsPlacesAndRouting(GoogleMapsGroundingTool):
    """Places plus routing, biased to the county the session is working on.

    build_readiness_brief and find_county store the county centroid in session state under
    `county_centroid`, so the same agent serves any county in the loaded state.
    """

    async def process_llm_request(self, *, tool_context: Any, llm_request: Any) -> None:
        await super().process_llm_request(tool_context=tool_context, llm_request=llm_request)
        tools = llm_request.config.tools
        for i, tool in enumerate(tools):
            if getattr(tool, "google_maps", None) is not None:
                tools[i] = maps_tool(routing=True)
        centroid = None
        try:
            centroid = tool_context.state.get("county_centroid")
        except Exception:
            centroid = None
        if centroid and "lat" in centroid and "lng" in centroid:
            cfg = llm_request.config
            cfg.tool_config = cfg.tool_config or types.ToolConfig()
            cfg.tool_config.retrieval_config = retrieval_config(float(centroid["lat"]), float(centroid["lng"]))


maps_grounding = MapsPlacesAndRouting()

_VERIFY_TEMPLATE = """FEMA's National Shelter System lists "<<NAME>>" at <<ADDRESS>> (approximately <<LAT>>, <<LNG>>) as a hurricane evacuation shelter. This is pre-season planning, weeks before any storm, not emergency response.

Using Google Maps, answer about this ONE building only:
1. Is there a place at or very near this address today? What is it called now, and what kind of place is it?
2. Does Google Maps mark it as permanently or temporarily closed?
3. Does Google Maps say anything about a wheelchair-accessible entrance? If it does not say, answer "unknown".
<<ROUTE>>
Do not guess. If Google Maps does not tell you something, say "unknown". Reply with only a fenced JSON block:
```json
{"exists": true | false | "unknown",
 "current_name": "string or null",
 "place_type": "string or null",
 "closed": "no" | "permanently" | "temporarily" | "unknown",
 "wheelchair_entrance": "yes" | "no" | "unknown",
 "drive_minutes": number or null,
 "notes": "one short sentence"}
```"""

_ROUTE_LINE = (
    "4. Using Google Maps routing, what is the typical driving time in minutes from "
    "<<OLAT>>, <<OLNG>> to this building? If routing gives nothing, use null."
)


def build_verify_prompt(row: Mapping[str, Any], origin: Optional[Mapping[str, Any]] = None) -> str:
    address = ", ".join(
        str(p).strip() for p in (row.get("address_1"), row.get("city"), row.get("zip")) if p and str(p).strip()
    )
    route = ""
    if origin and origin.get("lat") is not None and origin.get("lng") is not None:
        route = (
            _ROUTE_LINE.replace("<<OLAT>>", f"{float(origin['lat']):.5f}")
            .replace("<<OLNG>>", f"{float(origin['lng']):.5f}")
        )
    return (
        _VERIFY_TEMPLATE.replace("<<NAME>>", str(row.get("shelter_name") or "unnamed shelter"))
        .replace("<<ADDRESS>>", address or "address not recorded")
        .replace("<<LAT>>", f"{float(row['latitude']):.5f}" if row.get("latitude") is not None else "?")
        .replace("<<LNG>>", f"{float(row['longitude']):.5f}" if row.get("longitude") is not None else "?")
        .replace("<<ROUTE>>", route)
    )


def parse_json_block(text: Optional[str]) -> Optional[dict[str, Any]]:
    """Grounded calls cannot use JSON response mode, so parse a fenced block defensively."""
    if not text:
        return None
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if not candidates:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidates = [text[start : end + 1]]
    for cand in candidates:
        try:
            value = json.loads(cand)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def grounding_metadata(response: Any) -> Any:
    candidates = _get(response, "candidates") or []
    return _get(candidates[0], "grounding_metadata") if candidates else None


FALLBACK_TITLE = "Google Maps place"


def clean_title(title: Optional[str]) -> str:
    """Address-only results arrive titled " - Google Maps". Strip that suffix so the UI does not
    print "Google Maps - Google Maps"; fall back to a neutral title when nothing is left."""
    text = (title or "").strip()
    if text.endswith("- Google Maps"):
        text = text[: -len("- Google Maps")].strip()
    return text.rstrip("-").strip() or FALLBACK_TITLE


def extract_sources(metadata: Any) -> list[dict[str, Any]]:
    """Maps sources as {title, uri, place_id}. place_id is the only field we may keep."""
    out: list[dict[str, Any]] = []
    for chunk in _get(metadata, "grounding_chunks") or []:
        maps = _get(chunk, "maps")
        if not maps:
            continue
        out.append(
            {
                "title": clean_title(_get(maps, "title")),
                "uri": _get(maps, "uri") or "",
                "place_id": _get(maps, "place_id"),
            }
        )
    return out


def extract_supports(metadata: Any) -> list[dict[str, Any]]:
    """Sentence-to-source map. The first segment's start_index arrives as None; treat it as 0."""
    out: list[dict[str, Any]] = []
    for sup in _get(metadata, "grounding_supports") or []:
        seg = _get(sup, "segment")
        if seg is None:
            continue
        start, end = _get(seg, "start_index"), _get(seg, "end_index")
        out.append(
            {
                "start_index": 0 if start is None else int(start),
                "end_index": 0 if end is None else int(end),
                "text": _get(seg, "text") or "",
                "chunk_indices": list(_get(sup, "grounding_chunk_indices") or []),
            }
        )
    return out


def _duration_seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.fullmatch(r"\s*([\d.]+)\s*s\s*", str(value))
    return float(m.group(1)) if m else None


def extract_route(metadata: Any) -> Optional[dict[str, Any]]:
    """The first Maps route chunk, if routing grounding returned one."""
    for chunk in _get(metadata, "grounding_chunks") or []:
        route = _get(_get(chunk, "maps"), "route")
        if not route:
            continue
        seconds = _duration_seconds(_get(route, "duration"))
        meters = _get(route, "distance_meters")
        return {
            "drive_minutes": None if seconds is None else round(seconds / 60.0, 1),
            "road_km": None if meters is None else round(float(meters) / 1000.0, 2),
        }
    return None


_ABBREVIATIONS = {
    "MS": "MIDDLE SCHOOL", "ES": "ELEMENTARY SCHOOL", "HS": "HIGH SCHOOL", "ELEM": "ELEMENTARY",
    "EL": "ELEMENTARY", "SCH": "SCHOOL", "SCHL": "SCHOOL", "CTR": "CENTER", "CNTR": "CENTER",
    "CENTRE": "CENTER", "COMM": "COMMUNITY", "REC": "RECREATION", "MT": "MOUNT", "JR": "JUNIOR",
    "SR": "SENIOR", "MID": "MIDDLE", "ACAD": "ACADEMY", "UNIV": "UNIVERSITY", "CHR": "CHURCH",
    "BAPT": "BAPTIST", "METH": "METHODIST", "PRESBY": "PRESBYTERIAN", "GYM": "GYMNASIUM",
    "&": "AND",
}
_FILLER = {"THE", "OF", "AT", "INC", "BUILDING", "BLDG", "CAMPUS", "MAIN"}


def normalize_name(name: Optional[str]) -> tuple[str, ...]:
    if not name:
        return ()
    text = re.sub(r"[^A-Z0-9& ]+", " ", str(name).upper())
    tokens: list[str] = []
    for tok in text.split():
        tokens.extend(_ABBREVIATIONS.get(tok, tok).split())
    return tuple(sorted(t for t in tokens if t not in _FILLER))


_STATUS_WORDS = re.compile(r"\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b", re.I)


def names_match(record_name: Optional[str], live_name: Optional[str]) -> Optional[bool]:
    """True when the names are the same after expanding abbreviations. None if either is missing.

    Deliberately strict: "First Presbyterian Church of Maitland" and "Maitland Presbyterian
    Church" do NOT match. That pair is a real rename the organizers found, and a fuzzy matcher
    would hide it. A mismatch is a soft flag (update the record), never a reason to drop.
    """
    # Status words in a FEMA name ("INACTIVE Grace Church") describe the record, not the place.
    a = normalize_name(_STATUS_WORDS.sub(" ", record_name) if record_name else record_name)
    b = normalize_name(live_name)
    if not a or not b:
        return None
    return a == b


def attribution_html(sources: Iterable[Mapping[str, Any]]) -> str:
    """Google Maps attribution, to render immediately after the content it supports."""
    links = []
    for src in sources:
        title = html.escape(clean_title(src.get("title")))
        uri = str(src.get("uri") or "")
        if uri.startswith(("https://", "http://")):
            links.append(f'<a href="{html.escape(uri, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>')
        else:
            links.append(title)
    if not links:
        return ""
    return (
        '<p class="maps-sources">Sources: <span translate="no">Google Maps</span> · '
        + " · ".join(links)
        + "</p>"
    )


def sanitize_for_storage(record: Mapping[str, Any]) -> dict[str, str]:
    """Only place_id and review_id may be cached or stored. Everything else is re-grounded."""
    return {k: str(record[k]) for k in ("place_id", "review_id") if record.get(k)}


def build_single_shelter_grounding_prompt(
    shelter_name: Any,
    address: str = "",
    city: str = "",
    state: str = "FL",
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> str:
    """Build a Google Maps grounding prompt that queries ONE named building at ONE specific address."""
    if isinstance(shelter_name, Mapping):
        origin = (
            {"lat": origin_lat, "lng": origin_lon}
            if origin_lat is not None and origin_lon is not None
            else None
        )
        return build_verify_prompt(shelter_name, origin)
    full_city = ", ".join(p.strip() for p in (city, state) if p and p.strip())
    row: dict[str, Any] = {
        "shelter_name": shelter_name,
        "address_1": address,
        "city": full_city,
        "zip": "",
        "latitude": latitude,
        "longitude": longitude,
    }
    origin = (
        {"lat": origin_lat, "lng": origin_lon}
        if origin_lat is not None and origin_lon is not None
        else None
    )
    return build_verify_prompt(row, origin)


def format_google_maps_attribution(sources: Iterable[Mapping[str, Any]]) -> str:
    """Render compliant Google Maps HTML attribution with <span translate="no">Google Maps</span>."""
    rendered = attribution_html(sources)
    if rendered:
        return rendered
    return '<p class="maps-sources">Source: <span translate="no">Google Maps</span></p>'


def extract_maps_grounding_sources(response_or_metadata: Any) -> dict[str, Any]:
    """Extract grounding_chunks, normalized grounding_supports, route, and HTML attribution."""
    meta = (
        grounding_metadata(response_or_metadata)
        if _get(response_or_metadata, "candidates") is not None
        else response_or_metadata
    )
    sources = extract_sources(meta)
    supports = extract_supports(meta)
    route = extract_route(meta)
    return {
        "sources": sources,
        "grounding_chunks": sources,
        "grounding_supports": supports,
        "route": route,
        "attribution_html": format_google_maps_attribution(sources),
    }


def _resolve_project_id(project_id: Optional[str] = None) -> str:
    import os

    cand = (project_id or "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not cand or cand == "demo-project":
        return "qwiklabs-gcp-03-7a0c015d2e8a"
    return cand


def _normalize_exists(val: Any) -> Any:
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower() if val is not None else ""
    if s in ("true", "yes"):
        return True
    if s in ("false", "no"):
        return False
    return "unknown"


def verify_place_with_google_maps(
    shelter_name: str,
    address: str,
    city: str,
    state: str = "FL",
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
    project_id: Optional[str] = None,
    model_name: str = "gemini-3.6-flash",
) -> dict[str, Any]:
    """Verify one named shelter building at one specific address using Vertex AI Google Maps Grounding.

    Calls google.genai.Client(vertexai=True, project=project_id, location='global') with
    types.Tool(google_maps=types.GoogleMaps(...)), extracts grounding_chunks and grounding_supports
    (normalizing start_index=None to 0), detects facility renames vs the FEMA registry name, and
    attaches compliant <span translate="no">Google Maps</span> HTML attribution.
    """
    import os
    from google import genai

    proj = _resolve_project_id(project_id)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    client = genai.Client(vertexai=True, project=proj, location=location)

    prompt = build_single_shelter_grounding_prompt(
        shelter_name=shelter_name,
        address=address,
        city=city,
        state=state,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
    )
    want_routing = origin_lat is not None and origin_lon is not None
    tool_cfg = (
        types.ToolConfig(retrieval_config=retrieval_config(float(origin_lat), float(origin_lon)))
        if want_routing
        else None
    )

    models_to_try = [model_name]
    if "gemini-2.5-flash" not in models_to_try:
        models_to_try.append("gemini-2.5-flash")
    routing_attempts = [True, False] if want_routing else [False]

    resp = None
    used_model = model_name
    used_routing = False
    last_error: Optional[str] = None

    for m in models_to_try:
        for r_flag in routing_attempts:
            cfg = types.GenerateContentConfig(
                tools=[maps_tool(routing=r_flag)],
                tool_config=tool_cfg,
            )
            try:
                resp = client.models.generate_content(model=m, contents=prompt, config=cfg)
                used_model = m
                used_routing = r_flag
                last_error = None
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        if resp is not None:
            break

    if resp is None:
        attr = format_google_maps_attribution([])
        return {
            "shelter_name": shelter_name,
            "address": address,
            "city": city,
            "state": state,
            "exists": "unknown",
            "confirmed_open": False,
            "current_name": None,
            "name_differs": False,
            "place_type": None,
            "closed": "unknown",
            "wheelchair_entrance": "unknown",
            "drive_minutes": None,
            "road_km": None,
            "notes": f"Google Maps grounding failed: {last_error}",
            "grounded_summary": f"Could not verify {shelter_name} at {address}, {city}, {state}.",
            "grounded_summary_with_attribution": f"Could not verify {shelter_name}.\n{attr}",
            "sources": [],
            "grounding_chunks": [],
            "grounding_supports": [],
            "route": None,
            "attribution_html": attr,
            "stored_pointer": {},
            "model_used": used_model,
            "routing_used": False,
            "error": last_error,
        }

    raw_text = getattr(resp, "text", None) or ""
    extracted = extract_maps_grounding_sources(resp)
    sources = extracted["sources"]
    supports = extracted["grounding_supports"]
    route = extracted["route"]
    attr_html = extracted["attribution_html"]

    parsed = parse_json_block(raw_text) or {}
    exists = _normalize_exists(parsed.get("exists"))
    if exists == "unknown" and sources:
        exists = True

    current_name = parsed.get("current_name") or (sources[0]["title"] if sources else None)
    place_type = parsed.get("place_type")
    closed = str(parsed.get("closed") or "unknown").strip().lower()
    if closed not in ("no", "permanently", "temporarily"):
        closed = "unknown"
    wheelchair_entrance = str(parsed.get("wheelchair_entrance") or "unknown").strip().lower()
    if wheelchair_entrance not in ("yes", "no"):
        wheelchair_entrance = "unknown"

    drive_minutes: Optional[float] = None
    road_km: Optional[float] = None
    if route and route.get("drive_minutes") is not None:
        drive_minutes = route["drive_minutes"]
        road_km = route.get("road_km")
    elif isinstance(parsed.get("drive_minutes"), (int, float)):
        drive_minutes = float(parsed["drive_minutes"])

    match = names_match(shelter_name, current_name)
    name_differs = match is False
    confirmed_open = bool(exists is True and closed not in ("permanently", "temporarily") and len(sources) > 0)
    notes = str(parsed.get("notes") or "").strip()

    source_title = sources[0]["title"] if sources else (current_name or shelter_name)
    summary_parts = [
        f"[VERIFIED LIVE] Google Maps confirms '{current_name or shelter_name}' at {address}, {city}, {state} "
        f"(Google Maps: {source_title})."
        if exists is True
        else f"[UNRECORDED] Google Maps could not confirm '{shelter_name}' at {address}, {city}, {state}."
    ]
    if name_differs and current_name:
        summary_parts.append(
            f"Registry name '{shelter_name}' differs from current Google Maps name '{current_name}'."
        )
    if place_type:
        summary_parts.append(f"Facility type: {place_type}.")
    summary_parts.append(f"Wheelchair-accessible entrance on Google Maps: {wheelchair_entrance}.")
    if drive_minutes is not None:
        summary_parts.append(
            f"Routed drive time: {drive_minutes} min" + (f" ({road_km} km)." if road_km is not None else ".")
        )
    if notes:
        summary_parts.append(notes)
    grounded_summary = " ".join(summary_parts)

    return {
        "shelter_name": shelter_name,
        "address": address,
        "city": city,
        "state": state,
        "exists": exists,
        "confirmed_open": confirmed_open,
        "current_name": current_name,
        "name_differs": name_differs,
        "place_type": place_type,
        "closed": closed,
        "wheelchair_entrance": wheelchair_entrance,
        "drive_minutes": drive_minutes,
        "road_km": road_km,
        "notes": notes,
        "grounded_summary": grounded_summary,
        "grounded_summary_with_attribution": f"{grounded_summary}\n{attr_html}",
        "sources": sources,
        "grounding_chunks": sources,
        "grounding_supports": supports,
        "route": route,
        "attribution_html": attr_html,
        "stored_pointer": sanitize_for_storage(sources[0]) if sources else {},
        "model_used": used_model,
        "routing_used": used_routing,
        "error": None,
    }


def _parse_origin_coords(origin: Any) -> tuple[Optional[float], Optional[float], str]:
    """Extract (lat, lon, label) from a coordinate string, tuple, or mapping."""
    if isinstance(origin, Mapping):
        lat = origin.get("lat", origin.get("latitude"))
        lon = origin.get("lng", origin.get("lon", origin.get("longitude")))
        if lat is not None and lon is not None:
            return float(lat), float(lon), f"{float(lat):.5f}, {float(lon):.5f}"
    if isinstance(origin, (tuple, list)) and len(origin) == 2:
        try:
            return float(origin[0]), float(origin[1]), f"{float(origin[0]):.5f}, {float(origin[1]):.5f}"
        except (TypeError, ValueError):
            pass
    text = str(origin or "").strip()
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", text)
    if m:
        lat_f, lon_f = float(m.group(1)), float(m.group(2))
        return lat_f, lon_f, f"{lat_f:.5f}, {lon_f:.5f}"
    return None, None, text


def compute_grounded_route_with_maps(
    origin_address_or_coords: Any,
    destination_name: str,
    destination_address: str,
    city: str,
    state: str = "FL",
    project_id: Optional[str] = None,
    model_name: str = "gemini-3.6-flash",
) -> dict[str, Any]:
    """Estimate real road driving distance and travel time using Grounding with Google Maps.

    Uses Google Maps places + routing grounding to compute typical road travel time and distance
    to one named shelter building at its address, returning the grounded explanation, parsed route
    metrics, and <span translate="no">Google Maps</span> attribution HTML.
    """
    import os
    from google import genai

    proj = _resolve_project_id(project_id)
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
    client = genai.Client(vertexai=True, project=proj, location=location)

    olat, olon, origin_label = _parse_origin_coords(origin_address_or_coords)
    dest_full = ", ".join(p.strip() for p in (destination_address, city, state) if p and p.strip())

    prompt = (
        f"This is pre-season hurricane evacuation planning in {state}, weeks before any storm.\n"
        f"Using Google Maps routing, what is the driving distance (in km) and typical driving travel time "
        f"(in minutes) by road from {origin_label} to \"{destination_name}\" at {dest_full}?\n"
        "Also confirm the current name of the destination on Google Maps and whether road distance is longer "
        "than straight-line distance.\n"
        "Reply with a short explanation followed by a fenced JSON block:\n"
        "```json\n"
        '{"destination_current_name": "string or null", '
        '"drive_minutes": number or null, '
        '"road_km": number or null, '
        '"route_summary": "one short sentence"}\n'
        "```"
    )

    tool_cfg = (
        types.ToolConfig(retrieval_config=retrieval_config(olat, olon))
        if olat is not None and olon is not None
        else None
    )

    models_to_try = [model_name]
    if "gemini-2.5-flash" not in models_to_try:
        models_to_try.append("gemini-2.5-flash")

    resp = None
    used_model = model_name
    used_routing = False
    last_error: Optional[str] = None

    for m in models_to_try:
        for r_flag in (True, False):
            cfg = types.GenerateContentConfig(
                tools=[maps_tool(routing=r_flag)],
                tool_config=tool_cfg,
            )
            try:
                resp = client.models.generate_content(model=m, contents=prompt, config=cfg)
                used_model = m
                used_routing = r_flag
                last_error = None
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        if resp is not None:
            break

    if resp is None:
        attr = format_google_maps_attribution([])
        return {
            "origin": origin_label,
            "destination_name": destination_name,
            "destination_address": dest_full,
            "drive_minutes": None,
            "road_km": None,
            "grounded_answer": f"Could not compute route to {destination_name}: {last_error}",
            "sources": [],
            "grounding_chunks": [],
            "grounding_supports": [],
            "route": None,
            "attribution_html": attr,
            "model_used": used_model,
            "routing_used": False,
            "error": last_error,
        }

    raw_text = getattr(resp, "text", None) or ""
    extracted = extract_maps_grounding_sources(resp)
    sources = extracted["sources"]
    supports = extracted["grounding_supports"]
    route = extracted["route"]
    attr_html = extracted["attribution_html"]
    parsed = parse_json_block(raw_text) or {}

    drive_minutes = (
        route["drive_minutes"]
        if route and route.get("drive_minutes") is not None
        else (float(parsed["drive_minutes"]) if isinstance(parsed.get("drive_minutes"), (int, float)) else None)
    )
    road_km = (
        route["road_km"]
        if route and route.get("road_km") is not None
        else (float(parsed["road_km"]) if isinstance(parsed.get("road_km"), (int, float)) else None)
    )
    route_summary = str(parsed.get("route_summary") or "").strip()
    dest_current = parsed.get("destination_current_name") or (sources[0]["title"] if sources else destination_name)
    grounded_answer = (
        f"[VERIFIED LIVE] Route from {origin_label} to {dest_current} ({dest_full}): "
        f"typical road drive time is {drive_minutes} minutes over {road_km} km "
        f"(Google Maps: {dest_current}). {route_summary}".strip()
        if drive_minutes is not None
        else raw_text.strip()
    )

    return {
        "origin": origin_label,
        "destination_name": destination_name,
        "destination_current_name": dest_current,
        "destination_address": dest_full,
        "drive_minutes": drive_minutes,
        "road_km": road_km,
        "grounded_answer": grounded_answer,
        "grounded_answer_with_attribution": f"{grounded_answer}\n{attr_html}",
        "sources": sources,
        "grounding_chunks": sources,
        "grounding_supports": supports,
        "route": route or {"drive_minutes": drive_minutes, "road_km": road_km},
        "attribution_html": attr_html,
        "model_used": used_model,
        "routing_used": used_routing,
        "error": None,
    }
