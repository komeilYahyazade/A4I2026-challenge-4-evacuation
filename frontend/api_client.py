"""One function per /v1 route. The UI never touches BigQuery or Gemini.

USE_MOCK=1 reads contract/mocks/ directly (local offline dev only). When API_BASE_URL is a
private Cloud Run service, each request carries an ID token for it.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8080").rstrip("/")
USE_MOCK = os.environ.get("USE_MOCK", "0") == "1"
MOCKS = Path(__file__).resolve().parents[1] / "contract" / "mocks"
TIMEOUT_S = 310

_token: tuple[float, str] | None = None


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def _id_token() -> str | None:
    """ID token for a private Cloud Run API (the UI's service account, or API_ID_TOKEN locally)."""
    global _token
    if os.environ.get("API_ID_TOKEN"):
        return os.environ["API_ID_TOKEN"]
    if not API_BASE_URL.startswith("https://"):
        return None
    if _token and time.time() - _token[0] < 45 * 60:
        return _token[1]
    try:
        import google.auth.transport.requests
        from google.oauth2 import id_token
        tok = id_token.fetch_id_token(google.auth.transport.requests.Request(), API_BASE_URL)
    except Exception:
        return None
    _token = (time.time(), tok)
    return tok


def _mock(name: str) -> dict:
    return json.loads((MOCKS / name).read_text())


def _call(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    tok = _id_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        r = requests.request(method, f"{API_BASE_URL}{path}", json=body, headers=headers, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise ApiError(503, "api_unreachable", f"Could not reach the API: {type(e).__name__}", True) from e
    try:
        data = r.json()
    except ValueError:
        raise ApiError(r.status_code, "bad_response", r.text[:200], r.status_code >= 500) from None
    if r.status_code >= 400:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise ApiError(r.status_code, err.get("code", "error"), err.get("message", r.text[:200]),
                       err.get("retryable", False))
    return data


def health() -> dict:
    return _mock("health.json") if USE_MOCK else _call("GET", "/v1/health")


def counties() -> dict:
    return _mock("counties.json") if USE_MOCK else _call("GET", "/v1/counties")


def county_map(county_fips: str) -> dict:
    return _mock("county_map.json") if USE_MOCK else _call("GET", f"/v1/counties/{county_fips}/map")


def shelter(shelter_id: str) -> dict:
    return _mock("shelter.json") if USE_MOCK else _call("GET", f"/v1/shelters/{shelter_id}")


def verify(shelter_id: str, from_geo_id: str | None = None, include_route: bool = True) -> dict:
    if USE_MOCK:
        return _mock("verify.json")
    return _call("POST", f"/v1/shelters/{shelter_id}/verify",
                 {"from_geo_id": from_geo_id, "include_route": include_route})


def create_brief(county_fips: str, track: str, reachable_km: float, max_verify: int, include_routes: bool) -> dict:
    if USE_MOCK:
        return _mock("brief_accepted.json")
    return _call("POST", "/v1/briefs", {"county_fips": county_fips, "track": track, "options": {
        "reachable_km_no_vehicle": reachable_km, "max_shelters_to_verify": max_verify,
        "include_routes": include_routes}})


def brief(brief_id: str) -> dict:
    return _mock("brief_complete.json") if USE_MOCK else _call("GET", f"/v1/briefs/{brief_id}")


def audit(brief_id: str) -> dict:
    return _mock("audit.json") if USE_MOCK else _call("GET", f"/v1/briefs/{brief_id}/audit")


def chat(message: str, session_id: str | None, county_fips: str | None, brief_id: str | None) -> dict:
    if USE_MOCK:
        return _mock("chat.json")
    return _call("POST", "/v1/chat", {"session_id": session_id, "county_fips": county_fips,
                                     "brief_id": brief_id, "message": message})
