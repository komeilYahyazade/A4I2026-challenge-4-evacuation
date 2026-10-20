import asyncio

from google.adk.models.llm_request import LlmRequest
from google.genai import types

from agent.grounding import (
    attribution_html,
    clean_title,
    build_verify_prompt,
    extract_route,
    extract_sources,
    extract_supports,
    grounding_metadata,
    maps_grounding,
    names_match,
    parse_json_block,
    sanitize_for_storage,
)

ROW = {"shelter_name": "Lehigh Acres MS", "address_1": "104 Arthur Ave", "city": "Lehigh Acres",
       "zip": "33936", "latitude": 26.61, "longitude": -81.63}


def _response(text="", route=None, start_index=None):
    chunk = types.GroundingChunk(maps=types.GroundingChunkMaps(
        title="Lehigh Acres Middle School", uri="https://maps.google.com/?cid=1", place_id="places/abc", route=route))
    meta = types.GroundingMetadata(
        grounding_chunks=[chunk],
        grounding_supports=[types.GroundingSupport(
            segment=types.Segment(start_index=start_index, end_index=20, text="It is open."),
            grounding_chunk_indices=[0])],
    )
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]), grounding_metadata=meta)])


def test_verify_prompt_names_one_building_and_routes_only_with_origin():
    p = build_verify_prompt(ROW)
    assert "Lehigh Acres MS" in p and "104 Arthur Ave" in p
    assert "driving time" not in p
    assert "<<" not in p
    routed = build_verify_prompt(ROW, {"lat": 26.6, "lng": -81.8})
    assert "driving time" in routed and "26.60000" in routed


def test_sources_supports_and_route_from_real_sdk_objects():
    meta = grounding_metadata(_response(route=types.GroundingChunkMapsRoute(duration="840s", distance_meters=15100)))
    assert extract_sources(meta) == [
        {"title": "Lehigh Acres Middle School", "uri": "https://maps.google.com/?cid=1", "place_id": "places/abc"}]
    assert extract_supports(meta)[0]["start_index"] == 0  # None on the first segment
    assert extract_route(meta) == {"drive_minutes": 14.0, "road_km": 15.1}
    assert extract_route(grounding_metadata(_response())) is None


def test_parse_json_block_variants():
    assert parse_json_block('```json\n{"exists": true}\n```') == {"exists": True}
    assert parse_json_block('Here: {"exists": "unknown"} done') == {"exists": "unknown"}
    assert parse_json_block("no json here") is None
    assert parse_json_block(None) is None


def test_name_matching_expands_abbreviations_but_catches_real_renames():
    assert names_match("LEHIGH ACRES MS", "Lehigh Acres Middle School") is True
    assert names_match("Cypress Lake HS", "Cypress Lake High School") is True
    assert names_match("First Presbyterian Church of Maitland", "Maitland Presbyterian Church") is False
    assert names_match("Anything", None) is None
    assert names_match("INACTIVE Grace Church", "Grace Church") is True
    assert names_match("INACTIVE Grace Church", "Lehigh Acres Spanish Seventh-day Adventist Church") is False


def test_attribution_is_escaped_and_keeps_google_maps_untranslated():
    out = attribution_html([{"title": "<b>X</b>", "uri": "https://maps.google.com/?a=1&b=2"},
                            {"title": "Y", "uri": "javascript:alert(1)"}])
    assert '<span translate="no">Google Maps</span>' in out
    assert "&lt;b&gt;X&lt;/b&gt;" in out and "&amp;b=2" in out
    assert "javascript:" not in out


def test_only_place_and_review_ids_are_storable():
    assert sanitize_for_storage({"place_id": "places/abc", "title": "T", "uri": "u"}) == {"place_id": "places/abc"}


def test_adk_maps_tool_requests_routing_and_county_location():
    req = LlmRequest(model="gemini-3.6-flash", config=types.GenerateContentConfig())

    class Ctx:
        state = {"county_centroid": {"lat": 26.55, "lng": -81.95}}

    asyncio.run(maps_grounding.process_llm_request(tool_context=Ctx(), llm_request=req))
    maps = [t for t in req.config.tools if t.google_maps is not None]
    assert len(maps) == 1
    gt = maps[0].google_maps.grounding_types
    assert gt is not None and gt.places is not None and gt.routing is not None
    assert req.config.tool_config.retrieval_config.lat_lng.latitude == 26.55


def test_adk_maps_tool_without_county_context_still_routes():
    req = LlmRequest(model="gemini-3.6-flash", config=types.GenerateContentConfig())

    class Ctx:
        state = {}

    asyncio.run(maps_grounding.process_llm_request(tool_context=Ctx(), llm_request=req))
    assert req.config.tools[0].google_maps.grounding_types.routing is not None
    assert req.config.tool_config is None


def test_address_only_titles_do_not_double_google_maps():
    assert clean_title(" - Google Maps") == "Google Maps place"
    assert clean_title("Grace Church - Google Maps") == "Grace Church"
    assert clean_title(None) == "Google Maps place"
    assert clean_title("Lehigh Acres Middle School") == "Lehigh Acres Middle School"
    chunk = types.GroundingChunk(maps=types.GroundingChunkMaps(title=" - Google Maps", uri="https://maps.google.com/?cid=9"))
    meta = types.GroundingMetadata(grounding_chunks=[chunk])
    assert extract_sources(meta)[0]["title"] == "Google Maps place"
    assert "Google Maps - Google Maps" not in attribution_html([{"title": " - Google Maps", "uri": "https://x.test"}])
