"""Rehearsal: shelters go offline by a named rule, the plan re-solves, the diff is right, and no
Google Maps call is ever made."""

import asyncio

from agent.rehearse import SCENARIOS, diff_allocations, pick_shelters_down, rehearse_from
from tests.test_allocate import edge, tract

SC = dict(SCENARIOS["ian_2022"], advisory=None, issued_at=None, cone=None)


def test_down_prefers_shelters_in_plan_and_lists_the_rest():
    edges = [edge("T1", "U", 1.0, cap=None, surge="YES"), edge("T1", "A", 2.0, cap=100),
             edge("T1", "B", 3.0, cap=100), edge("T1", "C", 4.0, cap=100)]
    view = [{"shelter_id": s, "reason": "IN_CONE_AND_SURGE", "down_rank": i} for i, s in enumerate(["A", "B", "C"], 1)]
    view = [{"shelter_id": "U", "reason": "IN_SURGE_ZONE", "down_rank": 0}] + view
    down = pick_shelters_down(edges, view)
    assert [(d["shelter_id"], d["in_plan"]) for d in down] == [("U", False), ("A", True), ("B", True)]
    assert down[0]["reason"] == "IN_SURGE_ZONE" and "FEMA" in down[0]["rule"]


def test_fallback_to_recorded_surge_flag_when_view_missing():
    edges = [edge("T1", "S", 1.0, surge="YES"), edge("T1", "A", 2.0)]
    down = pick_shelters_down(edges, None)
    assert [d["shelter_id"] for d in down] == ["S"]


def test_rehearsal_resolves_and_diffs():
    edges = [edge("T1", "A", 1.0, cap=30), edge("T1", "B", 5.0, cap=20)]
    out = rehearse_from([tract("T1", hh=20)], edges, pick_shelters_down(edges, None, explicit=["A"]), SC)
    assert out["payload_type"] == "rehearsal.v1" and out["maps_used"] is False
    assert out["diff"]["reassigned"][0]["from_shelter_id"] == "A"
    assert out["diff"]["reassigned"][0]["to_shelter_id"] == "B"
    assert out["diff"]["newly_unassigned"][0]["persons"] == 20
    assert out["diff"]["capacity_lost"] == 30
    assert out["diff"]["unassigned_before"] == 0 and out["diff"]["unassigned_after"] == 20
    assert out["diff"]["reassigned"][0]["persons"] == 10


def test_diff_with_no_change():
    edges = [edge("T1", "A", 1.0)]
    out = rehearse_from([tract("T1", hh=10)], edges, [], SC)
    assert out["diff"]["reassigned"] == [] and out["diff"]["newly_unassigned"] == []
    assert diff_allocations(out["before"], out["after"])["unassigned_after"] == 0


def test_rehearse_tool_never_calls_maps(monkeypatch):
    import agent.rehearse as reh
    import agent.verify as verify

    async def boom(*a, **k):
        raise AssertionError("rehearsal must not call Google Maps")

    monkeypatch.setattr(verify, "ground_one", boom)
    monkeypatch.setattr(verify, "verify_rows", boom)
    edges = [edge("12071000100", "A", 1.0), edge("12071000100", "B", 2.0, surge="YES")]
    monkeypatch.setattr(reh, "fetch_inputs", lambda cfg, f, km: ([tract("12071000100")], edges))
    monkeypatch.setattr(reh, "fetch_down", lambda cfg, advisory="19": None)
    monkeypatch.setattr(reh, "fetch_storm", lambda cfg, sid: [])
    out = asyncio.run(reh.rehearse("12071"))
    assert out["maps_used"] is False and [d["shelter_id"] for d in out["shelters_down"]] == ["B"]
