"""Data Quality & Federal Defect Audit + Comprehensive Equity Audit for Challenge 4.

Surfaces the 6 structural defects in the federal shelter/vulnerability tables (FEMA NSS,
CDC SVI/PLACES, Census TIGER/ACS, FEMA NRI) and runs the 3-cohort Equity Audit on live
BigQuery data while keeping every SQL builder and transformation unit-testable.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from google.cloud import bigquery

_env_proj = os.environ.get("GOOGLE_CLOUD_PROJECT")
DEFAULT_PROJECT = (
    _env_proj if (_env_proj and _env_proj != "demo-project") else "qwiklabs-gcp-03-7a0c015d2e8a"
)
_env_ds = os.environ.get("BQ_DATASET")
DEFAULT_DATASET = (
    _env_ds if (_env_ds and _env_ds not in ("demo_fl", "evacuation_fl")) else "evacuation_readiness"
)

# State bounding boxes used for Defect 1 coordinate sanity checks.
STATE_BBOXES: dict[str, dict[str, float]] = {
    "FL": {"min_lat": 24.0, "max_lat": 31.5, "min_lon": -88.0, "max_lon": -79.5},
    "TX": {"min_lat": 25.5, "max_lat": 36.6, "min_lon": -106.7, "max_lon": -93.4},
    "LA": {"min_lat": 28.8, "max_lat": 33.1, "min_lon": -94.1, "max_lon": -88.7},
    "NC": {"min_lat": 33.7, "max_lat": 36.6, "min_lon": -84.4, "max_lon": -75.4},
}


def three_state_flag(val: bool | None) -> str:
    """Convert a nullable boolean into its explicit three-state string ('YES', 'NO', 'UNRECORDED').

    Never collapses NULL into FALSE.
    """
    if val is True:
        return "YES"
    if val is False:
        return "NO"
    return "UNRECORDED"


def normalize_facility_key(shelter_name: str | None, address_1: str | None) -> str:
    """Build a normalized deduplication key from shelter name and street address."""
    norm_name = (shelter_name or "").strip().upper()
    norm_addr = (address_1 or "").strip().upper()
    return f"{norm_name}|{norm_addr}"


def deduplicate_shelter_records(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Deduplicate shelter dicts in Python using the same priority rules as SQL.

    Prioritizes rows with recorded wheelchair_accessible, recorded generator_onsite,
    higher capacity, and deterministic shelter_id tie-breaking.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = normalize_facility_key(row.get("shelter_name"), row.get("address_1"))
        groups.setdefault(key, []).append(row)

    def _sort_key(r: dict[str, Any]) -> tuple[int, int, int, str]:
        wc_known = 0 if r.get("wheelchair_accessible") is not None else 1
        gen_known = 0 if r.get("generator_onsite") is not None else 1
        cap = max(int(r.get("evacuation_capacity") or 0), int(r.get("post_impact_capacity") or 0))
        sid = str(r.get("shelter_id") or "")
        return (wc_known, gen_known, -cap, sid)

    deduped: list[dict[str, Any]] = []
    duplicate_groups = 0
    excess_rows = 0
    for key, members in groups.items():
        ordered = sorted(members, key=_sort_key)
        winner = dict(ordered[0])
        winner["duplicate_group_size"] = len(members)
        deduped.append(winner)
        if len(members) > 1:
            duplicate_groups += 1
            excess_rows += len(members) - 1

    return {
        "total_rows": len(rows),
        "deduplicated_count": len(deduped),
        "duplicate_groups": duplicate_groups,
        "excess_duplicate_rows": excess_rows,
        "records": deduped,
    }


def apply_county_centroid_fallback(
    tracts: list[dict[str, Any]],
    county_centroids: dict[str, tuple[float, float]] | None = None,
) -> list[dict[str, Any]]:
    """Populate missing tract (lat, lon) from county-level average centroids or external centroids."""
    by_county: dict[str, list[tuple[float, float]]] = {}
    for t in tracts:
        cfips = str(t.get("county_fips") or "")
        lat, lon = t.get("lat"), t.get("lon")
        if cfips and lat is not None and lon is not None:
            by_county.setdefault(cfips, []).append((float(lat), float(lon)))

    avg_centroids: dict[str, tuple[float, float]] = {}
    for cfips, pts in by_county.items():
        avg_centroids[cfips] = (
            round(sum(p[0] for p in pts) / len(pts), 5),
            round(sum(p[1] for p in pts) / len(pts), 5),
        )

    out: list[dict[str, Any]] = []
    for t in tracts:
        row = dict(t)
        cfips = str(row.get("county_fips") or "")
        if row.get("lat") is not None and row.get("lon") is not None:
            row["coord_source"] = "TRACT_CENTROID"
        elif cfips in avg_centroids:
            row["lat"], row["lon"] = avg_centroids[cfips]
            row["coord_source"] = "COUNTY_TRACT_AVG_FALLBACK"
        elif county_centroids and cfips in county_centroids:
            row["lat"], row["lon"] = county_centroids[cfips]
            row["coord_source"] = "COUNTY_POLYGON_CENTROID_FALLBACK"
        else:
            row["coord_source"] = "MISSING"
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# SQL builders for the 6 Federal Data Quality Defects
# ---------------------------------------------------------------------------


def build_defect_1_coordinate_sql(project_id: str, dataset: str, state: str = "FL") -> str:
    """Defect 1: Coordinate sanity, bounding-box check, and county FIPS spatial coverage."""
    bbox = STATE_BBOXES.get(state.upper(), STATE_BBOXES["FL"])
    ds = f"`{project_id}.{dataset}`"
    return f"""
