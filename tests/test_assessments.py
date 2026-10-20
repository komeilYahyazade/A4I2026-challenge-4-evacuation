"""Field assessments: storage shape, the verdict rules they change, and the brief they update.

Synthetic rows only (rule 10). Reuses the brief fixture from test_briefs.
"""

import asyncio
import copy
import re
from datetime import datetime, timezone

import pytest
from test_briefs import _full_facts, _judgments

from contract import schemas
from readiness_agent import prompts
from readiness_agent.tools import assess, assessments

AT = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


def row(sid, wheelchair, record="unrecorded", notes="Side ramp, unlocked during the day.", role="facilities manager"):
    return {"assessment_id": f"asm_{sid}", "shelter_id": sid, "shelter_name": f"Shelter {sid}", "county": "12071",
            "operator_notes": notes, "wheelchair_unaccompanied_entries": wheelchair, "ramp_unlocked_24_7": None,
            "generator_powers_medical_outlets": None, "verified_status": "X", "assessed_at": AT,
            "assessor_role": role, "source": "assessment_agent", "county_fips": "12071", "record_wheelchair": record}


@pytest.fixture
def assemble():
    from api import briefs
    req = schemas.BriefRequest(county_fips="12071")
    return lambda f, j, rows=None: briefs.assemble_brief(f, j, req, rows)


# ---- storage ----------------------------------------------------------------------------------


def test_null_answers_are_unclear_or_unknown_never_no():
    a = assessments.shape(row("B", None))
    assert a["wheelchair_unaccompanied"] == "unclear"
    assert a["ramp_unlocked_24_7"] == a["generator_powers_medical_outlets"] == "unknown"
    assert a["freshness"] == "field" and a["assessed_at"] == "2026-10-20T18:00:00Z"
    schemas.FieldAssessment.model_validate(a)


def test_effective_wheelchair_prefers_a_definite_field_answer():
    yes, no, unclear = (assessments.shape(row("B", v)) for v in (True, False, None))
    assert assessments.effective_wheelchair("unrecorded", yes) == "yes"
    assert assessments.effective_wheelchair("yes", no) == "no"
    assert assessments.effective_wheelchair("no", unclear) == "no"
    assert assessments.effective_wheelchair("unrecorded", None) == "unrecorded"


def test_build_record_validates_and_maps_to_bools():
    shelter = {"shelter_id": "70851", "name": "Harborside", "county_fips": "12071"}
    p = assessments.build_record(shelter, "no", "no", "unknown", "Gate padlocked after 5pm.", "", "ses_1")
    assert (p["wheelchair"], p["ramp"], p["generator"]) == (False, False, None)
    assert p["verified_status"] == "VERIFIED_INACCESSIBLE_OR_RESTRICTED"
    assert p["assessor_role"] == "shelter operator" and p["source"] == "assessment_agent"
    with pytest.raises(ValueError):
        assessments.build_record(shelter, "maybe", "unknown", "unknown", "x", "r", None)
    with pytest.raises(ValueError):
        assessments.build_record(shelter, "yes", "unknown", "unknown", "   ", "r", None)


def test_closes_unrecorded_counts_only_definite_answers_on_blank_records():
    rows = [row("A", True), row("B", None), row("C", False, record="yes"), row("D", False)]
    assert assessments.closes_unrecorded(rows) == 2


# ---- verdict rules ----------------------------------------------------------------------------


def _v(status="confirmed", agreement="record_blank"):
    return {"status": status, "wheelchair": {"maps": "no_answer", "record": "unrecorded", "agreement": agreement}}


SHELTER = {"wheelchair_accessible": "unrecorded", "in_surge_slosh_area": "no", "in_100_yr_floodplain": "no"}


def test_field_answers_change_the_verdict():
    from api.briefs import _default_verdict
    yes, no, unclear = (assessments.shape(row("B", v)) for v in (True, False, None))
    assert _default_verdict(_v(), SHELTER) == "verify_before_season"
    assert _default_verdict(_v(), SHELTER, yes) == "holds_up"  # blank record, operator says yes
    assert _default_verdict(_v(), {**SHELTER, "wheelchair_accessible": "yes"}, no) == "do_not_rely"
    assert _default_verdict(_v("renamed"), SHELTER, yes) == "verify_before_season"  # Maps must confirm
    assert _default_verdict(_v(), SHELTER, unclear) == "verify_before_season"
    assert _default_verdict(_v("closed"), SHELTER, yes) == "do_not_rely"  # closed stays closed


# ---- the brief, re-assembled with assessments ------------------------------------------------


