"""Allocator and Ian rehearsal: pure functions on a synthetic fixture."""

from contract import schemas
from readiness_agent import plan


def _tract(g, persons, lat=26.6, lon=-81.85):
    return {"geo_id": g, "lat": lat, "lon": lon, "households_no_vehicle": persons // 2, "persons_per_household": 2.0,
            "est_persons_no_vehicle": persons}


def _edge(g, sid, km, cap, name=None, surge="no", lat=26.62, lon=-81.86):
    return {"geo_id": g, "shelter_id": sid, "shelter_name": name or f"Shelter {sid}", "shelter_county_fips": "12071",
            "shelter_lat": lat, "shelter_lng": lon, "straight_line_km": km, "evacuation_capacity": cap,
            "capacity_implausible": False, "wheelchair_accessible": "unrecorded", "generator_onsite": "unrecorded",
            "in_surge_slosh_area": surge, "is_medical": False}


TRACTS = [_tract("A", 1000), _tract("B", 400, 26.7, -81.95), _tract("C", 300, 27.2, -81.5)]
EDGES = [_edge("A", "S1", 1.0, 600), _edge("A", "S2", 4.0, 300), _edge("B", "S2", 9.0, 300),
         _edge("B", "S3", 3.0, None), _edge("B", "S4", 2.0, 500, "Old Hall - INACTIVE")]


def test_capacity_is_respected_and_nearest_first():
    a = plan.solve(TRACTS, EDGES, county_fips="12071")
    schemas.Allocation.model_validate(a)
    load = {s["shelter_id"]: s["assigned_persons"] for s in a["shelters"]}
    assert load["S1"] == 600 and load["S2"] == 300  # both full; nobody is sent beyond capacity
    assert a["totals"] == {"demand_persons": 1700, "assigned_persons": 900, "unassigned_persons": 800,
                           "usable_capacity": 900, "shelters_used": 2}


def test_unrecorded_capacity_is_never_assumed_and_inactive_names_are_dropped():
    a = plan.solve(TRACTS, EDGES)
    reasons = {s["shelter_id"]: s["excluded_reason"] for s in a["shelters"]}
    assert reasons["S3"] == "CAPACITY_UNRECORDED" and reasons["S4"] == "NAME_SAYS_INACTIVE"
    assert a["excluded_counts"] == {"CAPACITY_UNRECORDED": 1, "NAME_SAYS_INACTIVE": 1}


def test_unplaced_reasons_split_full_from_out_of_reach():
    a = plan.solve(TRACTS, EDGES)
    why = {u["geo_id"]: u["reason"] for u in a["unassigned"]}
    assert why["C"] == "NO_SHELTER_WITHIN_MAX_KM" and why["A"] == "CAPACITY_EXHAUSTED"


def test_duplicate_records_of_one_building_count_once():
    dup = _edge("A", "S1b", 1.0, 600, "Shelter S1")
    a = plan.solve(TRACTS, EDGES + [dup])
    assert {s["shelter_id"]: s["excluded_reason"] for s in a["shelters"]}["S1b"] == "DUPLICATE_RECORD"


def test_site_options_are_greedy_and_do_not_double_count():
    un = [{"geo_id": "X", "persons": 500, "lat": 26.6, "lon": -81.8}, {"geo_id": "Y", "persons": 300, "lat": 26.61, "lon": -81.81},
          {"geo_id": "Z", "persons": 200, "lat": 27.5, "lon": -81.0}]
    opts = plan.site_options(un, 15.0)
    assert [(o["persons_covered"], o["tracts_covered"]) for o in opts] == [(800, ["X", "Y"]), (200, ["Z"])]


def test_pick_down_counts_only_in_plan_shelters_toward_n():
    a = plan.solve(TRACTS, EDGES)
    shelters = {s["shelter_id"]: s for s in a["shelters"]}
    rows = [{"shelter_id": "S3", "name": "S3", "reason": "IN_SURGE_ZONE", "down_rank": 1, "flood_share": 0.1},
            {"shelter_id": "S1", "name": "S1", "reason": "IN_CONE_AND_FLOOD_EXPOSED", "down_rank": 2, "flood_share": 0.6},
            {"shelter_id": "S2", "name": "S2", "reason": "IN_CONE_AND_FLOOD_EXPOSED", "down_rank": 3, "flood_share": 0.7},
            {"shelter_id": "S9", "name": "S9", "reason": "IN_CONE_AND_FLOOD_EXPOSED", "down_rank": 4, "flood_share": 0.9}]
    picked = plan.pick_down(shelters, rows, n=2)
    assert [(p["shelter_id"], p["in_plan"]) for p in picked] == [("S3", False), ("S1", True), ("S2", True)]
    assert picked[0]["evidence"].startswith("RECORDED") and picked[1]["evidence"].startswith("APPROXIMATE: inside the cone, and 60%")


def test_rehearsal_diff_moves_and_strands_people():
    before = plan.solve(TRACTS, EDGES)
    after = plan.solve(TRACTS, EDGES, shelters_down=["S1"])
    d = plan.diff(before, after)
    assert after["totals"]["unassigned_persons"] - before["totals"]["unassigned_persons"] == 600
    assert sum(u["persons"] for u in d["newly_unassigned"]) == 600
    assert {s["shelter_id"]: s["excluded_reason"] for s in after["shelters"]}["S1"] == "DOWN"
