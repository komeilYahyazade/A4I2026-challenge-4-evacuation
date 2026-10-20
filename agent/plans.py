"""Versioned plans in readiness_ops.plans, approved by a person, every step in decision_log.

The agent can draft a plan; it cannot approve one. Approval is a REST call a person makes from the
console (agent/server.py), not a tool, so no model output is ever published unreviewed.

Google Maps terms allow keeping only place_id. strip_maps() removes every other Maps-derived field
before anything is written, wherever it sits in the payload; the shelters' place_ids go to
readiness_ops.shelter_place_ids with the verdict and time, and are re-grounded at runtime.

Writes use DML rather than streaming inserts so a draft can be approved (UPDATE) immediately.
"""

import datetime
import json
import uuid
from typing import Any, Optional

from .allocate import ops_table
from .config import AgentConfig

MAPS_KEYS = frozenset({
    "sources",
    "maps_sources",
    "supports",
    "grounding_supports",
    "current_name",
    "verified_name",
    "verified_address",
    "business_status",
    "place_type",
    "notes",
    "live_name",
    "live",
    "live_google_maps",
    "wheelchair_entrance",
    "wheelchair_accessible_entrance",
    "wheelchair_accessible_parking",
    "wheelchair_accessible_restroom",
    "drive_minutes",
    "road_km",
    "drive_basis",
    "closed",
    "confirmed_open",
    "exists",
    "attribution",
    "attribution_html",
    "maps_attribution_html",
    "google_maps_uri",
})

NEXT_VERSION_SQL = "SELECT IFNULL(MAX(version), 0) + 1 AS v FROM ${plans} WHERE county_fips = @county_fips"

INSERT_PLAN_SQL = """
INSERT INTO ${plans} (plan_id, county_fips, version, mode, created_at, created_by, status, plan_json)
VALUES (@plan_id, @county_fips, @version, @mode, CURRENT_TIMESTAMP(), @created_by, 'DRAFT', PARSE_JSON(@plan_json))
"""

SUPERSEDE_SQL = """
UPDATE ${plans} SET status = 'SUPERSEDED'
WHERE county_fips = (SELECT county_fips FROM ${plans} WHERE plan_id = @plan_id)
  AND status = 'APPROVED' AND plan_id != @plan_id
"""

APPROVE_SQL = """
UPDATE ${plans} SET status = 'APPROVED', approved_by = @approved_by, approved_at = CURRENT_TIMESTAMP()
WHERE plan_id = @plan_id AND status = 'DRAFT'
"""

LIST_SQL = """
SELECT plan_id, county_fips, version, mode, created_at, created_by, approved_by, approved_at, status,
       JSON_VALUE(plan_json, '$$.payload_type') AS payload_type,
       JSON_QUERY(plan_json, '$$.totals') AS totals
FROM ${plans}
WHERE county_fips = @county_fips
ORDER BY version DESC
LIMIT 50
"""

LOG_SQL = """
INSERT INTO ${log} (ts, actor, action, detail)
VALUES (CURRENT_TIMESTAMP(), @actor, @action, PARSE_JSON(@detail))
"""

# MERGE on (shelter_id, place_id): a repeat check refreshes verdict and time instead of adding a row.
PLACE_ID_SQL = """
MERGE ${place_ids} AS t
USING (
  SELECT r.shelter_id, r.place_id, CAST(r.verified_at AS TIMESTAMP) AS verified_at, r.verdict
  FROM UNNEST(@rows) AS r
  QUALIFY ROW_NUMBER() OVER (PARTITION BY r.shelter_id, r.place_id ORDER BY r.verified_at DESC) = 1
) AS s
ON t.shelter_id = s.shelter_id AND t.place_id = s.place_id
WHEN MATCHED THEN UPDATE SET verified_at = s.verified_at, verdict = s.verdict
WHEN NOT MATCHED THEN INSERT (shelter_id, place_id, verified_at, verdict)
  VALUES (s.shelter_id, s.place_id, s.verified_at, s.verdict)
"""


def _is_maps_uri(value: Any) -> bool:
    return isinstance(value, str) and ("google.com/maps" in value or "maps.google" in value)


