"""Run the files in sql/ with named parameters. Tools and the API's data routes share this."""

from __future__ import annotations

import threading
from typing import Any

from google.cloud import bigquery

from readiness_agent import config

# Every named parameter any sql/ file uses, with its BigQuery type, so NULL values still bind.
PARAM_TYPES = {
    "county_fips": "STRING",
    "geo_id": "STRING",
    "shelter_id": "STRING",
    "usage": "STRING",
    "limit": "INT64",
    "reachable_km": "FLOAT64",
    "geo_ids": "ARRAY<STRING>",
    "shelter_ids": "ARRAY<STRING>",
    "place_id": "STRING",
    # field assessments (sql/16_insert_assessment.sql)
    "assessment_id": "STRING",
    "shelter_name": "STRING",
    "county": "STRING",
    "operator_notes": "STRING",
    "wheelchair": "BOOL",
    "ramp": "BOOL",
    "generator": "BOOL",
    "verified_status": "STRING",
    "assessor_role": "STRING",
    "session_id": "STRING",
    "source": "STRING",
}

_client: bigquery.Client | None = None
_lock = threading.Lock()


def client() -> bigquery.Client:
    global _client
    with _lock:
        if _client is None:
            _client = bigquery.Client(project=config.PROJECT, location=config.BQ_LOCATION)
    return _client


def render(name: str) -> str:
    return (config.SQL_DIR / name).read_text().replace("{{dataset}}", f"{config.PROJECT}.{config.DATASET}")


def _param(name: str, value: Any):
    kind = PARAM_TYPES[name]
    if kind.startswith("ARRAY<"):
        return bigquery.ArrayQueryParameter(name, kind[6:-1], list(value or []))
    return bigquery.ScalarQueryParameter(name, kind, value)


def run(name: str, **params: Any) -> list[dict]:
    """Run sql/<name> and return rows as plain dicts (nested STRUCT/ARRAY become dicts/lists)."""
    job = client().query(
        render(name),
        job_config=bigquery.QueryJobConfig(query_parameters=[_param(k, v) for k, v in params.items()]),
    )
    return [dict(row.items()) for row in job.result()]


def run_script(name: str) -> None:
    """Run a multi-statement setup script (DDL)."""
    client().query(render(name)).result()
