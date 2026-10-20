"""Gap rules and assemble_brief on a synthetic fixture (never real Maps content: rule 10)."""

import copy

import pytest

from contract import schemas
from readiness_agent import brief_plan
from readiness_agent.tools.gaps import compute_gaps


def _shelter(sid, km, wc="unrecorded", within=True, usage="EVAC", surge="no", flood="no", gen="unrecorded"):
    return {"shelter_id": sid, "name": f"Shelter {sid}", "address": "1 Example St, Fort Myers, FL 33901",
            "address_1": "1 Example St", "city": "Fort Myers", "zip": "33901", "county_fips": "12071",
            "lat": 26.6, "lon": -81.8, "nearest_geo_id": "12071000100", "usage": usage,
            "evacuation_capacity": 200, "post_impact_capacity": None, "wheelchair_accessible": wc,
            "generator_onsite": gen, "self_sufficient_electricity": "unrecorded", "in_surge_slosh_area": surge,
            "in_100_yr_floodplain": flood, "is_medical": False, "capacity_implausible": False, "place_id": None,
            "straight_line_km": km, "within_reach": within}


def _verif(sid, status, maps_wc="yes", record="unrecorded", agreement="record_blank"):
    return {"shelter_id": sid, "checked_at": "2026-10-20T15:41:02Z", "status": status,
            "maps_name": f"Maps name {sid}", "record_name": f"Shelter {sid}", "place_type": "school",
            "open_now": None, "wheelchair": {"maps": maps_wc, "record": record, "agreement": agreement},
            "drive": {"from_geo_id": "12071000100", "drive_minutes": 4.1, "drive_km": 1.7},
            "summary": [{"text": "Live claim.", "source_ids": ["src_1"], "freshness": "live"}],
            "sources": [{"id": "src_1", "provider": "Google Maps", "title": "T", "uri": "u", "place_id": f"P{sid}"},
                        {"id": "src_2", "provider": "Google Maps", "title": "route", "uri": "u2", "place_id": None}]}


TRACTS = [
    {"geo_id": "12071000100", "lat": 26.6, "lon": -81.8, "total_pop": 3000, "households_no_vehicle": 401.0,
     "pct_no_vehicle": 22.6, "pop_65_plus": 900.0, "pct_age_65_plus": 30.0, "pct_disability": 20.0,
     "mobile_homes": 0.0, "group_quarters_pop": 0.0, "need_score": 0.34, "nearest_shelter_km": 0.8,
     "nearest_wheelchair_yes_km": 9.6},
    {"geo_id": "12071000200", "lat": 26.7, "lon": -81.9, "total_pop": 2000, "households_no_vehicle": 17.0,
     "pct_no_vehicle": 2.0, "pop_65_plus": 1100.0, "pct_age_65_plus": 55.0, "pct_disability": 21.0,
     "mobile_homes": 2989.0, "group_quarters_pop": 0.0, "need_score": 0.24, "nearest_shelter_km": 3.9,
     "nearest_wheelchair_yes_km": 11.6},
]

FACTS = {
    "county": {"12071": {"county_fips": "12071", "name": "Lee County", "shelters_total": 100, "wheelchair_yes": 2,
                         "wheelchair_no": 5, "wheelchair_unrecorded": 93, "generator_yes": 6, "generator_no": 0,
                         "generator_unrecorded": 94, "medical_needs": 9, "in_surge_zone": 0, "evac_total": 69,
                         "lacktrpt_published_tracts": 0}},
    "tracts": {"12071": {"county_fips": "12071", "tracts": TRACTS}},
    "care": {"12071": {"county_fips": "12071", "facilities": [
        {"ccn": "105966", "name": "LARSEN HEALTH CENTER", "lat": 26.6, "lon": -81.8, "certified_beds": 180,
         "nearest_generator_yes_km": 14.0},
        {"ccn": "105864", "name": "PAGE REHAB", "lat": 26.6, "lon": -81.8, "certified_beds": 180,
         "nearest_generator_yes_km": 6.65}]}},
    "power": {"12071": {"county_fips": "12071", "power_dependent_dme": 7381, "is_censored": False,
                        "medical_shelters": 9, "medical_capacity": 2497, "medical_capacity_unrecorded": 0,
                        "caveat": "County total"}},
    "candidates": {
        "12071000100": {"geo_id": "12071000100", "county_fips": None, "reachable_km": 3.0, "usage": None,
                        "counts_within_reach": {}, "shelters": [
                            _shelter("A", 0.24), _shelter("B", 0.84), _shelter("C", 2.3, surge="yes"),
                            _shelter("D", 5.0, within=False)]},
        "12071000200": {"geo_id": "12071000200", "county_fips": None, "reachable_km": 3.0, "usage": None,
                        "counts_within_reach": {}, "shelters": [_shelter("E", 4.5, within=False)]},
    },
    "verifications": {"A": _verif("A", "closed"), "B": _verif("B", "renamed"),
                      "E": _verif("E", "confirmed", maps_wc="no", record="yes", agreement="disagree")},
}


