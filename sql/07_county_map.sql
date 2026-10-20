-- 07_county_map.sql
-- Purpose: the four map layers for one county in one row: tract centroids, shelters, nursing
--   homes and the county power-dependence total. Only our own coordinates (ACS/NRI, FEMA, CMS).
-- Parameters: @county_fips STRING
-- Output: one row: lat, lon (county interior point), tracts ARRAY<STRUCT>, shelters ARRAY<STRUCT>,
--   care_facilities ARRAY<STRUCT>, power_dependent_dme, is_censored
-- Sample row: 26.56, -81.89, [{geo_id: "12071020801", ...}], [{shelter_id: "70851", ...}], [...], 5480, FALSE
SELECT
  (SELECT int_point_lat FROM `bigquery-public-data.geo_us_boundaries.counties` WHERE geo_id = @county_fips) AS lat,
  (SELECT int_point_lon FROM `bigquery-public-data.geo_us_boundaries.counties` WHERE geo_id = @county_fips) AS lon,
  ARRAY(
    SELECT AS STRUCT geo_id, lat, lon, total_pop, households_no_vehicle, pct_no_vehicle, pop_65_plus,
           pct_age_65_plus, pct_disability, mobile_homes, group_quarters_pop, need_score,
           nearest_shelter_km, nearest_wheelchair_yes_km, households, ROUND(exposure_score, 4) AS exposure_score,
           pct_mobility
    FROM `{{dataset}}.tract_scores`
    WHERE county_fips = @county_fips AND lat IS NOT NULL AND total_pop > 0
    ORDER BY need_score DESC) AS tracts,
  ARRAY(
    SELECT AS STRUCT s.shelter_id, s.name, s.address, s.county_fips, s.lat, s.lon, s.nearest_geo_id,
           s.usage, s.evacuation_capacity, s.post_impact_capacity, s.wheelchair_accessible,
           s.generator_onsite, s.in_surge_slosh_area, s.in_100_yr_floodplain, s.is_medical, p.place_id
    FROM `{{dataset}}.shelter_evidence` s
    LEFT JOIN `{{dataset}}.shelter_places` p USING (shelter_id)
    WHERE s.county_fips = @county_fips
    ORDER BY s.shelter_id) AS shelters,
  ARRAY(
    SELECT AS STRUCT ccn, name, lat, lon, certified_beds, nearest_generator_yes_km
    FROM `{{dataset}}.care_evidence`
    WHERE county_fips = @county_fips
    ORDER BY certified_beds DESC) AS care_facilities,
  (SELECT CAST(power_dependent_dme AS INT64) FROM `{{dataset}}.power_dependent_counties`
   WHERE fips_code = @county_fips) AS power_dependent_dme,
  (SELECT power_dependent_is_censored FROM `{{dataset}}.power_dependent_counties`
   WHERE fips_code = @county_fips) AS is_censored
