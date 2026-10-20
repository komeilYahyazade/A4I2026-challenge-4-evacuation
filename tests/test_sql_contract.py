"""Every query renders, parses as BigQuery SQL, and binds exactly the parameters it uses."""

import re

import pytest

sqlglot = pytest.importorskip("sqlglot")

from helpers import CFG, capture_sql  # noqa: E402

CALLS = {
    "find_county": lambda d: d.find_county(CFG, "Lee County"),
    "county_shelters": lambda d: d.county_shelters(CFG, "12071"),
    "shelters_by_id": lambda d: d.shelters_by_id(CFG, ["1", "2"]),
    "lookup_shelter": lambda d: d.lookup_shelter(CFG, "school", "12071"),
    "ranked_tracts": lambda d: d.ranked_tracts(CFG, "12071", 5),
    "shelters_for_tract": lambda d: d.shelters_for_tract(CFG, "12071050100", 10),
    "tract_origin": lambda d: d.tract_origin(CFG, "12071050100"),
    "power_dependence": lambda d: d.power_dependence(CFG, "12071"),
    "care_facilities": lambda d: d.care_facilities(CFG, "12071"),
    "equity_audit": lambda d: d.equity_audit(CFG, "12071", ["12071050100"]),
    "site_hazard": lambda d: d.site_hazard(CFG, ["344980", "345200"]),
    "county_totals": lambda d: d.county_totals(CFG, "12071"),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_query_parses_and_binds_every_parameter(name):
    calls = capture_sql(CALLS[name])
    assert calls, f"{name} sent no query"
    for sql, params in calls:
        assert "${" not in sql
        used = set(re.findall(r"@([A-Za-z_]\w*)", sql))
        assert used <= set(params), f"{name}: unbound parameters {used - set(params)}"
        assert set(params) <= used, f"{name}: unused parameters {set(params) - used}"
        sqlglot.parse_one(sql, read="bigquery")


def test_values_never_reach_sql_text():
    calls = capture_sql(lambda d: d.lookup_shelter(CFG, "x' OR TRUE --", "12071"))
    sql, params = calls[0]
    assert "OR TRUE" not in sql
    assert params["name"] == "x' OR TRUE --"


def test_flags_stay_three_state_in_every_shelter_query():
    for name in ("county_shelters", "shelters_by_id", "lookup_shelter", "shelters_for_tract"):
        sql = capture_sql(CALLS[name])[0][0]
        for flag in ("wheelchair_accessible", "generator_onsite", "in_surge_slosh_area", "in_100_yr_floodplain"):
            assert f"IFNULL(s.{flag}" not in sql, f"{name} collapses {flag}"
        assert "'UNRECORDED'" in sql
        assert "AS capacity_status" in sql
        assert "s.evacuation_capacity < 0 THEN 'UNRECORDED'" in sql, f"{name} counts a negative capacity"
        assert r"r'\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b')" in sql
        assert "IFNULL(s.usage IN UNNEST(@pre_landfall_usage), FALSE) AS is_pre_landfall" in sql


def test_ranking_inputs_exclude_accessibility_race_and_composites():
    sql = capture_sql(CALLS["ranked_tracts"])[0][0]
    inputs = sql[sql.index("-- ranking inputs start"):sql.index("-- ranking inputs end")]
    switch = sql[sql.index("CASE @ranking"):sql.index("END AS priority_score")]
    for part in (inputs, switch):
        for forbidden in ("wheelchair", "RISK_SCORE", "RPL_THEMES", "black", "hispanic", "white", "generator"):
            assert forbidden not in part
    assert "households_no_vehicle" in switch and "may_need_help_score" in switch
    assert "org_poc" not in sql


def test_ranking_choice_is_validated_and_bound():
    import pytest as _pytest

    from agent import data

    with _pytest.raises(ValueError):
        data.ranked_tracts(CFG, "12071", 5, "by_paperwork")
    for ranking in ("no_vehicle", "may_need_help", "composite_v0"):
        _, params = capture_sql(lambda d, r=ranking: d.ranked_tracts(CFG, "12071", 5, r))[0]
        assert params["ranking"] == ranking


def test_race_appears_only_in_the_audit():
    for name, call in CALLS.items():
        sql = " ".join(s for s, _ in capture_sql(call))
        if name == "equity_audit":
            assert "black_pop" in sql
        else:
            assert "black_pop" not in sql and "hispanic_pop" not in sql


def test_geometry_lookups_are_cached():
    from unittest import mock

    from agent import data

    calls = []
    data.clear_caches()
    with mock.patch.object(data, "run", lambda sql, params=None, cfg=None: calls.append(sql) or [{"x": 1}]):
        first = data.find_county(CFG, "Lee")
        first[0]["x"] = 99  # mutating the copy must not poison the cache
        second = data.find_county(CFG, "Lee")
    data.clear_caches()
    assert len(calls) == 1 and second == [{"x": 1}]
