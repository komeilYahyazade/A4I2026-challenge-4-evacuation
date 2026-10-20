"""Conversational Shelter Assessment Sub-Agent (Closing the 85% "Unrecorded" Gap).

Over 85% of Florida shelters in the FEMA National Shelter System have UNRECORDED
(NULL) values for wheelchair accessibility (`wheelchair_accessible`) and backup
generators (`generator_onsite`). Even when an operator says "we have a ramp",
real-world shelters frequently fail wheelchair users when the ramp is behind a
padlocked gate after hours, requires calling a custodian, or relies on a broken
vertical platform lift.

This module evaluates conversational statements from shelter operators, catches
the "chained side-door ramp / broken lift / custodian escort" trap, generates
targeted follow-up questions when operational readiness is ambiguous, and
persists verified assessments to BigQuery (`evacuation_readiness.shelter_assessments`).
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Mapping, Optional

from google.cloud import bigquery

from .grounding import parse_json_block

VERIFIED_ACCESSIBLE = "VERIFIED_ACCESSIBLE"
VERIFIED_INACCESSIBLE_OR_RESTRICTED = "VERIFIED_INACCESSIBLE_OR_RESTRICTED"
NEEDS_FOLLOWUP_QUESTION = "NEEDS_FOLLOWUP_QUESTION"

_RESTRICTION_PATTERNS = re.compile(
    r"\b("
    r"padlock(?:ed)?|chained|locked\s+(?:after|gate|door|unless|at\s+night)|"
    r"call(?:ed|ing)?\s+(?:the\s+)?(?:custodian|janitor|staff|maintenance|security|office)|"
    r"custodian\s+is\s+called|requires?\s+(?:an?\s+)?escort|must\s+call\s+ahead|"
    r"lift\s+is\s+(?:broken|out\s+of\s+(?:order|service)|down|inoperable)|"
    r"broken\s+(?:wheelchair\s+)?lift|no\s+ramp|steps\s+only|stairs\s+only|"
    r"not\s+wheelchair\s+accessible|inaccessible|cannot\s+enter\s+unaccompanied|"
    r"key\s+is\s+kept|need\s+someone\s+to\s+unlock|after\s+\d+\s*(?:pm|p\.m\.)"
    r")\b",
    re.I,
)

_UNACCOMPANIED_POSITIVE_PATTERNS = re.compile(
    r"\b("
    r"unaccompanied|unlocked\s+24\s*/?\s*7|open\s+24\s*/?\s*7|24\s+hours|"
    r"automatic\s+doors?\s+unlocked|level\s+entry\s+unlocked|any\s+hour\s+without\s+assistance|"
    r"enter\s+independently\s+24\s*/?\s*7|always\s+unlocked|no\s+locks?\s+or\s+gates?"
    r")\b",
    re.I,
)

_GENERATOR_RESTRICTED_PATTERNS = re.compile(
    r"\b("
    r"only\s+(?:runs?|powers?)\s+(?:the\s+)?(?:hallway\s+)?lights|"
    r"lights\s+only|emergency\s+lighting\s+only|"
    r"not\s+(?:hvac|air\s+conditioning|medical\s+outlets|wall\s+outlets|receptacles)|"
    r"does\s+not\s+(?:run|power)\s+(?:hvac|medical|outlets)|"
    r"no\s+(?:backup\s+)?generator|generator\s+is\s+(?:broken|out\s+of\s+service|down)"
    r")\b",
    re.I,
)

_GENERATOR_POSITIVE_PATTERNS = re.compile(
    r"\b("
    r"powers?\s+(?:all\s+)?(?:hvac\s+and\s+)?medical\s+(?:outlets|equipment|devices)|"
    r"medical\s+outlets\s+(?:and\s+hvac\s+)?are\s+powered|"
    r"runs?\s+(?:both\s+)?hvac\s+and\s+(?:all\s+)?medical\s+outlets|"
    r"dedicated\s+generator\s+circuits?\s+for\s+medical|"
    r"powers?\s+dme|full\s+facility\s+backup\s+power\s+including\s+medical\s+outlets"
    r")\b",
    re.I,
)


def _resolve_project_id(project_id: Optional[str] = None) -> str:
    cand = (project_id or "").strip() or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not cand or cand == "demo-project":
        return "qwiklabs-gcp-03-7a0c015d2e8a"
    return cand


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _deterministic_rule_eval(
    shelter_name: str, county: str, statement: str
) -> dict[str, Any]:
    """Deterministic safety guardrails so locked ramps or broken lifts are never marked accessible."""
    text = (statement or "").strip()

    has_restriction = bool(_RESTRICTION_PATTERNS.search(text))
    has_unaccompanied_pos = bool(_UNACCOMPANIED_POSITIVE_PATTERNS.search(text))
    has_gen_restriction = bool(_GENERATOR_RESTRICTED_PATTERNS.search(text))
    has_gen_pos = bool(_GENERATOR_POSITIVE_PATTERNS.search(text))

    wheelchair_unaccompanied: Optional[bool]
    ramp_24_7: Optional[bool]
    if has_restriction:
        wheelchair_unaccompanied = False
        ramp_24_7 = False
    elif has_unaccompanied_pos:
        wheelchair_unaccompanied = True
        ramp_24_7 = True
    else:
        wheelchair_unaccompanied = None
        ramp_24_7 = None

    gen_medical: Optional[bool]
    if has_gen_restriction:
        gen_medical = False
    elif has_gen_pos:
        gen_medical = True
    else:
        gen_medical = None

    if wheelchair_unaccompanied is False:
        status = VERIFIED_INACCESSIBLE_OR_RESTRICTED
    elif wheelchair_unaccompanied is True and ramp_24_7 is True:
        status = VERIFIED_ACCESSIBLE
    else:
        status = NEEDS_FOLLOWUP_QUESTION

    if status == VERIFIED_INACCESSIBLE_OR_RESTRICTED:
        gen_note = (
            " Additionally, the backup generator does NOT power medical outlets or HVAC, so electricity-dependent "
            "residents (oxygen concentrators, dialysis, CPAP, motorized wheelchairs) cannot rely on this site."
            if gen_medical is False
            else ""
        )
        followup = (
            f"Can the padlock/access restriction at {shelter_name} be permanently removed during storm activation "
            "so wheelchair users have 24/7 unaccompanied entry at the main entrance without calling a custodian?"
            + (
                " Also, can portable generator circuits be wired to medical outlets before hurricane season?"
                if gen_medical is not True
                else ""
            )
        )
        impact = (
            f"Do NOT count {shelter_name} ({county} County) as a verified wheelchair-accessible pre-landfall destination. "
            "Because the ramp or lift is locked/restricted or requires staff escort, a wheelchair user arriving at "
            "night cannot enter unaccompanied." + gen_note
        )
    elif status == VERIFIED_ACCESSIBLE:
        if gen_medical is False:
            followup = (
                f"Confirmed 24/7 unaccompanied wheelchair entry at {shelter_name}. Note: backup generator does not "
                "power medical outlets; do not route power-dependent DME patients here without backup power."
            )
            impact = (
                f"Upgrades {shelter_name} ({county} County) from UNRECORDED to VERIFIED_ACCESSIBLE for physical "
                "wheelchair entry (24/7 unlocked unaccompanied access), reducing the county's unassessed shelter gap, "
                "but flags it as unsuitable for electricity-dependent medical equipment."
            )
        elif gen_medical is True:
            followup = (
                f"Verification complete for {shelter_name}: 24/7 unaccompanied wheelchair entry and generator-backed "
                "medical outlets are both confirmed."
            )
            impact = (
                f"Upgrades {shelter_name} ({county} County) from UNRECORDED to VERIFIED_ACCESSIBLE with confirmed 24/7 "
                "unaccompanied wheelchair entry and backup generator power for medical outlets and HVAC."
            )
        else:
            followup = (
                f"Unaccompanied 24/7 wheelchair entry is confirmed at {shelter_name}. Follow-up needed on power: "
                "does the onsite backup generator power wall outlets in the shelter sleeping area for medical "
                "equipment (oxygen concentrators, CPAP, power wheelchairs) and HVAC, or only emergency lights?"
            )
            impact = (
                f"Upgrades {shelter_name} ({county} County) to VERIFIED_ACCESSIBLE for physical wheelchair entry, "
                "while keeping medical-power capability marked for follow-up."
            )
    else:
        missing_parts = []
        if wheelchair_unaccompanied is None:
            missing_parts.append(
                "whether a wheelchair user can enter completely unaccompanied 24/7 without calling a custodian, "
                "unlocking a gate, or using a key-operated lift"
            )
        if gen_medical is None:
            missing_parts.append(
                "whether the backup generator powers wall outlets for medical equipment (DME) and HVAC or only "
                "hallway/emergency lighting"
            )
        followup = (
            f"To verify {shelter_name} before hurricane season, please clarify: "
            + " and ".join(missing_parts)
            + "?"
        )
        impact = (
            f"Keeps {shelter_name} ({county} County) in NEEDS_FOLLOWUP_QUESTION status (not counted as accessible) "
            "until the operator confirms 24/7 unaccompanied wheelchair entry and generator load coverage."
        )

    return {
        "wheelchair_unaccompanied_entries": wheelchair_unaccompanied,
        "ramp_unlocked_24_7": ramp_24_7,
        "generator_powers_medical_outlets": gen_medical,
        "verified_status": status,
        "followup_question": followup,
        "impact_on_readiness": impact,
    }


def evaluate_operator_statement(
    shelter_id: str,
    shelter_name: str,
    county: str,
    operator_statement: str,
    use_llm: bool = True,
    project_id: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluate a shelter operator's statement for real operational wheelchair & medical power readiness.

    Catches the 'chained side-door ramp / broken lift / custodian escort' trap where a building has
    a nominal ramp or lift on paper, but a wheelchair user arriving at 2 AM cannot enter unaccompanied.

    Returns a dictionary containing:
    - shelter_id, shelter_name, county, operator_statement, assessed_at
    - wheelchair_unaccompanied_entries: True | False | None
    - ramp_unlocked_24_7: True | False | None
    - generator_powers_medical_outlets: True | False | None
    - verified_status: "VERIFIED_ACCESSIBLE" | "VERIFIED_INACCESSIBLE_OR_RESTRICTED" | "NEEDS_FOLLOWUP_QUESTION"
    - followup_question: probing follow-up question or confirmation summary
    - impact_on_readiness: plain-English explanation of how this changes the county's readiness plan
    """
    rule_eval = _deterministic_rule_eval(shelter_name, county, operator_statement)
    llm_eval: dict[str, Any] = {}

    if use_llm:
        try:
            from google import genai

            proj = _resolve_project_id(project_id)
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
            client = genai.Client(vertexai=True, project=proj, location=location)
            prompt = (
                "You are an evacuation shelter accessibility auditor for a county emergency manager in Florida.\n"
                f"Shelter: {shelter_name} (ID: {shelter_id}, County: {county})\n"
                f"Operator statement: {operator_statement!r}\n\n"
                "Evaluate strictly:\n"
                "1. Can a wheelchair user enter UNACCOMPANIED at any hour (24/7) without calling a custodian, "
                "waiting for a key, or facing a padlocked/chained gate or broken lift?\n"
                "   - If the ramp/gate is locked after hours, chained, padlocked, requires calling staff/custodian, "
                "or the lift is broken, wheelchair_unaccompanied_entries MUST be false and ramp_unlocked_24_7 MUST be false.\n"
                "   - Only if 24/7 unlocked, unaccompanied level/ramp entry is explicitly confirmed, set both to true.\n"
                "   - If unclear or unstated, set to null.\n"
                "2. Does the backup generator power medical outlets / HVAC (true), only lights / no generator (false), "
                "or is it unstated (null)?\n"
                "3. Set verified_status to one of:\n"
                '   - "VERIFIED_INACCESSIBLE_OR_RESTRICTED" (if wheelchair_unaccompanied_entries is false)\n'
                '   - "VERIFIED_ACCESSIBLE" (if wheelchair_unaccompanied_entries is true and ramp_unlocked_24_7 is true)\n'
                '   - "NEEDS_FOLLOWUP_QUESTION" (if wheelchair_unaccompanied_entries is null)\n'
                "Reply ONLY with a fenced JSON block:\n"
                "```json\n"
                "{\n"
                '  "wheelchair_unaccompanied_entries": true | false | null,\n'
                '  "ramp_unlocked_24_7": true | false | null,\n'
                '  "generator_powers_medical_outlets": true | false | null,\n'
                '  "verified_status": "VERIFIED_ACCESSIBLE" | "VERIFIED_INACCESSIBLE_OR_RESTRICTED" | "NEEDS_FOLLOWUP_QUESTION",\n'
                '  "followup_question": "string",\n'
                '  "impact_on_readiness": "string"\n'
                "}\n"
                "```"
            )
            for model_name in ("gemini-3.6-flash", "gemini-2.5-flash"):
                try:
                    resp = client.models.generate_content(model=model_name, contents=prompt)
                    parsed = parse_json_block(getattr(resp, "text", None))
                    if isinstance(parsed, dict):
                        llm_eval = parsed
                        break
                except Exception:
                    continue
        except Exception:
            llm_eval = {}

    # Merge LLM output with deterministic safety guardrails:
    # A deterministic False (locked gate, padlock, custodian required, lights-only generator) always wins.
    wc_unacc = rule_eval["wheelchair_unaccompanied_entries"]
    if wc_unacc is None and isinstance(llm_eval.get("wheelchair_unaccompanied_entries"), bool):
        # Only allow LLM True if deterministic patterns also saw explicit 24/7 unaccompanied confirmation
        if llm_eval["wheelchair_unaccompanied_entries"] is False:
            wc_unacc = False

    ramp_24_7 = rule_eval["ramp_unlocked_24_7"]
    if ramp_24_7 is None and isinstance(llm_eval.get("ramp_unlocked_24_7"), bool):
        if llm_eval["ramp_unlocked_24_7"] is False:
            ramp_24_7 = False

    gen_med = rule_eval["generator_powers_medical_outlets"]
    if gen_med is None and isinstance(llm_eval.get("generator_powers_medical_outlets"), bool):
        gen_med = llm_eval["generator_powers_medical_outlets"]

    if wc_unacc is False or ramp_24_7 is False:
        status = VERIFIED_INACCESSIBLE_OR_RESTRICTED
        wc_unacc = False
        ramp_24_7 = False
    elif wc_unacc is True and ramp_24_7 is True:
        status = VERIFIED_ACCESSIBLE
    else:
        status = NEEDS_FOLLOWUP_QUESTION

    followup_q = str(llm_eval.get("followup_question") or "").strip() or rule_eval["followup_question"]
    impact_txt = str(llm_eval.get("impact_on_readiness") or "").strip() or rule_eval["impact_on_readiness"]

    return {
        "shelter_id": str(shelter_id),
        "shelter_name": str(shelter_name),
        "county": str(county),
        "operator_statement": str(operator_statement),
        "wheelchair_unaccompanied_entries": wc_unacc,
        "ramp_unlocked_24_7": ramp_24_7,
        "generator_powers_medical_outlets": gen_med,
        "verified_status": status,
        "followup_question": followup_q,
        "impact_on_readiness": impact_txt,
        "assessed_at": _now_iso(),
    }


