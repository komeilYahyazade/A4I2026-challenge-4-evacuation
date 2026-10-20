"""Field assessments: what the shelter operator said when someone asked.

The README's add-on: for most shelters nobody recorded whether a wheelchair user can get in. An
assessment answers one question, "can somebody in a wheelchair get in, unaccompanied, today?", in
the operator's own words. It sits beside the FEMA record (historical) and Google Maps (live) as a
third kind of evidence, freshness "field". Rows are append-only; the newest per shelter wins.

A BOOL column left NULL means the answer was unclear or never asked. That is unrecorded, never no.
"""

from __future__ import annotations

import uuid
from typing import Any

from readiness_agent.tools import bq

ANSWERS = ("yes", "no", "unclear")
OPTIONAL_ANSWERS = ("yes", "no", "unknown")
VERIFIED_STATUS = {"yes": "VERIFIED_ACCESSIBLE", "no": "VERIFIED_INACCESSIBLE_OR_RESTRICTED",
                   "unclear": "UNRESOLVED"}
SOURCE = "assessment_agent"
NOTES_MAX = 600


def _to_bool(answer: str) -> bool | None:
    return {"yes": True, "no": False}.get(answer)


def _from_bool(value: Any, unknown: str) -> str:
    return {True: "yes", False: "no"}.get(value, unknown)


def shape(row: dict) -> dict:
    """A shelter_assessments row as the contract's FieldAssessment."""
    at = row.get("assessed_at")
    return {
        "assessment_id": row.get("assessment_id"),
        "shelter_id": str(row["shelter_id"]),
        "wheelchair_unaccompanied": _from_bool(row.get("wheelchair_unaccompanied_entries"), "unclear"),
        "ramp_unlocked_24_7": _from_bool(row.get("ramp_unlocked_24_7"), "unknown"),
        "generator_powers_medical_outlets": _from_bool(row.get("generator_powers_medical_outlets"), "unknown"),
        "notes": row.get("operator_notes"),
        "assessor_role": row.get("assessor_role"),
        "assessed_at": at.isoformat().replace("+00:00", "Z") if hasattr(at, "isoformat") else at,
        "source": "field_assessment",
        "freshness": "field",
    }


def effective_wheelchair(record_flag: str, assessment: dict | None) -> str:
    """The wheelchair answer the plan should act on: a definite field answer wins over the FEMA
    flag; an unclear field answer leaves the FEMA flag as it was."""
    if assessment and assessment["wheelchair_unaccompanied"] in ("yes", "no"):
        return assessment["wheelchair_unaccompanied"]
    return record_flag


def latest_rows(shelter_ids: list[str] | None = None, county_fips: str | None = None) -> list[dict]:
    """Newest assessment row per shelter, with the FEMA flag beside it (record_wheelchair)."""
    if not shelter_ids and not county_fips:
        return []
    return bq.run("15_latest_assessments.sql", shelter_ids=[str(s) for s in (shelter_ids or [])],
                  county_fips=county_fips)


def latest(shelter_ids: list[str] | None = None, county_fips: str | None = None) -> dict[str, dict]:
    """Newest assessment per shelter, shaped, keyed by shelter_id."""
    return {str(r["shelter_id"]): shape(r) for r in latest_rows(shelter_ids, county_fips)}


def closes_unrecorded(rows: list[dict]) -> int:
    """How many shelters FEMA left blank now have a definite field answer."""
    return sum(1 for r in rows if r.get("record_wheelchair") == "unrecorded"
               and r.get("wheelchair_unaccompanied_entries") is not None)


def build_record(shelter: dict, wheelchair_unaccompanied: str, ramp_unlocked_24_7: str,
                 generator_powers_medical_outlets: str, notes: str, assessor_role: str,
                 session_id: str | None) -> dict:
    """Validate the answers and build the insert parameters. Raises ValueError on a bad answer."""
    if wheelchair_unaccompanied not in ANSWERS:
        raise ValueError(f"wheelchair_unaccompanied must be one of {ANSWERS}")
    for name, value in (("ramp_unlocked_24_7", ramp_unlocked_24_7),
                        ("generator_powers_medical_outlets", generator_powers_medical_outlets)):
        if value not in OPTIONAL_ANSWERS:
            raise ValueError(f"{name} must be one of {OPTIONAL_ANSWERS}")
    notes = (notes or "").strip()
    if not notes:
        raise ValueError("notes must record what the operator said, in their words")
    return {
        "assessment_id": f"asm_{uuid.uuid4().hex[:12]}",
        "shelter_id": str(shelter["shelter_id"]),
        "shelter_name": shelter.get("name"),
        "county": shelter.get("county_fips"),
        "operator_notes": notes[:NOTES_MAX],
        "wheelchair": _to_bool(wheelchair_unaccompanied),
        "ramp": _to_bool(ramp_unlocked_24_7),
        "generator": _to_bool(generator_powers_medical_outlets),
        "verified_status": VERIFIED_STATUS[wheelchair_unaccompanied],
        "assessor_role": (assessor_role or "shelter operator").strip()[:80],
        "session_id": session_id,
        "source": SOURCE,
    }


def insert(params: dict) -> None:
    bq.run("16_insert_assessment.sql", **params)