WITH shelter_stats AS (
  SELECT
    COUNT(*) AS total_shelters,
    COUNTIF(latitude IS NULL OR longitude IS NULL) AS null_coords,
    COUNTIF(latitude = 0 OR longitude = 0) AS zero_coords,
    COUNTIF(
      latitude NOT BETWEEN {bbox['min_lat']} AND {bbox['max_lat']}
      OR longitude NOT BETWEEN {bbox['min_lon']} AND {bbox['max_lon']}
    ) AS out_of_state_bbox,
    COUNTIF(county_fips IS NULL) AS null_county_fips,
    COUNT(DISTINCT county_fips) AS distinct_shelter_counties
  FROM {ds}.shelters
),
tract_stats AS (
  SELECT
    COUNT(*) AS total_tracts,
    COUNTIF(lat IS NULL OR lon IS NULL) AS null_tract_coords,
    COUNT(DISTINCT county_fips) AS distinct_tract_counties
  FROM {ds}.vulnerability_tracts
)
SELECT
  s.*,
  t.total_tracts,
  t.null_tract_coords,
  t.distinct_tract_counties,
  (t.distinct_tract_counties - s.distinct_shelter_counties) AS counties_without_shelters
FROM shelter_stats AS s
CROSS JOIN tract_stats AS t
"""


def build_defect_2_flags_sql(project_id: str, dataset: str) -> str:
    """Defect 2: Unrecorded (NULL) boolean flags & missing capacity across FEMA NSS shelters."""
    ds = f"`{project_id}.{dataset}`"
    return f"""
