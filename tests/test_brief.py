"""The brief, fed exactly the row shapes the SQL returns, plus real verification decisions."""

import json

import pytest

pytest.importorskip("sqlglot")

from helpers import CFG, columns_of, make_row  # noqa: E402

from agent.brief import _pick_to_verify, assemble_brief  # noqa: E402
from agent.reachability import ROUTED  # noqa: E402
from agent.verify import decide  # noqa: E402

TRACT_COLS = columns_of(lambda d: d.ranked_tracts(CFG, "12071", 5))
CAND_COLS = columns_of(lambda d: d.shelters_for_tract(CFG, "12071050100", 10))
SHELTER_COLS = columns_of(lambda d: d.county_shelters(CFG, "12071"))
CARE_COLS = columns_of(lambda d: d.care_facilities(CFG, "12071"))
POWER_COLS = columns_of(lambda d: d.power_dependence(CFG, "12071"))

GID = "12071050100"
COUNTY = {"county_fips": "12071", "county_name": "Lee", "lat": 26.55, "lng": -81.95}
SOURCES = [{"title": "Place", "uri": "https://maps.google.com/?cid=1", "place_id": "places/p"}]


def tract(**kw):
    base = dict(
        geo_id=GID, county_fips="12071", lat=26.6, lon=-81.8, total_pop=4100, households=1600,
        households_no_vehicle=420, share_no_vehicle=0.2625, pop_65_plus=1300, pct_age_65_plus=31.7,
        pct_disability=22.0, pct_mobile_homes=3.0, mobile_homes=50, pct_mobility=11.0, pct_lacktrpt=None,
        state_share_no_vehicle=0.06, state_pct_age_65_plus=21.0, state_pct_disability=13.5,
        state_pct_mobile_homes=8.0, km_any_shelter=3.2, km_pre_landfall_shelter=9.0,
        km_pre_landfall_wheelchair_recorded_yes=9.0, n_any_walkable=0, n_pre_landfall_in_run=3,
        n_in_run_wheelchair_yes=1, n_in_run_wheelchair_no=1, n_in_run_wheelchair_unrecorded=1,
        hurricane_exposed_pop=4100, coastal_flood_exposed_pop=900, priority_score=4.1)
    base.update(kw)
    return make_row(TRACT_COLS, **base)


def cand(sid, name, km, wc, surge="NO", **kw):
    base = dict(shelter_id=sid, shelter_name=name, address_1="1 Main St", city="Fort Myers", zip="33901",
                county_fips="12071", county_name="Lee", latitude=26.6, longitude=-81.9, usage="EVAC",
                is_pre_landfall=True, evacuation_capacity=500, post_impact_capacity=250, capacity_status="RECORDED",
                capacity_implausible=False,
                wheelchair_status=wc, generator_status="UNRECORDED", surge_status=surge, floodplain_status="UNRECORDED",
                is_medical=False, straight_line_km=km, origin_geo_id=GID, origin_lat=26.6, origin_lng=-81.8)
    base.update(kw)
    return make_row(CAND_COLS, **base)


def county_shelter(sid, **kw):
    base = dict(shelter_id=sid, shelter_name=f"S{sid}", usage="EVAC", is_pre_landfall=True, evacuation_capacity=400,
                capacity_status="RECORDED", capacity_implausible=False, latitude=26.5, longitude=-81.9, wheelchair_status="UNRECORDED", generator_status="UNRECORDED",
                surge_status="UNRECORDED", is_medical=False)
    base.update(kw)
    return make_row(SHELTER_COLS, **base)


def grounded(text_obj, route=None, sources=SOURCES):
    return {"text": "```json\n" + json.dumps(text_obj) + "\n```", "sources": sources, "route": route, "error": None}


