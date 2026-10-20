"""Allocator: capacity is respected, leftover demand becomes a named gap, unrecorded capacity is
never treated as zero or as available, and recorded accessibility never changes who goes where."""

import pytest

from agent.allocate import solve


def tract(gid, lat=26.6, lon=-81.8, hh=100, pph=2.0, **kw):
    return {"geo_id": gid, "lat": lat, "lon": lon, "households": 1000, "households_no_vehicle": hh,
            "persons_per_household": pph, "est_persons_no_vehicle": hh * pph, **kw}


def edge(gid, sid, km, cap=100, wc="UNRECORDED", surge="UNRECORDED", status=None, pre=True):
    return {"geo_id": gid, "shelter_id": sid, "shelter_name": f"Shelter {sid}", "shelter_county_fips": "12071",
            "shelter_lat": 26.6, "shelter_lng": -81.8, "straight_line_km": km, "rank_k": 1,
            "usage": "EVAC" if pre else "POST", "is_pre_landfall": pre, "evacuation_capacity": cap,
            "capacity_status": status or ("RECORDED" if cap else "UNRECORDED"), "capacity_implausible": False,
            "wheelchair_status": wc, "generator_status": "UNRECORDED", "surge_status": surge, "is_medical": False}


def by_shelter(out):
    load = {}
    for a in out["assignments"]:
        load[a["shelter_id"]] = load.get(a["shelter_id"], 0) + a["persons"]
    return load


def test_capacity_respected_and_overflow_becomes_unassigned_gap():
    out = solve([tract("T1", hh=150)], [edge("T1", "A", 2.0, cap=120), edge("T1", "B", 5.0, cap=100)])
    assert out["status"] == "OPTIMAL"
    assert by_shelter(out) == {"A": 120, "B": 100}
    assert out["totals"] == {"demand_persons": 300, "assigned_persons": 220, "unassigned_persons": 80,
                             "usable_capacity": 220, "shelters_used": 2}
    assert out["unassigned"][0]["reason"] == "CAPACITY_EXHAUSTED"
    gap = next(g for g in out["gaps"] if g["type"] == "UNASSIGNED_DEMAND" and g["ref"]["kind"] == "tract")
    assert gap["severity"] == "HIGH" and gap["ref"]["kind"] == "tract" and gap["persons_affected"] == 80


def test_nearest_first_and_no_shelter_within_max_km():
    out = solve([tract("T1", hh=10), tract("T2", hh=10)],
                [edge("T1", "A", 1.0, cap=100), edge("T1", "B", 9.0, cap=100), edge("T2", "B", 30.0)], max_km=15)
    assert by_shelter(out) == {"A": 20}
    assert out["assignments"][0]["mode"] == "WALK"
    assert out["unassigned"] == [{"geo_id": "T2", "persons": 20, "lat": 26.6, "lng": -81.8,
                                  "reason": "NO_SHELTER_WITHIN_MAX_KM"}]


def test_unrecorded_capacity_is_excluded_not_zero_and_listed():
    out = solve([tract("T1", hh=10)], [edge("T1", "U", 1.0, cap=None), edge("T1", "Z", 1.5, cap=0),
                                       edge("T1", "A", 4.0, cap=50)])
    assert by_shelter(out) == {"A": 20}
    excluded = {e["shelter_id"]: e["reason"] for e in out["params"]["excluded_shelters"]}
    assert excluded == {"U": "CAPACITY_UNRECORDED", "Z": "CAPACITY_UNRECORDED"}
    assert out["totals"]["usable_capacity"] == 50
    assert sum(1 for g in out["gaps"] if g["type"] == "CAPACITY_UNRECORDED_EXCLUDED") == 2
    assert next(s for s in out["shelters"] if s["shelter_id"] == "U")["utilization"] is None


def test_implausible_capacity_excluded_and_flagged():
    e = edge("T1", "G", 1.0, cap=270_135)
    out = solve([tract("T1", hh=10)], [e, edge("T1", "A", 3.0, cap=100)])
    assert by_shelter(out) == {"A": 20}
    assert {"shelter_id": "G", "name": "Shelter G", "reason": "CAPACITY_IMPLAUSIBLE"} in out["params"]["excluded_shelters"]


def test_shelters_down_resolves_elsewhere():
    edges = [edge("T1", "A", 1.0, cap=100), edge("T1", "B", 6.0, cap=100)]
    before = solve([tract("T1", hh=20)], edges)
    after = solve([tract("T1", hh=20)], edges, shelters_down=["A"])
    assert by_shelter(before) == {"A": 40} and by_shelter(after) == {"B": 40}
    assert after["params"]["shelters_down"] == ["A"]


def test_recorded_accessibility_does_not_change_assignment():
    """Rewarding recorded YES would steer the plan toward good paperwork, not toward people."""
    a = solve([tract("T1", hh=10)], [edge("T1", "U", 2.0, wc="UNRECORDED"), edge("T1", "Y", 2.5, wc="YES")])
    assert by_shelter(a) == {"U": 20}
    assess = [g for g in a["gaps"] if g["type"] == "ASSESS_ASSIGNED_SHELTER"]
    assert assess and assess[0]["label"] == "UNRECORDED" and assess[0]["ref"]["id"] == "U"


def test_assigned_surge_shelter_is_a_high_gap():
    out = solve([tract("T1", hh=10)], [edge("T1", "S", 1.0, surge="YES")])
    assert any(g["type"] == "ASSIGNED_SHELTER_IN_SURGE_ZONE" and g["severity"] == "HIGH" for g in out["gaps"])


