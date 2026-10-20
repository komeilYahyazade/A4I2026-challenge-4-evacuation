"""verify_shelter's pure parts: parser, agreement matrix, drive time, attribution."""

from types import SimpleNamespace as NS

import pytest

from contract import schemas
from readiness_agent import attribution
from readiness_agent.tools import verify as V

GOOD = """STATUS: renamed
MAPS_NAME: Caloosa Sound Convention Center
PLACE_TYPE: convention center
OPEN_NOW: yes
WHEELCHAIR_ENTRANCE: yes
DRIVE_MINUTES: 16
NOTE: Renovated and renamed."""

SHELTER = {"shelter_id": "70851", "name": "Harborside Events Center Special Needs",
           "address_1": "1375 Monroe St.", "city": "Fort Myers", "zip": "33901",
           "lat": 26.644, "lon": -81.872, "wheelchair_accessible": "unrecorded"}


def _gm(with_route=True, supports=True):
    chunks = [NS(maps=NS(place_id="ChIJ_1", title="Caloosa Sound Convention Center - Google Maps",
                         uri="https://maps.google.com/?cid=1", route=None))]
    if with_route:
        chunks.append(NS(maps=NS(place_id=None, title="(a) to (b) - Google Maps", uri="https://maps/dir",
                                 route=NS(duration="978s", distance_meters=8159))))
    sups = []
    if supports:
        offset = 0
        for i, line in enumerate(GOOD.splitlines()):
            idx = [1] if line.startswith("DRIVE") and with_route else [0]
            sups.append(NS(segment=NS(start_index=None if i == 0 else offset, end_index=offset + len(line),
                                      text=line), grounding_chunk_indices=idx))
            offset += len(line) + 1
    return NS(grounding_chunks=chunks, grounding_supports=sups)


@pytest.mark.parametrize("status", V.STATUSES)
def test_parse_each_status(status):
    assert V.parse(GOOD.replace("renamed", status))["status"] == status


@pytest.mark.parametrize("value,expected", [("yes", True), ("no", False), ("unknown", None)])
def test_parse_open_now(value, expected):
    assert V.parse(GOOD.replace("OPEN_NOW: yes", f"OPEN_NOW: {value}"))["open_now"] is expected


@pytest.mark.parametrize("value", V.WHEELCHAIR)
def test_parse_wheelchair(value):
    assert V.parse(GOOD.replace("WHEELCHAIR_ENTRANCE: yes", f"WHEELCHAIR_ENTRANCE: {value}"))["wheelchair"] == value


@pytest.mark.parametrize("text", [
    None, "", "I could not find it.", GOOD.replace("STATUS: renamed", "STATUS: maybe"),
    GOOD.replace("OPEN_NOW: yes\n", ""), GOOD.replace("WHEELCHAIR_ENTRANCE: yes", "WHEELCHAIR_ENTRANCE: probably"),
])
def test_malformed_is_unclear(text):
    assert V.parse(text)["status"] == "unclear"


def test_parse_tolerates_markdown_and_none():
    p = V.parse(GOOD.replace("STATUS: renamed", "**STATUS:** renamed").replace(
        "MAPS_NAME: Caloosa Sound Convention Center", "MAPS_NAME: none").replace("DRIVE_MINUTES: 16", "DRIVE_MINUTES: about 16.5"))
    assert p["status"] == "renamed" and p["maps_name"] is None and p["drive_minutes"] == 16.5


@pytest.mark.parametrize("record,maps,expected", [
    ("yes", "yes", "agree"), ("no", "no", "agree"), ("yes", "no", "disagree"), ("no", "yes", "disagree"),
    ("unrecorded", "yes", "record_blank"), ("unrecorded", "no", "record_blank"),
    ("yes", "no_answer", "maps_blank"), ("no", "no_answer", "maps_blank"),
    ("unrecorded", "no_answer", "both_blank"),
])
def test_agreement_matrix(record, maps, expected):
    assert V.agreement(record, maps) == expected