def _gaps(f=FACTS):
    return compute_gaps(copy.deepcopy(f), 3.0, 10.0)


def by_cat(gaps, cat):
    return [g for g in gaps if g["category"] == cat]


def test_no_accessible_destination_splits_subtypes():
    g = by_cat(_gaps(), "no_accessible_destination")
    subtypes = {x["affected"]["tracts"][0]: x["subtype"] for x in g}
    assert subtypes == {"12071000100": "nobody_assessed", "12071000200": "none_within_reach"}
    first = [x for x in g if x["affected"]["tracts"] == ["12071000100"]][0]
    # the closed shelter A is not counted as a destination
    assert "A" not in first["affected"]["shelters"] and first["metrics"]["shelters_within_reach"] == 2


def test_recorded_no_subtype():
    f = copy.deepcopy(FACTS)
    for s in f["candidates"]["12071000100"]["shelters"]:
        s["wheelchair_accessible"] = "no"
    g = by_cat(_gaps(f), "no_accessible_destination")
    assert [x["subtype"] for x in g if x["affected"]["tracts"] == ["12071000100"]] == ["recorded_no"]


def test_recorded_yes_within_reach_means_no_gap():
    f = copy.deepcopy(FACTS)
    f["candidates"]["12071000100"]["shelters"][1]["wheelchair_accessible"] = "yes"
    assert not [x for x in by_cat(_gaps(f), "no_accessible_destination") if "12071000100" in x["affected"]["tracts"]]


def test_unassessed_counts_unchecked_and_no_answer():
    g = by_cat(_gaps(), "unassessed_access")[0]
    # C is unrecorded and never checked; B is unrecorded but Maps answered yes
    assert g["affected"]["shelters"] == ["C"] and g["metrics"]["not_checked_on_maps"] == 1


def test_generator_surge_mismatch_medical():
    gaps = _gaps()
    assert by_cat(gaps, "generator_gap")[0]["affected"]["care_facilities"] == ["105966"]
    assert by_cat(gaps, "shelter_in_surge_zone")[0]["affected"]["shelters"] == ["C"]
    mism = {x["affected"]["shelters"][0]: x["subtype"] for x in by_cat(gaps, "record_mismatch")}
    assert mism == {"A": "closed", "B": "renamed", "E": "wheelchair_disagree"}
    med = by_cat(gaps, "medical_needs_gap")[0]["metrics"]
    assert med["people_per_medical_space"] == 3.0


def _full_facts():
    f = copy.deepcopy(FACTS)
    f["gaps"] = {"12071": {"county_fips": "12071", "gaps": compute_gaps(copy.deepcopy(f)), "reachable_km": 3.0}}
    return f


def _judgments(**over):
    j = {
        "headline": "Of 3 shelters checked, 1 was closed on Google Maps.",
        "tract_reasons": [{"id": "12071000100", "reason": "401.0 households own no car (22.6%)."},
                          {"id": "99999999999", "reason": "Invented tract."}],
        "destination_verdicts": [
            {"shelter_id": "A", "verdict": "holds_up",
             "verdict_reason": [{"text": "Looks fine.", "source_ids": ["src_1"], "freshness": "live"}]},
            {"shelter_id": "B", "verdict": "holds_up",
             "verdict_reason": [{"text": "Renamed school, 4.1 minutes away.", "source_ids": ["src_3"],
                                 "freshness": "live"}]},
        ],
        "fixes": [{"priority": 1, "category": "record_mismatch", "title": "Update B's record",
                   "recommendation": "Rename it in the plan.",
                   "affected": {"tracts": ["12071000100", "12071999999"], "shelters": ["B", "ZZZ"],
                                "care_facilities": []}, "source_ids": ["src_3", "src_99"]},
                  {"priority": 2, "category": "generator_gap", "title": "Generators for 4,321 beds",
                   "recommendation": "Invented number.", "affected": {"tracts": [], "shelters": [],
                                                                      "care_facilities": ["105966"]},
                   "source_ids": []}],
        "unknowns_text": [{"topic": "Wheelchair access", "why": "Nobody recorded it.", "how_to_close": "Visit."}],
    }
    j.update(over)
    return schemas.BriefJudgments.model_validate(j)