def strip_maps(obj: Any) -> Any:
    """Pure: a deep copy with every Google Maps-derived field removed. place_id survives."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in MAPS_KEYS or _is_maps_uri(v):
                continue
            if k == "accessibility" and isinstance(v, dict) and v.get("label") == "VERIFIED LIVE":
                continue
            out[k] = strip_maps(v)
        return out
    if isinstance(obj, list):
        return [strip_maps(v) for v in obj]
    return obj


def _sql(template: str, cfg: AgentConfig) -> str:
    from .bq import render

    return render(template, plans=ops_table(cfg, "plans"), log=ops_table(cfg, "decision_log"),
                  place_ids=ops_table(cfg, "shelter_place_ids"))


def log(cfg: AgentConfig, actor: str, action: str, detail: dict[str, Any]) -> None:
    from .bq import run

    run(_sql(LOG_SQL, cfg), {"actor": actor, "action": action, "detail": json.dumps(detail)}, cfg)


def save_plan(cfg: AgentConfig, plan: dict[str, Any], created_by: str, mode: str = "PLANNING") -> dict[str, Any]:
    from .bq import run

    clean = strip_maps(plan)
    county = str(clean.get("county_fips") or "")
    version = int(run(_sql(NEXT_VERSION_SQL, cfg), {"county_fips": county}, cfg)[0]["v"])
    plan_id = str(uuid.uuid4())
    run(_sql(INSERT_PLAN_SQL, cfg), {
        "plan_id": plan_id, "county_fips": county, "version": version, "mode": mode,
        "created_by": created_by, "plan_json": json.dumps(clean),
    }, cfg)
    log(cfg, created_by, "PLAN_DRAFTED", {"plan_id": plan_id, "county_fips": county, "version": version,
                                          "payload_type": clean.get("payload_type"), "totals": clean.get("totals")})
    return {"plan_id": plan_id, "county_fips": county, "version": version, "mode": mode, "status": "DRAFT"}


def approve_plan(cfg: AgentConfig, plan_id: str, approved_by: str) -> dict[str, Any]:
    from .bq import run

    if not approved_by.strip():
        raise ValueError("approved_by is required: a named person approves every plan")
    run(_sql(SUPERSEDE_SQL, cfg), {"plan_id": plan_id}, cfg)
    run(_sql(APPROVE_SQL, cfg), {"plan_id": plan_id, "approved_by": approved_by}, cfg)
    log(cfg, approved_by, "PLAN_APPROVED", {"plan_id": plan_id})
    return {"plan_id": plan_id, "status": "APPROVED", "approved_by": approved_by,
            "approved_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()}


def list_plans(cfg: AgentConfig, county_fips: str) -> list[dict[str, Any]]:
    from .bq import run

    rows = run(_sql(LIST_SQL, cfg), {"county_fips": county_fips}, cfg)
    for r in rows:
        if isinstance(r.get("totals"), str):
            r["totals"] = json.loads(r["totals"])
    return rows


def place_id_rows(verification: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: the only Maps fields we may keep, from a shelter_verification.v1 payload."""
    out = []
    for r in verification.get("results", []):
        pid = (r.get("stored_pointer") or {}).get("place_id")
        if pid:
            out.append({"shelter_id": str(r["shelter_id"]), "place_id": pid,
                        "verified_at": (r.get("live") or {}).get("verified_at") or verification.get("verified_at"),
                        "verdict": r.get("verdict")})
    return out


def save_place_ids(cfg: AgentConfig, verification: dict[str, Any]) -> int:
    from google.cloud import bigquery

    from .bq import client

    rows = place_id_rows(verification)
    if not rows:
        return 0
    struct = [bigquery.StructQueryParameter(None, *[bigquery.ScalarQueryParameter(k, "STRING", r[k])
                                                    for k in ("shelter_id", "place_id", "verified_at", "verdict")])
              for r in rows]
    job = client(cfg).query(_sql(PLACE_ID_SQL, cfg), job_config=bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("rows", "STRUCT", struct)]))
    job.result()
    return len(rows)


def maybe_save_place_ids(verification: dict[str, Any], cfg: Optional[AgentConfig] = None) -> int:
    """Best effort: a failed pointer write must never fail the verification the manager is waiting for."""
    try:
        return save_place_ids(cfg or AgentConfig.from_env(), verification)
    except Exception:
        return 0