def test_drive_prefers_route_chunk():
    d = V.drive_from(_gm(), V.parse(GOOD), "12071020801")
    assert d == {"from_geo_id": "12071020801", "drive_minutes": 16.3, "drive_km": 8.2}


def test_drive_falls_back_to_parsed_then_none():
    assert V.drive_from(_gm(with_route=False), V.parse(GOOD), "g")["drive_minutes"] == 16.0
    assert V.drive_from(_gm(with_route=False), V.parse(GOOD.replace("16", "none")), "g") is None


def test_build_verification_validates_and_cites():
    v = V.build_verification(SHELTER, GOOD, _gm(), "12071020801", True, V.now_iso())
    schemas.ShelterVerification.model_validate(v)
    assert v["status"] == "renamed" and v["wheelchair"]["agreement"] == "record_blank"
    ids = {s["id"] for s in v["sources"]}
    assert ids == {"src_1", "src_2"}
    assert all(set(seg["source_ids"]) <= ids for seg in v["summary"])
    drive_seg = [s for s in v["summary"] if "by car" in s["text"]][0]
    assert drive_seg["source_ids"] == ["src_2"]
    assert v["sources"][0]["title"] == "Caloosa Sound Convention Center"


def test_no_maps_sources_is_never_a_live_confirmation():
    v = V.build_verification(SHELTER, GOOD, NS(grounding_chunks=[], grounding_supports=[]), None, False, V.now_iso())
    assert v["status"] == "unclear" and v["wheelchair"]["maps"] == "no_answer" and v["sources"] == []


def test_without_supports_falls_back_to_place_chunk():
    v = V.build_verification(SHELTER, GOOD, _gm(supports=False), "g", True, V.now_iso())
    assert v["summary"][0]["source_ids"] == ["src_1"]


def test_error_verification_validates():
    schemas.ShelterVerification.model_validate(V.error_verification(SHELTER, V.now_iso(), "timed out"))


def test_attribution_start_index_none_and_unique_ids():
    text = "First part. Second part."
    gm = NS(grounding_chunks=[NS(maps=NS(place_id="p1", title="A", uri="u1")), NS(web=None, maps=None),
                              NS(maps=NS(place_id="p2", title="B", uri="u2"))],
            grounding_supports=[NS(segment=NS(start_index=None, end_index=11, text="First part."),
                                   grounding_chunk_indices=[0]),
                                NS(segment=NS(start_index=12, end_index=24, text="Second part."),
                                   grounding_chunk_indices=[0, 2])])
    segs, sources = attribution.segments_from_response(text, gm)
    assert [s["id"] for s in sources] == ["src_1", "src_2"]
    assert segs[0] == {"text": "First part.", "source_ids": ["src_1"], "freshness": "live"}
    assert segs[1]["source_ids"] == ["src_1", "src_2"]


def test_renumber_makes_ids_unique():
    a = {"sources": [{"id": "src_1"}], "summary": [{"source_ids": ["src_1"]}]}
    b = {"sources": [{"id": "src_1"}, {"id": "src_2"}], "summary": [{"source_ids": ["src_2"]}]}
    srcs, nxt = attribution.renumber([a, b])
    assert [s["id"] for s in srcs] == ["src_1", "src_2", "src_3"] and nxt == 4
    assert b["summary"][0]["source_ids"] == ["src_3"]


def test_route_chunk_title_becomes_readable():
    gm = NS(grounding_chunks=[NS(maps=NS(place_id=None, title="(26.646241,-81.863837) to (26.645752,-81.866105) - Google Maps", uri="u"))])
    sources, _ = attribution.sources_from_metadata(gm)
    assert sources[0]["title"] == "Driving directions"


def test_digest_writes_whole_numbers_as_ints():
    from readiness_agent.tools import facts
    d = facts.digest({"tracts": {"12071": {"tracts": [{"geo_id": "12071020801", "households_no_vehicle": 401.0, "pct_no_vehicle": 22.6}]}}})
    assert '"households_no_vehicle":401,' in d and '"pct_no_vehicle":22.6' in d