SELECT
  COUNT(*) AS total_shelters,
  COUNTIF(wheelchair_accessible IS TRUE) AS wc_yes,
  COUNTIF(wheelchair_accessible IS FALSE) AS wc_no,
  COUNTIF(wheelchair_accessible IS NULL) AS wc_unrecorded,
  ROUND(SAFE_DIVIDE(COUNTIF(wheelchair_accessible IS NULL), COUNT(*)) * 100, 2) AS wc_unrecorded_pct,
  COUNTIF(generator_onsite IS TRUE) AS gen_yes,
  COUNTIF(generator_onsite IS FALSE) AS gen_no,
  COUNTIF(generator_onsite IS NULL) AS gen_unrecorded,
  ROUND(SAFE_DIVIDE(COUNTIF(generator_onsite IS NULL), COUNT(*)) * 100, 2) AS gen_unrecorded_pct,
  COUNTIF(self_sufficient_electricity IS TRUE) AS self_elec_yes,
  COUNTIF(self_sufficient_electricity IS FALSE) AS self_elec_no,
  COUNTIF(self_sufficient_electricity IS NULL) AS self_elec_unrecorded,
  COUNTIF(in_surge_slosh_area IS TRUE) AS surge_yes,
  COUNTIF(in_surge_slosh_area IS FALSE) AS surge_no,
  COUNTIF(in_surge_slosh_area IS NULL) AS surge_unrecorded,
  ROUND(SAFE_DIVIDE(COUNTIF(in_surge_slosh_area IS NULL), COUNT(*)) * 100, 2) AS surge_unrecorded_pct,
  COUNTIF(in_100_yr_floodplain IS TRUE) AS flood_yes,
  COUNTIF(in_100_yr_floodplain IS FALSE) AS flood_no,
  COUNTIF(in_100_yr_floodplain IS NULL) AS flood_unrecorded,
  ROUND(SAFE_DIVIDE(COUNTIF(in_100_yr_floodplain IS NULL), COUNT(*)) * 100, 2) AS flood_unrecorded_pct,
  COUNTIF(evacuation_capacity IS NULL OR evacuation_capacity <= 0) AS evac_cap_unrecorded_or_zero,
  COUNTIF(post_impact_capacity IS NULL OR post_impact_capacity <= 0) AS post_cap_unrecorded_or_zero,
  COUNTIF((evacuation_capacity IS NULL OR evacuation_capacity <= 0)
          AND (post_impact_capacity IS NULL OR post_impact_capacity <= 0)) AS both_cap_unrecorded_or_zero,
  COUNTIF(is_medical IS TRUE) AS medical_shelters,
  COUNTIF(REGEXP_CONTAINS(UPPER(IFNULL(shelter_name, '')), r'\\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\\b'))
    AS name_flagged_inactive
FROM {ds}.shelters
"""


def build_defect_3_deduplication_sql(project_id: str, dataset: str) -> str:
    """Defect 3: Duplicate physical facilities across FEMA NSS incident/import rows."""
    ds = f"`{project_id}.{dataset}`"
    return f"""
WITH grouped AS (
  SELECT
    UPPER(TRIM(IFNULL(shelter_name, ''))) AS norm_name,
    UPPER(TRIM(IFNULL(address_1, ''))) AS norm_addr,
    ANY_VALUE(county_name) AS sample_county,
    COUNT(*) AS row_count,
    ARRAY_AGG(CAST(shelter_id AS STRING) ORDER BY shelter_id) AS shelter_ids
  FROM {ds}.shelters
  GROUP BY 1, 2
)
SELECT
  SUM(row_count) AS total_shelter_rows,
  COUNT(*) AS deduplicated_facility_count,
  COUNTIF(row_count > 1) AS duplicate_facility_groups,
  SUM(IF(row_count > 1, row_count - 1, 0)) AS excess_duplicate_rows,
  SUM(IF(row_count > 1, row_count, 0)) AS total_rows_in_duplicate_groups,
  ARRAY_AGG(
    IF(row_count > 1,
       STRUCT(norm_name, norm_addr, sample_county, row_count, shelter_ids),
       NULL)
    IGNORE NULLS
    ORDER BY row_count DESC, norm_name
    LIMIT 10
  ) AS top_duplicate_examples
FROM grouped
"""


def build_defect_4_vintage_gap_sql(project_id: str, dataset: str, state_fips: str = "12") -> str:
    """Defect 4: 2010 vs 2020 Census Tract Geometry Vintage Gap."""
    ds = f"`{project_id}.{dataset}`"
    return f"""
WITH tracts_2010 AS (
  SELECT geo_id
  FROM `bigquery-public-data.geo_census_tracts.us_census_tracts_national`
  WHERE state_fips_code = '{state_fips}'
),
nri_2020 AS (
  SELECT geo_id
  FROM {ds}.hazard_tracts
)
SELECT
  COUNT(*) AS total_2020_svi_tracts,
  COUNTIF(t2010.geo_id IS NOT NULL) AS matched_in_2010_tiger_geom,
  COUNTIF(t2010.geo_id IS NULL) AS missing_in_2010_tiger_geom,
  ROUND(SAFE_DIVIDE(COUNTIF(t2010.geo_id IS NULL), COUNT(*)) * 100, 2) AS missing_in_2010_pct,
  (SELECT COUNT(*) FROM tracts_2010) AS total_2010_tiger_tracts_in_state,
  COUNTIF(nri.geo_id IS NOT NULL) AS matched_in_2020_nri_centroids,
  COUNTIF(v.lat IS NOT NULL AND v.lon IS NOT NULL) AS tracts_with_valid_coords_after_hybrid_join,
  COUNTIF(v.lat IS NULL OR v.lon IS NULL) AS tracts_still_missing_coords
