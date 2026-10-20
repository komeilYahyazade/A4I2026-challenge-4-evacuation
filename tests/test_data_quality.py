"""Unit and live BigQuery integration tests for agent.data_quality and upgraded agent.queries."""

from __future__ import annotations

import os
import unittest
from typing import Any

import sqlglot
from google.cloud import bigquery

from agent.data_quality import (
    DEFAULT_DATASET,
    DEFAULT_PROJECT,
    apply_county_centroid_fallback,
    audit_capacity_outliers_and_surge_exposure,
    audit_coordinate_sanity_and_counties,
    audit_flag_coverage_and_nulls,
    audit_missing_places_transport,
    audit_shelter_deduplication,
    audit_tract_geometry_vintage_gap,
    build_comprehensive_equity_audit_sql,
    build_defect_1_coordinate_sql,
    build_defect_2_flags_sql,
    build_defect_3_deduplication_sql,
    build_defect_4_vintage_gap_sql,
    build_defect_5_places_transport_sql,
    build_defect_6_outliers_and_surge_sql,
    deduplicate_shelter_records,
    normalize_facility_key,
    run_comprehensive_equity_audit,
    run_full_data_quality_report,
    three_state_flag,
)
from agent.queries import (
    build_care_facilities_proximity_query,
    build_county_list_query,
    build_county_summary_query,
    build_deduplicated_shelters_query,
    build_map_layer_queries,
    build_shelter_assessments_query,
    build_vulnerable_tracts_fallback_query,
)


