"""Voice gate on /run_live: public callers are refused with 1008 unless VOICE_PUBLIC=1, everyone is
refused when VOICE_ENABLED=0, and the decision uses the same identity rule as HTTP (fails closed)."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import agent.server as server
from tests.test_server_access import CASES, jwt


@pytest.fixture
def voice_on(monkeypatch, tmp_path):
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice" / "__init__.py").write_text("")
    monkeypatch.setattr(server, "AGENTS_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_ENABLED", "1")
    monkeypatch.delenv("VOICE_PUBLIC", raising=False)


@pytest.mark.parametrize("name", CASES)
def test_refusal_matches_http_tier(voice_on, name):
    headers, public = CASES[name]
    assert server.voice_refusal(headers) == (server.VOICE_OFF_PUBLIC if public else None)


def test_voice_public_flag_opens_it(voice_on, monkeypatch):
    monkeypatch.setenv("VOICE_PUBLIC", "1")
    assert server.voice_refusal({}) is None


def test_voice_disabled_closes_it_for_everyone(voice_on, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "0")
    assert server.voice_refusal(CASES["other_user"][0]) == server.VOICE_OFF


def test_voice_absent_reports_off(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "AGENTS_DIR", str(tmp_path))
    assert server.voice_status()["voice"] is False


@pytest.mark.parametrize("name", [n for n, (_, public) in CASES.items() if public])
def test_public_socket_closed_with_1008(voice_on, name):
    headers, _ = CASES[name]
    c = TestClient(server.app)
    with c.websocket_connect("/run_live?app_name=voice&user_id=u&session_id=s", headers=headers) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1008 and exc.value.reason == server.VOICE_OFF_PUBLIC


def test_health_reports_voice_flags(voice_on):
    h = TestClient(server.app).get("/api/health", headers={"authorization": jwt({"email": "m@lee.example"})}).json()
    assert h["voice"] is True and h["voice_public"] is False