FROM {ds}.vulnerability_tracts AS v
LEFT JOIN tracts_2010 AS t2010 USING (geo_id)
LEFT JOIN nri_2020 AS nri USING (geo_id)
"""


def build_defect_5_places_transport_sql(project_id: str, dataset: str) -> str:
    """Defect 5: Missing CDC PLACES transportation barrier measure (pct_lacktrpt) in Florida."""
    ds = f"`{project_id}.{dataset}`"
    return f"""
SELECT
  COUNT(*) AS total_tracts,
  COUNTIF(pct_lacktrpt IS NULL) AS pct_lacktrpt_null_tracts,
  COUNTIF(pct_lacktrpt IS NOT NULL) AS pct_lacktrpt_populated_tracts,
  ROUND(SAFE_DIVIDE(COUNTIF(pct_lacktrpt IS NULL), COUNT(*)) * 100, 2) AS pct_lacktrpt_null_pct,
  COUNTIF(households_no_vehicle IS NOT NULL) AS acs_no_vehicle_populated_tracts,
  SUM(households_no_vehicle) AS total_households_no_vehicle,
  COUNTIF(pct_mobility IS NOT NULL) AS places_mobility_populated_tracts,
  COUNTIF(pct_indeplive IS NOT NULL) AS places_indeplive_populated_tracts,
  COUNTIF(pct_selfcare IS NOT NULL) AS places_selfcare_populated_tracts
FROM {ds}.vulnerability_tracts
"""


def build_defect_6_outliers_and_surge_sql(
    project_id: str, dataset: str, capacity_max: int = 100000
) -> str:
    """Defect 6: Capacity outliers, surge/floodplain exposure, and NRI tract-level hazard proxy."""
    ds = f"`{project_id}.{dataset}`"
    return f"""
WITH shelter_haz AS (
  SELECT
    s.shelter_id,
    s.shelter_name,
    s.county_name,
    s.usage,
    s.evacuation_capacity,
    s.post_impact_capacity,
    GREATEST(IFNULL(s.evacuation_capacity, 0), IFNULL(s.post_impact_capacity, 0)) AS max_reported_cap,
    s.in_surge_slosh_area,
    s.in_100_yr_floodplain
  FROM {ds}.shelters AS s
)
SELECT
  COUNT(*) AS total_shelters,
  COUNTIF(max_reported_cap > {capacity_max}) AS capacity_outliers_gt_threshold,
  COUNTIF(max_reported_cap > 10000) AS capacity_gt_10k,
  MAX(max_reported_cap) AS max_shelter_capacity,
  COUNTIF(in_surge_slosh_area IS TRUE) AS recorded_in_surge_area,
  COUNTIF(in_100_yr_floodplain IS TRUE) AS recorded_in_100yr_floodplain,
  COUNTIF(in_surge_slosh_area IS TRUE OR in_100_yr_floodplain IS TRUE)
    AS recorded_in_surge_or_floodplain,
  COUNTIF((in_surge_slosh_area IS TRUE OR in_100_yr_floodplain IS TRUE)
          AND usage IN ('EVAC', 'BOTH')) AS pre_landfall_recorded_in_surge_or_floodplain,
  COUNTIF(in_surge_slosh_area IS NULL) AS surge_unrecorded_shelters
FROM shelter_haz
"""


def build_comprehensive_equity_audit_sql(
    project_id: str,
    dataset: str,
    top_n: int = 50,
    state_fips: str = "12",
    accessible_radius_m: float = 5000.0,
) -> str:
    """Build SQL for the 3-Cohort Equity Audit across SVI, income/poverty, language, and ACS race/ethnicity.

    Cohorts:
      - Cohort A ('cohort_a_top_vulnerable'): Top `top_n` tracts prioritized purely by physical/structural
        evacuation need (no-vehicle households + 65+ + disability + mobile homes).
      - Cohort B ('cohort_b_near_recorded_accessible'): Populated tracts within `accessible_radius_m` (5 km)
        of a shelter with `wheelchair_accessible IS TRUE`.
      - Cohort C ('cohort_c_state_baseline'): All populated tracts in the state (`total_pop > 0`).
    """
    ds = f"`{project_id}.{dataset}`"
    return f"""
