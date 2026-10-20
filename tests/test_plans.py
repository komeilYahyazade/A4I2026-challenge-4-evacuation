"""Plans: Maps content never reaches storage, only place_id does, and approval needs a named person."""

import json

import pytest

from agent import plans
from agent.config import AgentConfig

CFG = AgentConfig(project_id="demo-project")


def test_strip_maps_removes_every_maps_field_but_place_id():
    verification_like = {
        "payload_type": "readiness_brief.v1",
        "maps_sources": [{"title": "X - Google Maps", "uri": "https://maps.google.com/?cid=1", "place_id": "p1"}],
        "where_they_would_go": [{"shelter_id": "S", "live": {
            "sources": [{"title": "X", "uri": "https://maps.google.com/?cid=1", "place_id": "p1"}],
            "current_name": "New Name", "notes": "from maps", "exists": True, "drive_minutes": 12.0,
            "wheelchair_entrance": "YES"},
            "accessibility": {"value": "YES", "label": "VERIFIED LIVE", "note": "Google Maps reports ramp"}}],
        "link": "https://www.google.com/maps/place/x",
        "stored_pointer": {"place_id": "p1"},
    }
    out = plans.strip_maps(verification_like)
    text = json.dumps(out)
    assert "maps.google" not in text and "google.com/maps" not in text and "New Name" not in text
    assert "drive_minutes" not in text and "wheelchair_entrance" not in text and "VERIFIED LIVE" not in text
    assert out["stored_pointer"] == {"place_id": "p1"}
    assert "live" not in out["where_they_would_go"][0]
    assert "accessibility" not in out["where_they_would_go"][0]


def test_place_id_rows_keep_only_pointer_verdict_time():
    v = {"verified_at": "2026-10-20T16:00:00+00:00", "results": [
        {"shelter_id": "1", "verdict": "DROP", "stored_pointer": {"place_id": "p1"}, "live": {"verified_at": "t1"}},
        {"shelter_id": "2", "verdict": "KEEP_UNVERIFIED", "stored_pointer": {}, "live": None}]}
    assert plans.place_id_rows(v) == [{"shelter_id": "1", "place_id": "p1", "verified_at": "t1", "verdict": "DROP"}]


def test_save_plan_writes_stripped_json_and_logs(monkeypatch):
    calls = []

    def fake_run(sql, params=None, cfg=None):
        calls.append((sql, dict(params or {})))
        return [{"v": 3}] if "MAX(version)" in sql else []

    monkeypatch.setattr("agent.bq.run", fake_run)
    out = plans.save_plan(CFG, {"payload_type": "allocation.v1", "county_fips": "12071", "totals": {"a": 1},
                                "maps_sources": [{"uri": "https://maps.google.com/?cid=9"}]}, "em@lee", "PLANNING")
    assert out["version"] == 3 and out["status"] == "DRAFT"
    insert = next(p for s, p in calls if "INSERT INTO" in s and "plans" in s)
    assert "maps" not in insert["plan_json"] and insert["created_by"] == "em@lee"
    assert any("decision_log" in s and p["action"] == "PLAN_DRAFTED" for s, p in calls)
    assert all("`demo-project.readiness_ops." in s for s, _ in calls)


def test_approval_requires_a_named_person():
    with pytest.raises(ValueError):
        plans.approve_plan(CFG, "id", "  ")


def test_list_sql_renders(monkeypatch):
    seen = []
    monkeypatch.setattr("agent.bq.run", lambda sql, params=None, cfg=None: seen.append(sql) or [])
    plans.list_plans(CFG, "12071")
    assert "'$.payload_type'" in seen[0] and "'$.totals'" in seen[0]