def sample_brief():
    a = cand("A", "Alpha Elementary", 9.0, "YES")
    b = cand("B", "Bravo Middle School", 12.5, "NO", surge="UNRECORDED")
    c = cand("C", "Charlie High School", 14.2, "UNRECORDED")
    verifications = {
        "A": decide(a, grounded({"exists": False, "closed": "unknown"})),
        "B": decide(b, grounded({"exists": True, "current_name": "Bravo Middle School", "closed": "no",
                                 "wheelchair_entrance": "unknown"}, route={"drive_minutes": 18.0, "road_km": 15.0})),
    }
    shelters = [
        county_shelter("1", wheelchair_status="YES", generator_status="YES"),
        county_shelter("2", wheelchair_status="NO", surge_status="YES"),
        county_shelter("3", evacuation_capacity=270135, capacity_implausible=True),
        county_shelter("4", usage=None, is_pre_landfall=False),
        county_shelter("5", is_medical=True, generator_status="UNRECORDED"),
        county_shelter("6", evacuation_capacity=None, capacity_status="UNRECORDED"),
        county_shelter("7", shelter_name="INACTIVE Grace Church", name_says_inactive=True),
    ]
    hazard = {
        "7": {"shelter_id": "7", "coastal_flood_exposed_share": 1.0, "coastal_flood_annual_frequency": 3.197,
              "hurricane_annual_frequency": 0.4, "nearest_geo_id": GID, "km_to_centroid": 1.2,
              "method": "nearest 2020 tract centroid by ST_DISTANCE (approximate)"},
        "2": {"shelter_id": "2", "coastal_flood_exposed_share": 0.9, "coastal_flood_annual_frequency": 1.0},
        "1": {"shelter_id": "1", "coastal_flood_exposed_share": 0.1, "coastal_flood_annual_frequency": 0.2},
        "B": {"shelter_id": "B", "coastal_flood_exposed_share": 0.54, "coastal_flood_annual_frequency": 0.8,
              "nearest_geo_id": GID, "km_to_centroid": 0.7, "method": "nearest 2020 tract centroid by ST_DISTANCE (approximate)"},
    }
    power = make_row(POWER_COLS, county_fips="12071", power_dependent_dme=7381, power_dependent_is_censored=False)
    care = [make_row(CARE_COLS, facility_id="105", facility_name="Shady Palms", city="Fort Myers", certified_beds=120,
                     latitude=26.6, longitude=-81.9, km_to_generator_recorded_shelter=None,
                     km_to_medical_generator_recorded_shelter=None)]
    help_tracts = [
        tract(geo_id="12071080100", lat=26.4, lon=-81.8, households_no_vehicle=17, share_no_vehicle=0.01,
              mobile_homes=1450, pct_mobile_homes=61.0, pop_65_plus=2400, pct_age_65_plus=61.0,
              est_people_with_disability=900, ranking="may_need_help", priority_score=7.2, may_need_help_score=7.2),
        tract(geo_id="12071080200", mobile_homes=900, pop_65_plus=1500, est_people_with_disability=500),
        tract(geo_id="12071080300", mobile_homes=800, pop_65_plus=1400, est_people_with_disability=400),
        tract(geo_id="12071080400", mobile_homes=700, pop_65_plus=1300, est_people_with_disability=300),
    ]
    return assemble_brief(COUNTY, [tract()], shelters, {GID: [a, b, c]}, verifications, power, care,
                          ledger={"maps_calls": 2, "definite": 2, "unknown": 0},
                          generated_at="2026-08-18T14:02:00+00:00", site_hazard=hazard, help_tracts=help_tracts)


@pytest.fixture
def brief():
    return sample_brief()


def test_payload_is_tagged_and_maps_sources_are_collected(brief):
    assert brief["payload_type"] == "readiness_brief.v1" and brief["schema_version"] == 1
    assert brief["maps_sources"] == [{**SOURCES[0], "supports_shelter_ids": ["A", "B"]}]


def test_four_segments_present(brief):
    for key in ("who_cannot_leave", "where_they_would_go", "fix_before_season", "could_not_tell_you"):
        assert brief[key]


def test_tract_is_not_called_walkable_and_dropped_shelter_is_skipped(brief):
    m = brief["who_cannot_leave"][0]["mobility"]
    assert m["status"] == "PICKUP_NEEDED"
    assert m["straight_line_km"] == 12.5  # A was dropped by Google Maps, so B is the nearest real option
    assert m["distance_basis"] == ROUTED and m["drive_minutes"] == 18.0


def test_recorded_flags_survive_and_unrecorded_is_not_no(brief):
    where = {w["shelter_id"]: w for w in brief["where_they_would_go"]}
    assert where["A"]["record"]["wheelchair"] == "YES"
    assert where["B"]["record"]["wheelchair"] == "NO"
    assert where["C"]["record"]["wheelchair"] == "UNRECORDED"
    assert where["B"]["record"]["surge"] == "UNRECORDED"
    assert where["A"]["verdict"] == "DROP"
    assert where["C"]["verdict"] == "NOT_VERIFIED_THIS_RUN"