WITH acs_race AS (
  SELECT
    LPAD(CAST(geo_id AS STRING), 11, '0') AS geo_id,
    SAFE_DIVIDE(black_pop, NULLIF(total_pop, 0)) * 100.0 AS pct_black,
    SAFE_DIVIDE(hispanic_pop, NULLIF(total_pop, 0)) * 100.0 AS pct_hispanic,
    SAFE_DIVIDE(white_pop, NULLIF(total_pop, 0)) * 100.0 AS pct_white_non_hispanic,
    SAFE_DIVIDE(asian_pop, NULLIF(total_pop, 0)) * 100.0 AS pct_asian
  FROM `bigquery-public-data.census_bureau_acs.censustract_2020_5yr`
  WHERE STARTS_WITH(LPAD(CAST(geo_id AS STRING), 11, '0'), '{state_fips}')
),
base_tracts AS (
  SELECT
    v.geo_id,
    v.county_fips,
    v.total_pop,
    v.households,
    v.households_no_vehicle,
    SAFE_DIVIDE(v.households_no_vehicle, NULLIF(v.households, 0)) * 100.0 AS pct_no_vehicle_calc,
    IF(v.poverty_rate < 0, NULL, v.poverty_rate) * 100.0 AS poverty_rate_pct,
    IF(v.median_income < 0, NULL, v.median_income) AS median_income,
    IF(v.pct_limited_english < 0, NULL, v.pct_limited_english) AS pct_limited_english,
    IF(v.pct_age_65_plus < 0, NULL, v.pct_age_65_plus) AS pct_age_65_plus,
    IF(v.pct_disability < 0, NULL, v.pct_disability) AS pct_disability,
    IF(v.pct_mobile_homes < 0, NULL, v.pct_mobile_homes) AS pct_mobile_homes,
    IF(v.svi_socioeconomic < 0, NULL, v.svi_socioeconomic) AS svi_socioeconomic,
    IF(v.svi_household < 0, NULL, v.svi_household) AS svi_household,
    IF(v.svi_housing_transport < 0, NULL, v.svi_housing_transport) AS svi_housing_transport,
    r.pct_black,
    r.pct_hispanic,
    r.pct_white_non_hispanic,
    r.pct_asian,
    ST_GEOGPOINT(v.lon, v.lat) AS g
  FROM {ds}.vulnerability_tracts AS v
  LEFT JOIN acs_race AS r USING (geo_id)
  WHERE v.total_pop > 0 AND v.lat IS NOT NULL AND v.lon IS NOT NULL
),
scored_need AS (
  SELECT
    b.*,
    SAFE_DIVIDE(b.households_no_vehicle - AVG(b.households_no_vehicle) OVER (),
                STDDEV(b.households_no_vehicle) OVER ())
    + SAFE_DIVIDE(b.pct_age_65_plus - AVG(b.pct_age_65_plus) OVER (),
                  STDDEV(b.pct_age_65_plus) OVER ())
    + SAFE_DIVIDE(b.pct_disability - AVG(b.pct_disability) OVER (),
                  STDDEV(b.pct_disability) OVER ())
    + SAFE_DIVIDE(b.pct_mobile_homes - AVG(b.pct_mobile_homes) OVER (),
                  STDDEV(b.pct_mobile_homes) OVER ()) AS structural_need_score
  FROM base_tracts AS b
),
cohort_a AS (
  SELECT *
  FROM scored_need
  ORDER BY households_no_vehicle DESC, structural_need_score DESC, geo_id
  LIMIT {int(top_n)}
),
accessible_shelters AS (
  SELECT ST_GEOGPOINT(longitude, latitude) AS g
  FROM {ds}.shelters
  WHERE wheelchair_accessible IS TRUE
    AND latitude IS NOT NULL AND longitude IS NOT NULL
),
cohort_b_ids AS (
  SELECT DISTINCT b.geo_id
  FROM scored_need AS b
  JOIN accessible_shelters AS a
    ON ST_DWITHIN(b.g, a.g, {float(accessible_radius_m)})
),
cohort_b AS (
  SELECT s.*
  FROM scored_need AS s
  JOIN cohort_b_ids USING (geo_id)
),
combined AS (
  SELECT 'cohort_a_top_vulnerable' AS cohort, * FROM cohort_a
  UNION ALL
  SELECT 'cohort_b_near_recorded_accessible' AS cohort, * FROM cohort_b
  UNION ALL
  SELECT 'cohort_c_state_baseline' AS cohort, * FROM scored_need
)
SELECT
  cohort,
  COUNT(*) AS tract_count,
  SUM(total_pop) AS total_population,
  SUM(households_no_vehicle) AS total_households_no_vehicle,
  ROUND(AVG(pct_no_vehicle_calc), 2) AS avg_pct_no_vehicle,
  ROUND(AVG(poverty_rate_pct), 2) AS avg_poverty_rate_pct,
  ROUND(AVG(median_income), 0) AS avg_median_income,
  ROUND(AVG(pct_limited_english), 2) AS avg_pct_limited_english,
  ROUND(AVG(pct_age_65_plus), 2) AS avg_pct_age_65_plus,
  ROUND(AVG(pct_disability), 2) AS avg_pct_disability,
  ROUND(AVG(pct_mobile_homes), 2) AS avg_pct_mobile_homes,
  ROUND(AVG(svi_socioeconomic), 3) AS avg_svi_socioeconomic,
  ROUND(AVG(svi_household), 3) AS avg_svi_household,
  ROUND(AVG(svi_housing_transport), 3) AS avg_svi_housing_transport,
  ROUND(AVG(pct_black), 2) AS avg_pct_black,
  ROUND(AVG(pct_hispanic), 2) AS avg_pct_hispanic,
  ROUND(AVG(pct_white_non_hispanic), 2) AS avg_pct_white_non_hispanic,
  ROUND(AVG(pct_asian), 2) AS avg_pct_asian