@pytest.fixture
def assemble():
    from api import briefs
    req = schemas.BriefRequest(county_fips="12071")
    return lambda f, j: briefs.assemble_brief(f, j, req)


def test_assemble_validates_and_uses_facts_numbers(assemble):
    b = schemas.Brief.model_validate(assemble(_full_facts(), _judgments()))
    tract = b.who_cannot_leave[0]
    assert tract.id == "12071000100" and tract.people == 3000 and tract.reason.startswith("401.0 households")
    # nursing homes in a generator gap are preferred; 105864 has a generator-yes shelter at 6.65 km
    assert {w.id for w in b.who_cannot_leave} == {"12071000100", "12071000200", "105966"}


def test_assemble_drops_invented_ids_and_sources(assemble):
    b = assemble(_full_facts(), _judgments())
    fix = [x for x in b["fixes"] if x["title"] == "Update B's record"][0]
    assert fix["affected"]["tracts"] == ["12071000100"] and fix["affected"]["shelters"] == ["B"]
    assert all(s in {x["id"] for x in b["sources"]} for s in fix["source_ids"])


def test_invented_number_falls_back_to_rule_text(assemble):
    b = assemble(_full_facts(), _judgments())
    gen = [x for x in b["fixes"] if x["category"] == "generator_gap"][0]
    assert "4,321" not in gen["title"] and "1 nursing homes" in gen["title"]


def test_maps_closed_forces_do_not_rely_and_holds_up_is_earned(assemble):
    b = assemble(_full_facts(), _judgments())
    v = {d["shelter"]["shelter_id"]: d["verdict"] for d in b["destinations"]}
    assert v["A"] == "do_not_rely"  # Maps: permanently closed, whatever the writer said
    assert v["B"] == "verify_before_season"  # renamed + blank record is not holds_up
    assert b["destinations"][-1]["shelter"]["shelter_id"] == "A"  # dropped ones sort last


def test_every_gap_category_has_a_fix_and_every_live_segment_cites(assemble):
    b = assemble(_full_facts(), _judgments())
    gap_cats = {g["category"] for g in _full_facts()["gaps"]["12071"]["gaps"]}
    assert gap_cats <= {fx["category"] for fx in b["fixes"]}
    assert [fx["priority"] for fx in b["fixes"]] == list(range(1, len(b["fixes"]) + 1))
    for d in b["destinations"]:
        assert any(s["freshness"] == "live" and s["source_ids"] for s in d["verdict_reason"])


def test_source_ids_unique_across_brief(assemble):
    b = assemble(_full_facts(), _judgments())
    ids = [s["id"] for s in b["sources"]]
    assert len(ids) == len(set(ids)) == 6
    for d in b["destinations"]:
        own = {s["id"] for s in d["verification"]["sources"]}
        assert all(set(seg["source_ids"]) <= own for seg in d["verification"]["summary"])


def test_unknowns_counted_with_defaults(assemble):
    b = assemble(_full_facts(), _judgments())
    u = {x["topic"]: x for x in b["unknowns"]}
    assert (u["Wheelchair access"]["count"], u["Wheelchair access"]["of"]) == (93, 100)
    assert u["Wheelchair access"]["why"] == "Nobody recorded it."
    assert "Lack of reliable transport" in u and u["Where power-dependent residents live"]["count"] == 7381


def test_no_judgments_still_builds_a_complete_brief(assemble):
    b = schemas.Brief.model_validate(assemble(_full_facts(), None))
    assert b.headline.startswith("Of 3 shelters checked on Google Maps")
    assert b.fixes and b.unknowns and b.destinations


def test_tract_label():
    assert brief_plan.tract_label("12071020801") == "Tract 208.01"
    assert brief_plan.tract_label("12071080300") == "Tract 803"
