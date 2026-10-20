-- 13_county_insights.sql
-- Purpose: the numbers behind the insights dashboard for one county, with state baselines.
--   Shelter flags in three states (all shelters and EVAC only); straight-line access from tract
--   centroids (median km to any shelter / to one recorded wheelchair-yes; households with no car
--   within @reachable_km of each); population-weighted county vs state baselines; nursing homes far
--   from a recorded generator. Recorded counts measure paperwork: shown, never used to rank.
-- Parameters: @county_fips STRING, @reachable_km FLOAT64
-- Output: one row of scalar columns and ARRAY<STRUCT> flags (shaped by api/data.py)
-- Sample row: shelters_total=100, evac_total=69, ..., county_median_km_any=1.6, state_median_km_any=1.9
WITH s AS (
  SELECT e.*, p.place_id FROM `{{dataset}}.shelter_evidence` e
  LEFT JOIN `{{dataset}}.shelter_places` p USING (shelter_id)
  WHERE e.county_fips = @county_fips
),
t AS (
  SELECT *, county_fips = @county_fips AS in_county
  FROM `{{dataset}}.tract_scores` WHERE total_pop > 0 AND lat IS NOT NULL
),
flag_rows AS (
  SELECT scope, flag,
         COUNTIF(v = 'yes') AS yes, COUNTIF(v = 'no') AS `no`, COUNTIF(v = 'unrecorded') AS unrecorded
  FROM (
    SELECT 'all' AS scope, usage, wheelchair_accessible, generator_onsite, self_sufficient_electricity,
           in_surge_slosh_area, in_100_yr_floodplain FROM s
    UNION ALL
    SELECT 'evac', usage, wheelchair_accessible, generator_onsite, self_sufficient_electricity,
           in_surge_slosh_area, in_100_yr_floodplain FROM s WHERE usage = 'EVAC'
  )
  UNPIVOT (v FOR flag IN (wheelchair_accessible, generator_onsite, self_sufficient_electricity,
                          in_surge_slosh_area, in_100_yr_floodplain))
  GROUP BY scope, flag
),
care AS (
  SELECT COUNT(*) AS facilities, SUM(certified_beds) AS beds,
         COUNTIF(nearest_generator_yes_km IS NULL OR nearest_generator_yes_km > 10) AS far,
         SUM(IF(nearest_generator_yes_km IS NULL OR nearest_generator_yes_km > 10, certified_beds, 0)) AS beds_far
  FROM `{{dataset}}.care_evidence` WHERE county_fips = @county_fips
)
SELECT
  (SELECT lsad_name FROM `bigquery-public-data.geo_us_boundaries.counties` WHERE geo_id = @county_fips) AS name,
  -- shelters (FEMA, historical)
  (SELECT COUNT(*) FROM s) AS shelters_total,
  (SELECT COUNTIF(usage = 'EVAC') FROM s) AS evac_total,
  (SELECT COUNTIF(is_medical) FROM s) AS medical_total,
  (SELECT SUM(evacuation_capacity) FROM s) AS capacity_recorded,
  (SELECT COUNTIF(evacuation_capacity IS NULL) FROM s) AS capacity_unrecorded,
  (SELECT COUNTIF(capacity_implausible) FROM s) AS capacity_implausible,
  (SELECT COUNTIF(place_id IS NOT NULL) FROM s) AS verified_on_maps,
  ARRAY(SELECT AS STRUCT scope, flag, yes, `no`, unrecorded FROM flag_rows ORDER BY scope, flag) AS flags,
  -- access, straight line from tract centroids
  (SELECT COUNTIF(in_county) FROM t) AS county_tracts,
  (SELECT COUNT(*) FROM t) AS state_tracts,
  (SELECT ROUND(APPROX_QUANTILES(IF(in_county, nearest_shelter_km, NULL), 100)[OFFSET(50)], 2) FROM t) AS county_median_km_any,
  (SELECT ROUND(APPROX_QUANTILES(nearest_shelter_km, 100)[OFFSET(50)], 2) FROM t) AS state_median_km_any,
  (SELECT ROUND(APPROX_QUANTILES(IF(in_county, nearest_wheelchair_yes_km, NULL), 100)[OFFSET(50)], 2) FROM t) AS county_median_km_wc,
  (SELECT ROUND(APPROX_QUANTILES(nearest_wheelchair_yes_km, 100)[OFFSET(50)], 2) FROM t) AS state_median_km_wc,
  (SELECT CAST(SUM(IF(in_county AND nearest_shelter_km > 10, total_pop, 0)) AS INT64) FROM t) AS county_people_over_10km,
  (SELECT CAST(SUM(IF(nearest_shelter_km > 10, total_pop, 0)) AS INT64) FROM t) AS state_people_over_10km,
  (SELECT CAST(SUM(IF(in_county, households_no_vehicle, 0)) AS INT64) FROM t) AS county_hh_no_vehicle,
  (SELECT CAST(SUM(households_no_vehicle) AS INT64) FROM t) AS state_hh_no_vehicle,
  (SELECT CAST(SUM(IF(in_county AND nearest_shelter_km <= @reachable_km, households_no_vehicle, 0)) AS INT64) FROM t) AS county_hh_reach_any,
  (SELECT CAST(SUM(IF(nearest_shelter_km <= @reachable_km, households_no_vehicle, 0)) AS INT64) FROM t) AS state_hh_reach_any,
  (SELECT CAST(SUM(IF(in_county AND nearest_wheelchair_yes_km <= @reachable_km, households_no_vehicle, 0)) AS INT64) FROM t) AS county_hh_reach_wc,
  (SELECT CAST(SUM(IF(nearest_wheelchair_yes_km <= @reachable_km, households_no_vehicle, 0)) AS INT64) FROM t) AS state_hh_reach_wc,
  (SELECT COUNTIF(in_county AND (nearest_wheelchair_yes_km IS NULL OR nearest_wheelchair_yes_km > @reachable_km)) FROM t) AS county_tracts_no_wc,
  (SELECT COUNTIF(nearest_wheelchair_yes_km IS NULL OR nearest_wheelchair_yes_km > @reachable_km) FROM t) AS state_tracts_no_wc,
  -- population-weighted baselines (ACS counts; SVI shares; PLACES modeled)
  (SELECT ROUND(100 * SAFE_DIVIDE(SUM(IF(in_county, households_no_vehicle, 0)), SUM(IF(in_county, households, 0))), 1) FROM t) AS county_pct_no_vehicle,
  (SELECT ROUND(100 * SAFE_DIVIDE(SUM(households_no_vehicle), SUM(households)), 1) FROM t) AS state_pct_no_vehicle,
  (SELECT ROUND(SAFE_DIVIDE(SUM(IF(in_county, pct_age_65_plus * total_pop, 0)), SUM(IF(in_county AND pct_age_65_plus IS NOT NULL, total_pop, 0))), 1) FROM t) AS county_pct_65,
  (SELECT ROUND(SAFE_DIVIDE(SUM(pct_age_65_plus * total_pop), SUM(IF(pct_age_65_plus IS NOT NULL, total_pop, 0))), 1) FROM t) AS state_pct_65,
  (SELECT ROUND(SAFE_DIVIDE(SUM(IF(in_county, pct_disability * total_pop, 0)), SUM(IF(in_county AND pct_disability IS NOT NULL, total_pop, 0))), 1) FROM t) AS county_pct_disability,
  (SELECT ROUND(SAFE_DIVIDE(SUM(pct_disability * total_pop), SUM(IF(pct_disability IS NOT NULL, total_pop, 0))), 1) FROM t) AS state_pct_disability,
  (SELECT ROUND(100 * SAFE_DIVIDE(SUM(IF(in_county, mobile_homes, 0)), SUM(IF(in_county, households, 0))), 1) FROM t) AS county_pct_mobile_homes,
  (SELECT ROUND(100 * SAFE_DIVIDE(SUM(mobile_homes), SUM(households)), 1) FROM t) AS state_pct_mobile_homes,
  (SELECT ROUND(100 * SAFE_DIVIDE(SUM(IF(in_county, poverty_rate * total_pop, 0)), SUM(IF(in_county AND poverty_rate IS NOT NULL, total_pop, 0))), 1) FROM t) AS county_poverty_rate,
  (SELECT ROUND(100 * SAFE_DIVIDE(SUM(poverty_rate * total_pop), SUM(IF(poverty_rate IS NOT NULL, total_pop, 0))), 1) FROM t) AS state_poverty_rate,
  (SELECT ROUND(SAFE_DIVIDE(SUM(IF(in_county, pct_mobility * total_pop, 0)), SUM(IF(in_county AND pct_mobility IS NOT NULL, total_pop, 0))), 1) FROM t) AS county_pct_mobility,
  (SELECT ROUND(SAFE_DIVIDE(SUM(pct_mobility * total_pop), SUM(IF(pct_mobility IS NOT NULL, total_pop, 0))), 1) FROM t) AS state_pct_mobility,
  -- nursing homes (CMS)
  (SELECT facilities FROM care) AS care_facilities, (SELECT beds FROM care) AS care_beds,
  (SELECT far FROM care) AS care_far_from_generator, (SELECT beds_far FROM care) AS care_beds_far_from_generator,
  -- power dependence (HHS emPOWER, county total) and medical-needs capacity
  (SELECT CAST(power_dependent_dme AS INT64) FROM `{{dataset}}.power_dependent_counties` WHERE fips_code = @county_fips) AS power_dependent_dme,
  (SELECT power_dependent_is_censored FROM `{{dataset}}.power_dependent_counties` WHERE fips_code = @county_fips) AS power_is_censored,
  (SELECT SUM(IF(is_medical, evacuation_capacity, 0)) FROM s) AS medical_capacity