FROM combined
GROUP BY cohort
ORDER BY cohort
"""


# ---------------------------------------------------------------------------
# Live BigQuery Audit Runners
# ---------------------------------------------------------------------------


def _run_single_row(client: bigquery.Client, sql: str) -> dict[str, Any]:
    rows = [dict(r.items()) for r in client.query(sql).result()]
    return rows[0] if rows else {}


def audit_coordinate_sanity_and_counties(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    state: str = "FL",
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run Defect 1 audit: coordinate sanity, bounding box, and county FIPS coverage."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_defect_1_coordinate_sql(project_id, dataset, state)
    return _run_single_row(bq, sql)


def audit_flag_coverage_and_nulls(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run Defect 2 audit: three-state boolean flag coverage and unrecorded NULL percentages."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_defect_2_flags_sql(project_id, dataset)
    return _run_single_row(bq, sql)


def audit_shelter_deduplication(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run Defect 3 audit: duplicate shelter records by normalized (shelter_name, address_1)."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_defect_3_deduplication_sql(project_id, dataset)
    return _run_single_row(bq, sql)


def audit_tract_geometry_vintage_gap(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    state_fips: str = "12",
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run Defect 4 audit: 2010 BigQuery public tract geometry vs 2020 SVI/NRI tract vintage gap."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_defect_4_vintage_gap_sql(project_id, dataset, state_fips)
    return _run_single_row(bq, sql)


def audit_missing_places_transport(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run Defect 5 audit: missing CDC PLACES transportation barrier measure (pct_lacktrpt)."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_defect_5_places_transport_sql(project_id, dataset)
    return _run_single_row(bq, sql)


def audit_capacity_outliers_and_surge_exposure(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    capacity_max: int = 100000,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run Defect 6 audit: capacity outliers and surge/floodplain exposure."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_defect_6_outliers_and_surge_sql(project_id, dataset, capacity_max)
    return _run_single_row(bq, sql)


def run_comprehensive_equity_audit(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    top_n: int = 50,
    state_fips: str = "12",
    accessible_radius_m: float = 5000.0,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run the 3-Cohort Equity Audit and compute disparity deltas relative to the state baseline."""
    bq = client or bigquery.Client(project=project_id)
    sql = build_comprehensive_equity_audit_sql(
        project_id=project_id,
        dataset=dataset,
        top_n=top_n,
        state_fips=state_fips,
        accessible_radius_m=accessible_radius_m,
    )
    rows = [dict(r.items()) for r in bq.query(sql).result()]
    by_cohort = {r["cohort"]: r for r in rows}

    a = by_cohort.get("cohort_a_top_vulnerable", {})
    b = by_cohort.get("cohort_b_near_recorded_accessible", {})
    c = by_cohort.get("cohort_c_state_baseline", {})

    def _diff(lhs: dict[str, Any], rhs: dict[str, Any], key: str) -> float | None:
        lv, rv = lhs.get(key), rhs.get(key)
        if lv is None or rv is None:
            return None
        return round(float(lv) - float(rv), 3)

    return {
        "parameters": {
            "top_n": top_n,
            "state_fips": state_fips,
            "accessible_radius_m": accessible_radius_m,
        },
        "cohorts": by_cohort,
        "comparisons": {
            "vulnerable_vs_state_baseline": {
                "delta_pct_no_vehicle": _diff(a, c, "avg_pct_no_vehicle"),
                "delta_poverty_rate_pct": _diff(a, c, "avg_poverty_rate_pct"),
                "delta_median_income": _diff(a, c, "avg_median_income"),
                "delta_pct_limited_english": _diff(a, c, "avg_pct_limited_english"),
                "delta_svi_socioeconomic": _diff(a, c, "avg_svi_socioeconomic"),
                "delta_svi_household": _diff(a, c, "avg_svi_household"),
                "delta_svi_housing_transport": _diff(a, c, "avg_svi_housing_transport"),
                "delta_pct_black": _diff(a, c, "avg_pct_black"),
                "delta_pct_hispanic": _diff(a, c, "avg_pct_hispanic"),
            },
            "vulnerable_vs_near_accessible_shelter": {
                "delta_pct_no_vehicle": _diff(a, b, "avg_pct_no_vehicle"),
                "delta_poverty_rate_pct": _diff(a, b, "avg_poverty_rate_pct"),
                "delta_median_income": _diff(a, b, "avg_median_income"),
                "delta_pct_limited_english": _diff(a, b, "avg_pct_limited_english"),
                "delta_svi_socioeconomic": _diff(a, b, "avg_svi_socioeconomic"),
                "delta_pct_black": _diff(a, b, "avg_pct_black"),
                "delta_pct_hispanic": _diff(a, b, "avg_pct_hispanic"),
            },
        },
    }


