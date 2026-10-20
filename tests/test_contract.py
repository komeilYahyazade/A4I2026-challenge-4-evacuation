"""Every mock in contract/mocks/ validates against contract/schemas.py."""

import json
from pathlib import Path

import pytest

from contract import schemas

MOCKS = Path(__file__).resolve().parents[1] / "contract" / "mocks"

MODEL_FOR = {
    "health.json": schemas.Health,
    "counties.json": schemas.CountiesResponse,
    "county_map.json": schemas.CountyMap,
    "insights.json": schemas.CountyInsights,
    "shelter.json": schemas.ShelterDetail,
    "verify.json": schemas.ShelterVerification,
    "brief_accepted.json": schemas.BriefAccepted,
    "brief_running.json": schemas.BriefStatus,
    "brief_complete.json": schemas.BriefStatus,
    "audit.json": schemas.AuditResponse,
    "chat.json": schemas.ChatResponse,
    "assess.json": schemas.AssessResponse,
    "error.json": schemas.ErrorResponse,
}


def test_every_mock_has_a_model():
    assert sorted(p.name for p in MOCKS.glob("*.json")) == sorted(MODEL_FOR)


@pytest.mark.parametrize("name", sorted(MODEL_FOR))
def test_mock_validates(name):
    MODEL_FOR[name].model_validate(json.loads((MOCKS / name).read_text()))


def test_unrecorded_is_not_a_boolean():
    with pytest.raises(Exception):
        schemas.FlagCounts.model_validate({"yes": 1, "no": 1})
    shelter = json.loads((MOCKS / "shelter.json").read_text())
    shelter["wheelchair_accessible"] = None
    with pytest.raises(Exception):
        schemas.ShelterDetail.model_validate(shelter)


def test_complete_brief_source_ids_resolve():
    status = schemas.BriefStatus.model_validate(json.loads((MOCKS / "brief_complete.json").read_text()))
    known = {s.id for s in status.brief.sources}
    cited = {i for f in status.brief.fixes for i in f.source_ids}
    cited |= {i for d in status.brief.destinations for seg in d.verdict_reason for i in seg.source_ids}
    assert cited <= known


def test_brief_judgments_schema_builds():
    # The writer's output_schema must serialise to a JSON schema Gemini can take.
    schemas.BriefJudgments.model_json_schema()
