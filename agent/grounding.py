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