def test_no_assessments_leaves_the_brief_unchanged(assemble):
    before, after = assemble(_full_facts(), _judgments()), assemble(_full_facts(), _judgments(), [])
    before.pop("generated_at"), after.pop("generated_at")
    assert before == after


def test_operator_yes_turns_a_disagreeing_shelter_into_holds_up(assemble):
    f = _full_facts()
    b = schemas.Brief.model_validate(assemble(f, _judgments(), [row("E", True, record="yes")]))
    e = next(d for d in b.destinations if d.shelter.shelter_id == "E")
    assert e.verdict == "holds_up"
    assert e.shelter.wheelchair_accessible == "unrecorded"  # FEMA's own (blank) flag is still shown as FEMA's
    assert e.shelter.field_assessment.wheelchair_unaccompanied == "yes"
    assert e.verdict_reason[-1].freshness == "field" and "Side ramp" in e.verdict_reason[-1].text


def test_operator_no_adds_a_first_priority_fix_and_do_not_rely(assemble):
    b = assemble(_full_facts(), _judgments(), [row("B", False, notes="Gate padlocked after 5pm.")])
    d = next(x for x in b["destinations"] if x["shelter"]["shelter_id"] == "B")
    assert d["verdict"] == "do_not_rely"
    first = b["fixes"][0]
    assert first["category"] == "field_assessment" and first["affected"]["shelters"] == ["B"]
    assert "cannot get in unaccompanied" in first["title"]
    schemas.Brief.model_validate(b)


def test_access_gaps_are_recomputed_and_reworded(assemble):
    f = _full_facts()

    def tract_100_gap(brief):
        return any(x["category"] == "no_accessible_destination" and "12071000100" in x["affected"]["tracts"]
                   for x in brief["fixes"])
    assert tract_100_gap(assemble(f, _judgments()))
    # B is within reach of tract 100 and Maps did not drop it; the operator says yes. Tract 200 has
    # no shelter within reach at all, so its gap stays.
    b = assemble(f, _judgments(), [row("B", True)])
    assert not tract_100_gap(b)


def test_every_recomputed_access_gap_keeps_its_own_fix(assemble):
    from api.briefs import _with_field_answers
    from readiness_agent.tools.gaps import compute_gaps
    f = _full_facts()
    rows = [row("C", None)]  # touches the plan without answering: gaps recompute, counts stay
    gaps = compute_gaps(_with_field_answers(f, {"C": assessments.shape(rows[0])}), 3.0)
    want = sum(1 for g in gaps if g["category"] == "no_accessible_destination")
    b = assemble(f, _judgments(), rows)
    assert want >= 2
    assert sum(1 for x in b["fixes"] if x["category"] == "no_accessible_destination") == want
    assert [x["priority"] for x in b["fixes"]] == list(range(1, len(b["fixes"]) + 1))


def test_unknown_counts_and_headline_reflect_phone_answers(assemble):
    b = assemble(_full_facts(), _judgments(), [row("B", True), row("Z", None)])
    u = {x["topic"]: x for x in b["unknowns"]}
    assert u["Wheelchair access"]["count"] == 92  # 93 blank in FEMA, one answered by phone
    assert "1 answered by phone" in u["Wheelchair access"]["how_to_close"]
    assert "answered by the operator by phone" in b["headline"]


# ---- tools and agent wiring -------------------------------------------------------------------


def test_save_rejects_a_bad_answer_without_writing(monkeypatch):
    writes = []
    monkeypatch.setattr(assess.verify, "load_shelter", lambda sid: {"shelter_id": sid, "name": "S", "county_fips": "12071"})
    monkeypatch.setattr(assessments, "insert", writes.append)
    out = asyncio.run(assess.save_field_assessment("1", "probably", "Ramp at side.", tool_context=None))
    assert out["status"] == "error" and not writes


def test_contact_parse_drops_none_values():
    c = assess.parse_contact("MAPS_NAME: Example Center\nPHONE: (239) 555-0100\nWEBSITE: none\nNOTE: Front desk.")
    assert c == {"maps_name": "Example Center", "phone": "(239) 555-0100", "website": None, "note": "Front desk."}


def test_assessment_agent_wiring_and_instruction():
    from readiness_agent.agent import assessment_agent, readiness_agent
    assert [t.name for t in assessment_agent.tools] == ["get_shelter_contact", "save_field_assessment"]
    assert "get_field_assessments" in [getattr(t, "name", "") for t in readiness_agent.tools]
    # ADK reads {key} as session state: the only placeholder allowed is the shelter id.
    assert re.findall(r"\{[^}]*\}", prompts.ASSESS) == ["{shelter_id}"]
    assert "unaccompanied, today" in prompts.ASSESS