def test_fix_list_covers_the_gaps_in_severity_order(brief):
    types = [f["type"] for f in brief["fix_before_season"]]
    for expected in ("NOT_FOUND", "PRE_LANDFALL_IN_SURGE_ZONE", "NO_WALKABLE_DESTINATION", "GENERATOR_GAP",
                     "CARE_FACILITY_FAR_FROM_GENERATOR", "CAPACITY_IMPLAUSIBLE"):
        assert expected in types, expected
    assert types.index("NOT_FOUND") < types.index("CAPACITY_IMPLAUSIBLE")
    gen = next(f for f in brief["fix_before_season"] if f["type"] == "GENERATOR_GAP")
    assert gen["label"] == "COUNTY-LEVEL"


def test_capacity_totals_exclude_implausible_and_count_unrecorded(brief):
    s = brief["county_shelters"]
    # Shelters 1, 2, 5 count. Not 3 (implausible), 4 (no usage code) or 6 (capacity unrecorded, not zero).
    # 7 is excluded too: its own name says INACTIVE. Its spaces are reported separately.
    assert s["pre_landfall_capacity_plausible"] == 400 * 3
    assert s["pre_landfall_capacity_recorded"] == 3 and s["pre_landfall_capacity_unrecorded"] == 1
    assert s["pre_landfall_name_says_inactive"] == 1 and s["pre_landfall_name_says_inactive_spaces"] == 400
    assert s["usage_unrecorded"] == 1
    assert s["pre_landfall_wheelchair"] == {"yes": 1, "no": 1, "unrecorded": 4}
    wc = brief["could_not_tell_you"][0]
    assert wc["note"] == "2 I can speak to; 4 nobody has assessed."
    cap = next(c for c in brief["could_not_tell_you"] if c["item"] == "Capacity of pre-landfall shelters")
    assert cap["unassessed"] == 1 and "not zero spaces" in cap["note"]
    surge = next(c for c in brief["could_not_tell_you"] if c["item"].startswith("Surge-zone"))
    assert "not known to be outside the surge zone" in surge["note"]


def test_missing_transport_measure_is_named(brief):
    assert any(c["item"] == "Lack of reliable transportation" for c in brief["could_not_tell_you"])


def test_reasons_carry_labels(brief):
    reasons = brief["who_cannot_leave"][0]["reasons"]
    labels = {r["label"] for r in reasons}
    assert {"RECORDED", "MODELED"} <= labels
    assert any("420 households without a vehicle" in r["text"] for r in reasons)


def test_verification_picks_skip_surge_and_dedupe():
    a = cand("A", "A", 1.0, "YES", surge="YES")
    b = cand("B", "B", 2.0, "NO")
    c = cand("C", "C", 3.0, "UNRECORDED")
    picked = _pick_to_verify({"t1": [a, b, c], "t2": [b, c]}, limit=8)
    assert list(picked) == ["B", "C"]
    assert picked["B"]["origin"] == {"lat": 26.6, "lng": -81.8}
    grace = county_shelter("345200", shelter_name="INACTIVE Grace Church", name_says_inactive=True)
    first = _pick_to_verify({"t1": [a, b, c]}, limit=2, priority=[county_shelter("1"), grace])
    assert list(first) == ["345200", "B"] and first["345200"]["origin"] is None


def test_coordinates_for_the_map(brief):
    assert brief["county"]["lat"] == 26.55 and brief["county"]["lng"] == -81.95
    assert brief["who_cannot_leave"][0]["lat"] == 26.6 and brief["who_cannot_leave"][0]["lng"] == -81.8
    for w in brief["where_they_would_go"]:
        assert w["lat"] == 26.6 and w["lng"] == -81.9
    flags = {w["shelter_id"]: w["flags"] for w in brief["where_they_would_go"]}
    assert flags["A"] == ["NOT_FOUND"] and flags["C"] == []


def test_every_fix_has_severity_and_a_ref(brief):
    for f in brief["fix_before_season"]:
        assert f["severity"] in ("HIGH", "MEDIUM", "LOW"), f
        assert f["ref"]["kind"] in ("tract", "shelter", "facility", "county") and f["ref"]["id"], f
    by_type = {f["type"]: f for f in brief["fix_before_season"]}
    assert by_type["NO_WALKABLE_DESTINATION"]["ref"] == {"kind": "tract", "id": GID, "lat": 26.6, "lng": -81.8}
    assert by_type["GENERATOR_GAP"]["ref"]["kind"] == "county"
    assert by_type["CARE_FACILITY_FAR_FROM_GENERATOR"]["ref"]["kind"] == "facility"
    assert by_type["NOT_FOUND"]["ref"] == {"kind": "shelter", "id": "A", "lat": 26.6, "lng": -81.9}
    assert by_type["NOT_FOUND"]["severity"] == "HIGH" and by_type["CAPACITY_IMPLAUSIBLE"]["severity"] == "MEDIUM"


