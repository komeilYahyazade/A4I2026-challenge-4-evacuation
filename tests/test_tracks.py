"""Tracks change what the brief looks at: analyst steps, who is listed, and the seat gap."""

import copy

from readiness_agent import brief_plan, prompts
from readiness_agent.tools.gaps import compute_gaps
from tests.test_briefs import FACTS


class Ctx:
    def __init__(self, state):
        self.state = state


def _instr(track):
    return prompts.analyst_instruction(Ctx({"track": track, "county_fips": "12071", "reachable_km": 3.0,
                                            "max_shelters_to_verify": 12, "include_routes": True}))


def test_analyst_steps_differ_by_track():
    sr, gpo, med = _instr("shelter_readiness"), _instr("getting_people_out"), _instr("medically_dependent")
    assert 'rank_by "need_score"' in sr and 'rank_by "households_no_vehicle"' in gpo
    assert "ccn = that facility" in med and 'require "medical_or_generator"' in med and "ccn:" in med
    assert len({sr, gpo, med}) == 3
    assert "{" not in sr + gpo + med  # str.format consumed every placeholder


def test_unknown_track_falls_back_to_shelter_readiness():
    assert _instr("nonsense") == _instr("shelter_readiness")


def test_who_order_and_counts_follow_the_track():
    f = copy.deepcopy(FACTS)
    sr = brief_plan.plan(f, "shelter_readiness")["who"]
    med = brief_plan.plan(f, "medically_dependent")["who"]
    gpo = brief_plan.plan(f, "getting_people_out")["who"]
    assert sr[0]["kind"] == "tract" and med[0]["kind"] == "care_facility"
    assert all(w["kind"] == "tract" for w in gpo)  # rides, not facilities


def test_nursing_home_origins_serve_destinations_and_gaps():
    f = copy.deepcopy(FACTS)
    f["candidates"]["ccn:105966"] = {"geo_id": None, "ccn": "105966", "shelters": [
        {**f["candidates"]["12071000100"]["shelters"][2], "within_reach": True}]}  # shelter C, surge yes
    gaps = compute_gaps(f)
    surge = [g for g in gaps if g["category"] == "shelter_in_surge_zone"][0]
    assert surge["affected"]["care_facilities"] == ["105966"] and "ccn:105966" not in surge["affected"]["tracts"]


def test_seat_gap_comes_from_the_allocation():
    f = copy.deepcopy(FACTS)
    f["allocation"] = {"12071": {"max_km": 15.0, "totals": {"demand_persons": 36832, "unassigned_persons": 5592},
                                 "unassigned": [{"geo_id": "12071001502", "persons": 3000}], "unassigned_tracts": 46,
                                 "site_options": [{"geo_id": "12071001502", "persons_covered": 5198}]}}
    seat = [g for g in compute_gaps(f) if g["category"] == "seat_gap"][0]
    assert seat["metrics"]["unassigned_persons"] == 5592 and seat["metrics"]["best_site_persons"] == 5198
    assert seat["affected"]["tracts"] == ["12071001502"]
