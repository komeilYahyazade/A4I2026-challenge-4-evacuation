"""MOCK_MODE: serve contract/mocks/*.json. Mock briefs step from running to complete so the UI's
polling loop can be built against them."""

import json
import time
import uuid
from functools import lru_cache

from api.config import ROOT

MOCKS = ROOT / "contract" / "mocks"
MOCK_BRIEF_SECONDS = 6.0
_mock_briefs: dict[str, float] = {}


@lru_cache(maxsize=None)
def _load(name: str) -> str:
    return (MOCKS / name).read_text()


def mock(name: str) -> dict:
    return json.loads(_load(name))


def start_brief() -> dict:
    brief_id = f"brf_{uuid.uuid4().hex[:8]}"
    _mock_briefs[brief_id] = time.time()
    return {"brief_id": brief_id, "status": "running", "poll_url": f"/v1/briefs/{brief_id}"}


def brief_status(brief_id: str) -> dict | None:
    started = _mock_briefs.get(brief_id)
    if started is None and brief_id != "brf_01JB7X":
        return None
    elapsed = time.time() - (started or 0)
    if elapsed < MOCK_BRIEF_SECONDS:
        body = mock("brief_running.json")
        body["progress"]["done"] = min(12, int(12 * elapsed / MOCK_BRIEF_SECONDS))
    else:
        body = mock("brief_complete.json")
    body["brief_id"] = brief_id
    return body