def run_full_data_quality_report(
    project_id: str = DEFAULT_PROJECT,
    dataset: str = DEFAULT_DATASET,
    top_n: int = 50,
    state: str = "FL",
    state_fips: str = "12",
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run all 6 federal defect audits plus the 3-cohort equity audit against BigQuery."""
    bq = client or bigquery.Client(project=project_id)
    return {
        "project_id": project_id,
        "dataset": dataset,
        "state": state,
        "defects": {
            "defect_1_coordinates_and_counties": audit_coordinate_sanity_and_counties(
                project_id, dataset, state, client=bq
            ),
            "defect_2_unrecorded_flags_and_nulls": audit_flag_coverage_and_nulls(
                project_id, dataset, client=bq
            ),
            "defect_3_duplicate_shelters": audit_shelter_deduplication(
                project_id, dataset, client=bq
            ),
            "defect_4_tract_geometry_vintage_gap": audit_tract_geometry_vintage_gap(
                project_id, dataset, state_fips, client=bq
            ),
            "defect_5_missing_places_transport": audit_missing_places_transport(
                project_id, dataset, client=bq
            ),
            "defect_6_capacity_outliers_and_surge": audit_capacity_outliers_and_surge_exposure(
                project_id, dataset, client=bq
            ),
        },
        "equity_audit": run_comprehensive_equity_audit(
            project_id, dataset, top_n=top_n, state_fips=state_fips, client=bq
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Challenge 4 Data Quality & Federal Defect Report + Equity Audit."
    )
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--top-n", type=int, default=50)
    args = parser.parse_args(argv)

    report = run_full_data_quality_report(
        project_id=args.project,
        dataset=args.dataset,
        top_n=args.top_n,
    )
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