def test_unrecorded_capacity_is_not_shown_as_zero():
    c = cand("Z", "Zulu School", 3.0, "UNRECORDED", evacuation_capacity=0, capacity_status="UNRECORDED")
    out = assemble_brief(COUNTY, [tract()], [], {GID: [c]}, {}, None, [])
    row = out["where_they_would_go"][0]["record"]
    assert row["capacity"] is None and row["capacity_status"] == "UNRECORDED" and row["capacity_as_filed"] == 0


def test_flood_exposure_is_flagged_as_approximate_and_skips_recorded_surge(brief):
    flood = [f for f in brief["fix_before_season"] if f["type"] == "PRE_LANDFALL_FLOOD_EXPOSED_SITE"]
    subjects = {f["ref"]["id"] for f in flood}
    assert subjects == {"7"}  # 2 is recorded in the surge zone already; 1 is below the threshold
    assert flood[0]["label"] == "APPROXIMATE" and flood[0]["severity"] == "MEDIUM"
    assert "100%" in flood[0]["evidence"] and "not building" in flood[0]["evidence"]
    assert brief["county_shelters"]["pre_landfall_flood_exposed_approx"] == 2
    item = next(c for c in brief["could_not_tell_you"] if c["item"] == "Site flood exposure")
    assert item["label"] == "APPROXIMATE" and "neighbourhood, not the building" in item["note"]


def test_site_hazard_rides_on_destination_rows(brief):
    where = {w["shelter_id"]: w for w in brief["where_they_would_go"]}
    sh = where["B"]["record"]["site_hazard"]
    assert sh["label"] == "APPROXIMATE" and sh["coastal_flood_exposed_share"] == 0.54
    assert where["C"]["record"]["site_hazard"] is None


def test_name_says_inactive_is_flagged_and_never_the_nearest_destination(brief):
    inactive = [f for f in brief["fix_before_season"] if f["type"] == "NAME_SAYS_INACTIVE"]
    assert len(inactive) == 1 and inactive[0]["severity"] == "HIGH" and inactive[0]["label"] == "RECORDED"
    assert "INACTIVE Grace Church" in inactive[0]["evidence"]
    a = cand("X", "INACTIVE Old Gym", 2.0, "YES", name_says_inactive=True)
    b = cand("Y", "Yankee Elementary", 5.0, "UNRECORDED")
    out = assemble_brief(COUNTY, [tract()], [], {GID: [a, b]}, {}, None, [])
    assert out["who_cannot_leave"][0]["mobility"]["straight_line_km"] == 5.0
    assert out["where_they_would_go"][0]["record"]["name_says_inactive"] is True


def test_missing_site_hazard_view_is_named_not_hidden():
    out = assemble_brief(COUNTY, [tract()], [], {}, {}, None, [], site_hazard=None)
    item = next(c for c in out["could_not_tell_you"] if c["item"] == "Site flood exposure")
    assert "Unavailable" in item["note"]
    assert out["county_shelters"]["pre_landfall_flood_exposed_approx"] is None


def test_two_lists_each_led_by_their_own_need(brief):
    who = brief["who_cannot_leave"][0]
    assert who["reasons"][0]["text"].startswith("420 households without a vehicle")
    helpers = brief["must_leave_may_need_help"]
    assert [h["rank"] for h in helpers] == [1, 2, 3, 4]
    first = helpers[0]
    assert first["lat"] == 26.4 and first["people"]["mobile_homes"] == 1450
    texts = [r["text"] for r in first["reasons"]]
    assert texts[0].startswith("1450 mobile homes") and texts[1].startswith("2400 residents aged 65+")
    assert texts[2].startswith("About 900 residents with a disability")
    assert all(r["label"] == "RECORDED" for r in first["reasons"])


def test_outreach_fixes_for_the_top_three_must_leave_tracts(brief):
    outreach = [f for f in brief["fix_before_season"] if f["type"] == "OUTREACH_FOR_MUST_LEAVE"]
    assert [f["ref"]["id"] for f in outreach] == ["12071080100", "12071080200", "12071080300"]
    assert outreach[0]["severity"] == "MEDIUM" and "1450 mobile homes" in outreach[0]["evidence"]


def test_ranking_change_is_recorded_for_the_audit_story(brief):
    a = brief["assumptions"]
    assert set(a["rankings"]) == {"who_cannot_leave", "must_leave_may_need_help"}
    assert "14%" in a["ranking_change"] and "34%" in a["ranking_change"] and "composite_v0" in a["ranking_change"]
