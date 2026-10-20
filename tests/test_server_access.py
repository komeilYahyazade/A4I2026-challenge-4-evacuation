"""Access tiers on the private agent: identity from the (Cloud Run-verified) ID token decides; the public
header can only restrict. Public callers never write to BigQuery and get at most 2 Maps checks."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

import agent.server as server

WEB_SA = server.PUBLIC_CALLER_SA


def jwt(claims):
    enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"Bearer {enc({'alg': 'RS256'})}.{enc(claims)}.sig"


CASES = {
    "web_sa": ({"authorization": jwt({"email": WEB_SA})}, True),
    "other_user": ({"authorization": jwt({"email": "Manager@Lee.example"})}, False),
    "no_token": ({}, True),
    "garbage_token": ({"authorization": "Bearer not.a~jwt"}, True),
    "token_without_email": ({"authorization": jwt({"sub": "1234"})}, True),
    "signed_in_plus_public_header": ({"authorization": jwt({"email": "m@lee.example"}), "x-readiness-public": "1"}, True),
    "public_header_cannot_grant": ({"authorization": jwt({"email": WEB_SA}), "x-readiness-public": "0"}, True),
}


@pytest.fixture
def client(monkeypatch):
    calls = {"verify": [], "saved": 0, "brief": []}

    async def fake_verify(ids, origin=""):
        calls["verify"].append(list(ids))
        return {"payload_type": "shelter_verification.v1", "results": [], "skipped_over_limit": []}

    def fake_save(out, cfg=None):
        calls["saved"] += 1
        return 1

    async def fake_brief(fips, top_tracts=5, verify_with_maps=True, tool_context=None):
        calls["brief"].append(verify_with_maps)
        return {"payload_type": "readiness_brief.v1"}

    monkeypatch.setattr(server, "verify_shelters", fake_verify)
    monkeypatch.setattr(server.plans, "maybe_save_place_ids", fake_save)
    monkeypatch.setattr(server, "build_readiness_brief", fake_brief)
    monkeypatch.setenv("VERIFY_MAX", "4")
    return TestClient(server.app), calls


@pytest.mark.parametrize("name", CASES)
def test_identity_tiers(client, name):
    c, calls = client
    headers, public = CASES[name]
    h = c.get("/api/health", headers=headers).json()
    assert h["caller"] == ("public" if public else "signed_in")
    assert ("caller_email" in h) is (not public)
    out = c.post("/api/verify", json={"shelter_ids": ["1", "2", "3", "4", "5"]}, headers=headers).json()
    assert out["place_ids_stored"] == (0 if public else 1)
    assert calls["saved"] == (0 if public else 1)
    assert calls["verify"][-1] == (["1", "2"] if public else ["1", "2", "3", "4"])
    assert out["skipped_over_limit"] == (["3", "4", "5"] if public else ["5"])


@pytest.mark.parametrize("name", [n for n, (_, public) in CASES.items() if public])
def test_public_cannot_write_plans(client, name):
    c, _ = client
    headers, _ = CASES[name]
    r = c.post("/api/plans", json={"kind": "allocation", "created_by": "x"}, headers=headers)
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    r = c.post("/api/plans/abc/approve", json={"approved_by": "x"}, headers=headers)
    assert r.status_code == 403


def test_verify_limit_clamp():
    from agent.config import AgentConfig

    cfg = AgentConfig(project_id="demo-project", verify_max=4)
    assert server.verify_limit(10, public=False, cfg=cfg) == 4
    assert server.verify_limit(10, public=True, cfg=cfg) == 2
    assert server.verify_limit(1, public=True, cfg=cfg) == 1
    assert server.verify_limit(-3, public=False, cfg=cfg) == 0


def test_brief_clamp_and_never_stores(client):
    c, calls = client
    signed = CASES["other_user"][0]
    assert c.post("/api/brief", json={"verify_top_n": 9}, headers=signed).json()["verify_limit"] == 4
    assert c.post("/api/brief", json={"verify_top_n": 0}, headers=signed).json()["verify_limit"] == 0
    # public cap (2) is below VERIFY_MAX (4): the brief runs without Maps; live checks go through /api/verify
    assert c.post("/api/brief", json={"verify_top_n": 9}).json()["verify_limit"] == 0
    assert calls["brief"] == [True, False, False] and calls["saved"] == 0


def test_place_id_write_is_a_merge():
    from agent.plans import PLACE_ID_SQL

    assert PLACE_ID_SQL.lstrip().startswith("MERGE") and "ON t.shelter_id = s.shelter_id AND t.place_id = s.place_id" in PLACE_ID_SQL
