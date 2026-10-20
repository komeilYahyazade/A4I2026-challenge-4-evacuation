"""Test helpers. make_row() refuses keys the SQL does not return, so fixtures cannot drift from
the queries the way the original brief test did."""

from typing import Any, Callable
from unittest import mock

from agent.config import AgentConfig

CFG = AgentConfig(project_id="demo-project")

FAKE_COLUMNS = {
    "power_dependent_counties": frozenset({"fips_code", "power_dependent_dme", "power_dependent_is_censored"}),
    "care_facilities": frozenset({
        "cms_certification_number_ccn", "provider_name", "citytown", "number_of_certified_beds",
        "latitude", "longitude",
    }),
}
FAKE_ACS_COLUMNS = frozenset({
    "geo_id", "total_pop", "black_pop", "hispanic_pop", "white_pop", "asian_pop", "amerindian_pop",
    "poverty", "pop_determined_poverty_status",
})


def capture_sql(call: Callable[[Any], Any]) -> list[tuple[str, dict[str, Any]]]:
    """Run a data.py fetcher with BigQuery replaced, returning every (sql, params) it would send."""
    from agent import data

    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run(sql: str, params: Any = None, cfg: Any = None) -> list[dict[str, Any]]:
        calls.append((sql, dict(params or {})))
        return []

    def fake_columns(cfg: Any, table: str, dataset_path: Any = None) -> frozenset[str]:
        return FAKE_COLUMNS.get(table, FAKE_ACS_COLUMNS)

    data.clear_caches()  # cached lookups would otherwise skip the query we want to capture
    with mock.patch.object(data, "run", fake_run), \
         mock.patch.object(data, "columns", fake_columns), \
         mock.patch.object(data, "newest_acs_table", lambda cfg: "censustract_2021_5yr"):
        call(data)
    data.clear_caches()
    return calls


def output_columns(sql: str) -> list[str]:
    import sqlglot

    return list(sqlglot.parse_one(sql, read="bigquery").named_selects)


def columns_of(call: Callable[[Any], Any]) -> list[str]:
    return output_columns(capture_sql(call)[0][0])


def make_row(columns: list[str], **values: Any) -> dict[str, Any]:
    unknown = set(values) - set(columns)
    assert not unknown, f"fixture uses columns the SQL does not return: {sorted(unknown)}"
    return {c: values.get(c) for c in columns}
