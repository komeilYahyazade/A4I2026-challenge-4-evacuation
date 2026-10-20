"""BigQuery access: one client, real query parameters, and column discovery.

Values always travel as query parameters. Identifiers (table and column names) cannot be
parameters in BigQuery, so they come only from validated config or from INFORMATION_SCHEMA.
"""

import datetime
import decimal
import string
import threading
from functools import lru_cache
from typing import Any, Iterable, Optional, Sequence

from .config import AgentConfig

_lock = threading.Lock()
_clients: dict[tuple[str, str], Any] = {}

ACS_DATASET = "bigquery-public-data.census_bureau_acs"
COUNTIES_TABLE = "`bigquery-public-data.geo_us_boundaries.counties`"


def client(cfg: AgentConfig) -> Any:
    from google.cloud import bigquery

    key = (cfg.project_id, cfg.bq_location)
    with _lock:
        if key not in _clients:
            _clients[key] = bigquery.Client(project=cfg.project_id or None, location=cfg.bq_location)
        return _clients[key]


def _param(name: str, value: Any) -> Any:
    from google.cloud import bigquery

    if isinstance(value, (list, tuple)):
        items = list(value)
        kind = "STRING"
        if items and all(isinstance(v, bool) for v in items):
            kind = "BOOL"
        elif items and all(isinstance(v, int) and not isinstance(v, bool) for v in items):
            kind = "INT64"
        elif items and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in items):
            kind = "FLOAT64"
        else:
            items = [str(v) for v in items]
        return bigquery.ArrayQueryParameter(name, kind, items)
    if isinstance(value, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", value)
    if isinstance(value, int):
        return bigquery.ScalarQueryParameter(name, "INT64", value)
    if isinstance(value, float):
        return bigquery.ScalarQueryParameter(name, "FLOAT64", value)
    return bigquery.ScalarQueryParameter(name, "STRING", None if value is None else str(value))


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def run(sql: str, params: Optional[dict[str, Any]] = None, cfg: Optional[AgentConfig] = None) -> list[dict[str, Any]]:
    from google.cloud import bigquery

    cfg = cfg or AgentConfig.from_env()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[_param(k, v) for k, v in (params or {}).items()]
    )
    rows = client(cfg).query(sql, job_config=job_config).result()
    return [{k: _plain(v) for k, v in row.items()} for row in rows]


def render(template: str, **subs: str) -> str:
    """Fill ${name} placeholders. Raises on a missing one rather than shipping broken SQL."""
    return string.Template(template).substitute(**subs)


@lru_cache(maxsize=64)
def _columns_cached(project: str, location: str, dataset_path: str, table: str) -> frozenset[str]:
    cfg = AgentConfig(project_id=project, bq_location=location)
    rows = run(
        f"SELECT column_name FROM `{dataset_path}.INFORMATION_SCHEMA.COLUMNS` WHERE table_name = @t",
        {"t": table},
        cfg,
    )
    return frozenset(r["column_name"].lower() for r in rows)


def columns(cfg: AgentConfig, table: str, dataset_path: Optional[str] = None) -> frozenset[str]:
    path = dataset_path or (f"{cfg.project_id}.{cfg.dataset}" if cfg.project_id else cfg.dataset)
    return _columns_cached(cfg.project_id, cfg.bq_location, path, table)


def pick(available: Iterable[str], *candidates: str) -> Optional[str]:
    """First candidate column that exists. Source releases rename columns; never assume one."""
    have = {c.lower() for c in available}
    for cand in candidates:
        if cand.lower() in have:
            return cand.lower()
    return None


@lru_cache(maxsize=4)
def _newest_acs_table(project: str, location: str) -> str:
    cfg = AgentConfig(project_id=project, bq_location=location)
    rows = run(
        f"SELECT table_name FROM `{ACS_DATASET}.INFORMATION_SCHEMA.TABLES` "
        "WHERE table_name LIKE 'censustract%' ORDER BY table_name DESC LIMIT 1",
        None,
        cfg,
    )
    if not rows:
        raise RuntimeError("No census tract tables found in bigquery-public-data.census_bureau_acs")
    return rows[0]["table_name"]


def newest_acs_table(cfg: AgentConfig) -> str:
    """Same rule the notebook used to build vulnerability_tracts, so the vintages match."""
    return _newest_acs_table(cfg.project_id, cfg.bq_location)


def require(column: Optional[str], what: str, table: str, available: Sequence[str]) -> str:
    if column is None:
        raise RuntimeError(
            f"{table} has no {what} column. Columns present: {sorted(available)[:40]}"
        )
    return column