class _FakeQueryJob:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    def result(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeBQClient:
    def __init__(self, rows_by_substring: dict[str, list[dict[str, Any]]]):
        self._rows_by_substring = rows_by_substring
        self.queries: list[str] = []

    def query(self, sql: str) -> _FakeQueryJob:
        self.queries.append(sql)
        for substr, rows in self._rows_by_substring.items():
            if substr in sql:
                return _FakeQueryJob(rows)
        return _FakeQueryJob([])


class TestDataQualityUnit(unittest.TestCase):
    """Fast, offline unit tests for three-state logic, deduplication, fallbacks, and SQL ASTs."""

    def test_three_state_flag_never_collapses_null_to_no(self) -> None:
        self.assertEqual(three_state_flag(True), "YES")
        self.assertEqual(three_state_flag(False), "NO")
        self.assertEqual(three_state_flag(None), "UNRECORDED")

    def test_normalize_facility_key(self) -> None:
        self.assertEqual(
            normalize_facility_key("   Dunbar High School ", " 3800 Edison Ave "),
            "DUNBAR HIGH SCHOOL|3800 EDISON AVE",
        )
        self.assertEqual(normalize_facility_key(None, None), "|")

    def test_deduplicate_shelter_records_prioritizes_recorded_flags_and_capacity(self) -> None:
        raw = [
            {
                "shelter_id": "102",
                "shelter_name": "Dunbar High School",
                "address_1": "3800 Edison Ave",
                "wheelchair_accessible": None,
                "generator_onsite": None,
                "evacuation_capacity": 500,
                "post_impact_capacity": 500,
            },
            {
                "shelter_id": "101",
                "shelter_name": "DUNBAR HIGH SCHOOL ",
                "address_1": " 3800 edison ave",
                "wheelchair_accessible": True,
                "generator_onsite": True,
                "evacuation_capacity": 1200,
                "post_impact_capacity": 800,
            },
            {
                "shelter_id": "205",
                "shelter_name": "Estero High School",
                "address_1": "21900 River Ranch Rd",
                "wheelchair_accessible": False,
                "generator_onsite": True,
                "evacuation_capacity": 600,
                "post_impact_capacity": 600,
            },
        ]
        res = deduplicate_shelter_records(raw)
        self.assertEqual(res["total_rows"], 3)
        self.assertEqual(res["deduplicated_count"], 2)
        self.assertEqual(res["duplicate_groups"], 1)
        self.assertEqual(res["excess_duplicate_rows"], 1)

        by_id = {r["shelter_id"]: r for r in res["records"]}
        self.assertIn("101", by_id)
        self.assertNotIn("102", by_id)
        self.assertEqual(by_id["101"]["duplicate_group_size"], 2)
        self.assertEqual(by_id["205"]["duplicate_group_size"], 1)

    def test_apply_county_centroid_fallback_rescues_missing_tract_coords(self) -> None:
        tracts = [
            {"geo_id": "12071000100", "county_fips": "12071", "lat": 26.60, "lon": -81.80},
            {"geo_id": "12071000200", "county_fips": "12071", "lat": 26.70, "lon": -81.90},
            {"geo_id": "12071000300", "county_fips": "12071", "lat": None, "lon": None},
            {"geo_id": "12086000100", "county_fips": "12086", "lat": None, "lon": None},
            {"geo_id": "12099000100", "county_fips": "12099", "lat": None, "lon": None},
        ]
        rescued = apply_county_centroid_fallback(
            tracts, county_centroids={"12086": (25.77, -80.21)}
        )
        by_gid = {r["geo_id"]: r for r in rescued}

        self.assertEqual(by_gid["12071000100"]["coord_source"], "TRACT_CENTROID")
        self.assertEqual(by_gid["12071000300"]["coord_source"], "COUNTY_TRACT_AVG_FALLBACK")
        self.assertAlmostEqual(by_gid["12071000300"]["lat"], 26.65, places=4)
        self.assertAlmostEqual(by_gid["12071000300"]["lon"], -81.85, places=4)

        self.assertEqual(by_gid["12086000100"]["coord_source"], "COUNTY_POLYGON_CENTROID_FALLBACK")
        self.assertAlmostEqual(by_gid["12086000100"]["lat"], 25.77, places=4)

        self.assertEqual(by_gid["12099000100"]["coord_source"], "MISSING")

    def test_all_data_quality_and_upgraded_queries_parse_as_valid_bigquery_sql(self) -> None:
        sql_builders = [
            build_defect_1_coordinate_sql("p", "d", "FL"),
            build_defect_2_flags_sql("p", "d"),
            build_defect_3_deduplication_sql("p", "d"),
            build_defect_4_vintage_gap_sql("p", "d", "12"),
            build_defect_5_places_transport_sql("p", "d"),
            build_defect_6_outliers_and_surge_sql("p", "d", 100000),
            build_comprehensive_equity_audit_sql("p", "d", top_n=50),
            build_deduplicated_shelters_query("p", "d"),
            build_county_list_query("p", "d"),
            build_county_summary_query("p", "d"),
            build_care_facilities_proximity_query("p", "d"),
            build_vulnerable_tracts_fallback_query("p", "d"),
            build_shelter_assessments_query("p", "d"),
        ]
        for sql in sql_builders:
            parsed = sqlglot.parse_one(sql, read="bigquery")
            self.assertIsNotNone(parsed)

        map_queries = build_map_layer_queries("p", "d")
        self.assertEqual(set(map_queries.keys()), {"shelters", "tracts", "care_facilities"})
        for layer_sql in map_queries.values():
            self.assertIsNotNone(sqlglot.parse_one(layer_sql, read="bigquery"))

    def test_equity_audit_computes_cohort_deltas_from_query_rows(self) -> None:
        fake_rows = [
            {
                "cohort": "cohort_a_top_vulnerable",
                "tract_count": 50,
                "total_population": 240000,
                "total_households_no_vehicle": 32000,
                "avg_pct_no_vehicle": 28.5,
                "avg_poverty_rate_pct": 29.0,
                "avg_median_income": 34000.0,
                "avg_pct_limited_english": 18.0,
                "avg_svi_socioeconomic": 0.89,
                "avg_svi_household": 0.78,
                "avg_svi_housing_transport": 0.91,
                "avg_pct_black": 45.0,
                "avg_pct_hispanic": 35.0,
            },
            {
                "cohort": "cohort_b_near_recorded_accessible",
                "tract_count": 620,
                "total_population": 2800000,
                "total_households_no_vehicle": 85000,
                "avg_pct_no_vehicle": 7.5,
                "avg_poverty_rate_pct": 14.0,
                "avg_median_income": 58000.0,
                "avg_pct_limited_english": 8.0,
                "avg_svi_socioeconomic": 0.52,
                "avg_svi_household": 0.51,
                "avg_svi_housing_transport": 0.55,
                "avg_pct_black": 18.0,
                "avg_pct_hispanic": 24.0,
            },
            {
                "cohort": "cohort_c_state_baseline",
                "tract_count": 5120,
                "total_population": 21500000,
                "total_households_no_vehicle": 485183,
                "avg_pct_no_vehicle": 6.2,
                "avg_poverty_rate_pct": 13.5,
                "avg_median_income": 61000.0,
                "avg_pct_limited_english": 7.5,
                "avg_svi_socioeconomic": 0.50,
                "avg_svi_household": 0.50,
                "avg_svi_housing_transport": 0.50,
                "avg_pct_black": 16.0,
                "avg_pct_hispanic": 26.0,
            },
        ]
        client = _FakeBQClient({"cohort_a_top_vulnerable": fake_rows})
        audit = run_comprehensive_equity_audit("p", "d", top_n=50, client=client)  # type: ignore[arg-type]
        vs_state = audit["comparisons"]["vulnerable_vs_state_baseline"]
        self.assertAlmostEqual(vs_state["delta_pct_no_vehicle"], 22.3, places=2)
        self.assertAlmostEqual(vs_state["delta_poverty_rate_pct"], 15.5, places=2)
        self.assertAlmostEqual(vs_state["delta_median_income"], -27000.0, places=1)
        self.assertAlmostEqual(vs_state["delta_pct_black"], 29.0, places=2)


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_BQ_TESTS", "1") == "1",
    "Live BigQuery tests skipped when RUN_LIVE_BQ_TESTS=0",
)
class TestDataQualityLiveBigQuery(unittest.TestCase):
    """Live BigQuery integration tests against qwiklabs-gcp-03-7a0c015d2e8a.evacuation_readiness."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project_id = DEFAULT_PROJECT
        cls.dataset = DEFAULT_DATASET
        cls.client = bigquery.Client(project=cls.project_id)

    def test_live_full_data_quality_and_equity_audit_florida(self) -> None:
        report = run_full_data_quality_report(
            project_id=self.project_id,
            dataset=self.dataset,
            top_n=50,
            client=self.client,
        )
        d1 = report["defects"]["defect_1_coordinates_and_counties"]
        self.assertEqual(d1["total_shelters"], 2793)
        self.assertEqual(d1["null_coords"], 0)
        self.assertEqual(d1["distinct_shelter_counties"], 67)
        self.assertEqual(d1["distinct_tract_counties"], 67)

        d2 = report["defects"]["defect_2_unrecorded_flags_and_nulls"]
        self.assertEqual(d2["wc_yes"], 247)
        self.assertEqual(d2["wc_no"], 83)
        self.assertEqual(d2["wc_unrecorded"], 2463)
        self.assertEqual(d2["gen_unrecorded"], 2318)
        self.assertEqual(d2["name_flagged_inactive"], 148)

        d3 = report["defects"]["defect_3_duplicate_shelters"]
        self.assertEqual(d3["total_shelter_rows"], 2793)
        self.assertEqual(d3["deduplicated_facility_count"], 2763)
        self.assertEqual(d3["duplicate_facility_groups"], 25)
        self.assertEqual(d3["excess_duplicate_rows"], 30)

        d4 = report["defects"]["defect_4_tract_geometry_vintage_gap"]
        self.assertEqual(d4["total_2020_svi_tracts"], 5160)
        self.assertEqual(d4["matched_in_2010_tiger_geom"], 3338)
        self.assertEqual(d4["missing_in_2010_tiger_geom"], 1822)
        self.assertEqual(d4["tracts_still_missing_coords"], 0)

        d5 = report["defects"]["defect_5_missing_places_transport"]
        self.assertEqual(d5["total_tracts"], 5160)
        self.assertEqual(d5["pct_lacktrpt_null_tracts"], 5160)
        self.assertEqual(d5["acs_no_vehicle_populated_tracts"], 5160)

        d6 = report["defects"]["defect_6_capacity_outliers_and_surge"]
        self.assertEqual(d6["recorded_in_surge_area"], 58)
        self.assertEqual(d6["recorded_in_100yr_floodplain"], 35)
        self.assertEqual(d6["recorded_in_surge_or_floodplain"], 68)

        eq = report["equity_audit"]
        self.assertIn("cohort_a_top_vulnerable", eq["cohorts"])
        self.assertIn("cohort_b_near_recorded_accessible", eq["cohorts"])
        self.assertIn("cohort_c_state_baseline", eq["cohorts"])
        self.assertEqual(eq["cohorts"]["cohort_a_top_vulnerable"]["tract_count"], 50)
        self.assertGreater(
            eq["comparisons"]["vulnerable_vs_state_baseline"]["delta_pct_no_vehicle"], 10.0
        )

    def test_shelter_assessments_table_exists_with_required_schema(self) -> None:
        table_ref = f"{self.project_id}.{self.dataset}.shelter_assessments"
        table = self.client.get_table(table_ref)
        col_names = {field.name for field in table.schema}
        expected = {
            "shelter_id",
            "shelter_name",
            "county",
            "operator_notes",
            "wheelchair_unaccompanied_entries",
            "ramp_unlocked_24_7",
            "generator_powers_medical_outlets",
            "verified_status",
            "assessed_at",
        }
        self.assertTrue(expected.issubset(col_names), f"Missing columns: {expected - col_names}")


if __name__ == "__main__":
    unittest.main()
