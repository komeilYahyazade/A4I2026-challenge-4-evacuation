"""Every fixture the front end builds against validates against agent/contract.py."""

import json
from pathlib import Path

import pytest

from agent.contract import PAYLOAD_MODELS, validate

FIXTURES = sorted((Path(__file__).resolve().parent.parent / "contract" / "fixtures").glob("*.json"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_validates(path):
    obj = json.loads(path.read_text())
    assert obj["payload_type"] in PAYLOAD_MODELS
    validate(obj)


def test_every_owned_payload_has_a_fixture():
    types = {json.loads(p.read_text())["payload_type"] for p in FIXTURES}
    assert set(PAYLOAD_MODELS) <= types


def test_komeil_d2_sample_brief_validates():
    sample = Path(__file__).resolve().parent.parent / "docs" / "samples" / "readiness_brief.v1.json"
    validate(json.loads(sample.read_text()))