_ASSESSMENTS_SCHEMA = [
    bigquery.SchemaField("shelter_id", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("shelter_name", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("county", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("operator_notes", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("wheelchair_unaccompanied_entries", "BOOLEAN", mode="NULLABLE"),
    bigquery.SchemaField("ramp_unlocked_24_7", "BOOLEAN", mode="NULLABLE"),
    bigquery.SchemaField("generator_powers_medical_outlets", "BOOLEAN", mode="NULLABLE"),
    bigquery.SchemaField("verified_status", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("assessed_at", "TIMESTAMP", mode="NULLABLE"),
]


def save_shelter_assessment_to_bq(
    assessment_record: Mapping[str, Any],
    project_id: Optional[str] = None,
    dataset: str = "evacuation_readiness",
    table_name: str = "shelter_assessments",
) -> dict[str, Any]:
    """Persist a verified shelter operator assessment to BigQuery `evacuation_readiness.shelter_assessments`.

    Creates the table if it does not yet exist and inserts the assessment row so county readiness
    audits reflect operator-verified wheelchair and generator status in real time.
    """
    proj = _resolve_project_id(project_id)
    client = bigquery.Client(project=proj)
    table_id = f"{proj}.{dataset}.{table_name}"

    table_ref = bigquery.Table(table_id, schema=_ASSESSMENTS_SCHEMA)
    client.create_table(table_ref, exists_ok=True)

    assessed_at = str(assessment_record.get("assessed_at") or _now_iso())
    operator_notes = str(
        assessment_record.get("operator_statement")
        or assessment_record.get("operator_notes")
        or ""
    )
    row = {
        "shelter_id": str(assessment_record.get("shelter_id") or ""),
        "shelter_name": str(assessment_record.get("shelter_name") or ""),
        "county": str(assessment_record.get("county") or ""),
        "operator_notes": operator_notes,
        "operator_statement": operator_notes,
        "wheelchair_unaccompanied_entries": assessment_record.get("wheelchair_unaccompanied_entries"),
        "ramp_unlocked_24_7": assessment_record.get("ramp_unlocked_24_7"),
        "generator_powers_medical_outlets": assessment_record.get("generator_powers_medical_outlets"),
        "verified_status": str(assessment_record.get("verified_status") or NEEDS_FOLLOWUP_QUESTION),
        "followup_question": str(assessment_record.get("followup_question") or ""),
        "impact_on_readiness": str(assessment_record.get("impact_on_readiness") or ""),
        "assessed_at": assessed_at,
    }

    sql = f"""
    INSERT INTO `{table_id}` (
      shelter_id, shelter_name, county, operator_notes,
      wheelchair_unaccompanied_entries, ramp_unlocked_24_7, generator_powers_medical_outlets,
      verified_status, assessed_at
    ) VALUES (
      @shelter_id, @shelter_name, @county, @operator_notes,
      @wheelchair_unaccompanied_entries, @ramp_unlocked_24_7, @generator_powers_medical_outlets,
      @verified_status, TIMESTAMP(@assessed_at)
    )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("shelter_id", "STRING", row["shelter_id"]),
            bigquery.ScalarQueryParameter("shelter_name", "STRING", row["shelter_name"]),
            bigquery.ScalarQueryParameter("county", "STRING", row["county"]),
            bigquery.ScalarQueryParameter("operator_notes", "STRING", row["operator_notes"]),
            bigquery.ScalarQueryParameter(
                "wheelchair_unaccompanied_entries", "BOOL", row["wheelchair_unaccompanied_entries"]
            ),
            bigquery.ScalarQueryParameter("ramp_unlocked_24_7", "BOOL", row["ramp_unlocked_24_7"]),
            bigquery.ScalarQueryParameter(
                "generator_powers_medical_outlets", "BOOL", row["generator_powers_medical_outlets"]
            ),
            bigquery.ScalarQueryParameter("verified_status", "STRING", row["verified_status"]),
            bigquery.ScalarQueryParameter("assessed_at", "STRING", row["assessed_at"]),
        ]
    )
    client.query(sql, job_config=job_config).result()

    return {
        "saved": True,
        "table_id": table_id,
        "shelter_id": row["shelter_id"],
        "verified_status": row["verified_status"],
        "assessed_at": assessed_at,
        "record": row,
    }


def get_shelter_assessments(
    county: Optional[str] = None,
    shelter_id: Optional[str] = None,
    project_id: Optional[str] = None,
    dataset: str = "evacuation_readiness",
    table_name: str = "shelter_assessments",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch persisted shelter operator assessments from BigQuery."""
    proj = _resolve_project_id(project_id)
    client = bigquery.Client(project=proj)
    table_id = f"{proj}.{dataset}.{table_name}"

    try:
        client.get_table(table_id)
    except Exception:
        return []

    sql = f"""
    SELECT
      shelter_id,
      shelter_name,
      county,
      operator_notes,
      operator_notes AS operator_statement,
      wheelchair_unaccompanied_entries,
      ramp_unlocked_24_7,
      generator_powers_medical_outlets,
      verified_status,
      CAST(assessed_at AS STRING) AS assessed_at
    FROM `{table_id}`
    WHERE (@county = '' OR LOWER(county) LIKE CONCAT('%', LOWER(@county), '%'))
      AND (@shelter_id = '' OR shelter_id = @shelter_id)
    ORDER BY assessed_at DESC
    LIMIT @limit
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("county", "STRING", (county or "").strip()),
            bigquery.ScalarQueryParameter("shelter_id", "STRING", (shelter_id or "").strip()),
            bigquery.ScalarQueryParameter("limit", "INT64", int(limit)),
        ]
    )
    return [dict(r.items()) for r in client.query(sql, job_config=job_config).result()]