def test_post_only_shelters_never_destinations():
    out = solve([tract("T1", hh=10)], [edge("T1", "P", 0.5, pre=False), edge("T1", "A", 3.0)])
    assert by_shelter(out) == {"A": 20}


def test_shelter_seeking_share_scales_demand_and_no_demand_status():
    out = solve([tract("T1", hh=100)], [edge("T1", "A", 1.0, cap=1000)], shelter_seeking_share=0.25)
    assert out["totals"]["demand_persons"] == 50
    assert solve([tract("T1", hh=0)], [edge("T1", "A", 1.0)])["status"] == "NO_DEMAND"


def test_allocation_never_touches_maps(monkeypatch):
    import agent.verify as verify

    async def boom(*a, **k):
        raise AssertionError("allocation must not call Google Maps")

    monkeypatch.setattr(verify, "ground_one", boom)
    out = solve([tract("T1")], [edge("T1", "A", 1.0)])
    assert out["maps_used"] is False and out["assignments"][0]["drive_minutes"] is None


@pytest.mark.parametrize("fn", ["allocate_county"])
def test_tool_has_long_docstring_and_hidden_tool_context(fn):
    import inspect

    import agent.allocate as m

    f = getattr(m, fn)
    assert len(inspect.getdoc(f)) > 80
    assert "tool_context" in inspect.signature(f).parameters


def test_name_says_inactive_is_never_a_destination():
    out = solve([tract("T1", hh=10)], [edge("T1", "I", 0.5) | {"shelter_name": "INACTIVE Grace Church"},
                                       edge("T1", "A", 3.0)])
    assert by_shelter(out) == {"A": 20}
    assert {"shelter_id": "I", "name": "INACTIVE Grace Church", "reason": "NAME_SAYS_INACTIVE"} in \
        out["params"]["excluded_shelters"]


def test_site_options_greedy_coverage():
    from agent.allocate import site_options

    un = [{"geo_id": "A", "persons": 300, "lat": 26.60, "lng": -81.60},
          {"geo_id": "B", "persons": 200, "lat": 26.61, "lng": -81.61},
          {"geo_id": "C", "persons": 400, "lat": 26.30, "lng": -81.30}]
    opts = site_options(un, max_km=15)
    assert [o["persons_covered"] for o in opts] == [500, 400]  # A+B together beat C alone; C second
    assert opts[0]["tracts_covered"] == ["A", "B"] and opts[0]["ref"]["kind"] == "tract"
    assert solve([tract("T1", hh=10)], [edge("T1", "A", 1.0, cap=5)])["site_options"][0]["persons_covered"] == 15


def test_county_headline_gap_first():
    out = solve([tract("12071000100", hh=150)], [edge("12071000100", "A", 2.0, cap=100)], county_fips="12071")
    g = out["gaps"][0]
    assert g["type"] == "UNASSIGNED_DEMAND" and g["ref"] == {"kind": "county", "id": "12071", "lat": None, "lng": None}
    assert g["severity"] == "HIGH" and g["persons_affected"] == 200


def test_tool_path_returns_compact_and_rest_path_full(monkeypatch):
    import asyncio
    import json

    import agent.allocate as m
    from agent.contract import validate

    monkeypatch.setattr(m, "fetch_inputs", lambda cfg, f, km: ([tract("12071000100", hh=150)],
                                                              [edge("12071000100", "A", 2.0, cap=100)]))

    class Ctx:
        state: dict = {}

    full = asyncio.run(m.allocate_county("12071"))
    small = asyncio.run(m.allocate_county("12071", tool_context=Ctx()))
    assert full.get("compact") is None and full["assignments"]
    assert small["compact"] is True and small["assignments"] == [] and small["full"]["path"] == "/api/allocate"
    assert small["totals"] == full["totals"] and small["gaps"][0]["ref"]["kind"] == "county"
    assert len(json.dumps(small)) < len(json.dumps(full))
    validate(small)
    validate(full)


def test_duplicate_shelter_records_are_excluded_not_double_counted():
    e1 = edge("T1", "259512", 2.0, cap=500) | {"shelter_name": "Hudson Elementary"}
    e2 = edge("T1", "259513", 2.0, cap=500) | {"shelter_name": "Hudson Elementary"}
    out = solve([tract("T1", hh=300)], [e1, e2])
    assert out["totals"]["usable_capacity"] == 500
    assert by_shelter(out) == {"259512": 500}
    assert {"shelter_id": "259513", "name": "Hudson Elementary", "reason": "DUPLICATE_RECORD"} in \
        out["params"]["excluded_shelters"]


def test_session_dropped_shelters_propagate_into_allocate_county(monkeypatch):
    import asyncio

    import agent.allocate as m

    monkeypatch.setattr(
        m,
        "fetch_inputs",
        lambda cfg, f, km: (
            [tract("12071000100", hh=20)],
            [edge("12071000100", "A", 1.0, cap=100), edge("12071000100", "B", 4.0, cap=100)],
        ),
    )

    class Ctx:
        state = {"dropped_shelter_ids": ["A"]}

    res = asyncio.run(m.allocate_county("12071", tool_context=Ctx()))
    assert res["params"]["shelters_down"] == ["A"]
    assert res["totals"]["assigned_persons"] == 40
