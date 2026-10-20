-- 00_shelter_evidence.sql: one-off setup script (re-run after any data reload; idempotent).
--
-- Purpose: build the derived evidence tables every other file reads, so the three-state rule,
--   capacity rule, id casting and spatial placement happen once, at one boundary.
--   * shelter_places   : the ONLY Maps-derived thing we persist (shelter_id, place_id, first_seen)
--   * tract_scores     : vulnerability_tracts + NRI exposure + need_score / exposure_score +
--                        straight-line km to the nearest shelter and nearest wheelchair-yes shelter
--   * shelter_evidence : shelters in three-state form + nearest tract centroid (ST_DISTANCE) and
--                        that tract's HRCN_EALP / CFLD_EALP
--   * care_evidence    : nursing homes placed in a county by ST_CONTAINS + nearest generator-yes km
-- Federal tables are never modified. Derived tables are rebuilt wholesale.
-- Parameters: none. {{dataset}} is replaced with <project>.<dataset> by tools/bq.py.
-- need_score (min-max within the state, NULL components count as 0):
--   0.35*households_no_vehicle + 0.20*pop_65_plus + 0.20*pct_disability + 0.15*mobile_homes
--   + 0.10*group_quarters_pop. No race, no NRI RISK_SCORE or *_RISKS composites.
-- exposure_score: min-max of HRCN_EALP + CFLD_EALP (expected annual loss, population).
-- Sample shelter_evidence row (illustrative):
--   shelter_id=70851, name="Harborside Events Center Special Needs", wheelchair_accessible="unrecorded",
--   evacuation_capacity=NULL, nearest_geo_id="12071000500", nearest_km=0.4

CREATE TABLE IF NOT EXISTS `{{dataset}}.shelter_places` (
  shelter_id STRING NOT NULL,
  place_id STRING NOT NULL,
  first_seen TIMESTAMP NOT NULL
);

CREATE OR REPLACE TABLE `{{dataset}}.shelter_evidence` AS
WITH s AS (
  SELECT
    CAST(shelter_id AS STRING) AS shelter_id,
    shelter_name AS name,
    CONCAT(IFNULL(TRIM(address_1), ''), ', ', IFNULL(INITCAP(TRIM(city)), ''), ', ', state, ' ',
           IFNULL(zip, '')) AS address,
    TRIM(address_1) AS address_1,
    INITCAP(TRIM(city)) AS city,
    state,
    zip,
    county_fips,
    county_name,
    latitude AS lat,
    longitude AS lon,
    NULLIF(TRIM(usage), '') AS usage,
    IF(evacuation_capacity < 0, NULL, CAST(evacuation_capacity AS INT64)) AS evacuation_capacity,
    IF(post_impact_capacity < 0, NULL, CAST(post_impact_capacity AS INT64)) AS post_impact_capacity,
    -- Flagged, never dropped or overwritten (defect 6).
    evacuation_capacity > 100000 AS capacity_implausible,
    CASE WHEN wheelchair_accessible IS TRUE THEN 'yes'
         WHEN wheelchair_accessible IS FALSE THEN 'no' ELSE 'unrecorded' END AS wheelchair_accessible,
    CASE WHEN generator_onsite IS TRUE THEN 'yes'
         WHEN generator_onsite IS FALSE THEN 'no' ELSE 'unrecorded' END AS generator_onsite,
    CASE WHEN self_sufficient_electricity IS TRUE THEN 'yes'
         WHEN self_sufficient_electricity IS FALSE THEN 'no' ELSE 'unrecorded' END AS self_sufficient_electricity,
    CASE WHEN in_surge_slosh_area IS TRUE THEN 'yes'
         WHEN in_surge_slosh_area IS FALSE THEN 'no' ELSE 'unrecorded' END AS in_surge_slosh_area,
    CASE WHEN in_100_yr_floodplain IS TRUE THEN 'yes'
         WHEN in_100_yr_floodplain IS FALSE THEN 'no' ELSE 'unrecorded' END AS in_100_yr_floodplain,
    IFNULL(is_medical, FALSE) AS is_medical,
    IFNULL(is_functional, FALSE) AS is_functional,
    NULLIF(TRIM(pet_accommodations_code), '') AS pet_accommodations_code,
    NULLIF(TRIM(population_code), '') AS population_code,
    NULLIF(TRIM(shelter_status_code), '') AS shelter_status_code,
    CAST(CAST(incident_id AS INT64) AS STRING) AS incident_id,
    -- Coordinates win in spatial joins (defect 1); flag a record whose coordinates leave its state.
    county_fips IS NOT NULL AND SUBSTR(county_fips, 1, 2) != '12' AS coord_state_mismatch
  FROM `{{dataset}}.shelters`
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL
),
nearest AS (
  SELECT
    s.shelter_id,
    ARRAY_AGG(STRUCT(t.geo_id, t.HRCN_EALP, t.CFLD_EALP,
                     ST_DISTANCE(ST_GEOGPOINT(s.lon, s.lat), ST_GEOGPOINT(t.lon, t.lat)) / 1000 AS km)
              ORDER BY ST_DISTANCE(ST_GEOGPOINT(s.lon, s.lat), ST_GEOGPOINT(t.lon, t.lat))
              LIMIT 1)[OFFSET(0)] AS n
  FROM s
  CROSS JOIN (
    SELECT v.geo_id, v.lat, v.lon, h.HRCN_EALP, h.CFLD_EALP
    FROM `{{dataset}}.vulnerability_tracts` v
    LEFT JOIN `{{dataset}}.hazard_tracts` h USING (geo_id)
    WHERE v.lat IS NOT NULL
  ) t
  GROUP BY s.shelter_id
)
SELECT s.*, n.n.geo_id AS nearest_geo_id, ROUND(n.n.km, 2) AS nearest_tract_km,
       n.n.HRCN_EALP AS nearest_hrcn_ealp, n.n.CFLD_EALP AS nearest_cfld_ealp
FROM s LEFT JOIN nearest n USING (shelter_id);

CREATE OR REPLACE TABLE `{{dataset}}.tract_scores` AS
WITH t AS (
  SELECT v.geo_id, v.county_fips, v.lat, v.lon,
         CAST(v.total_pop AS INT64) AS total_pop, v.households, v.households_no_vehicle,
         v.pct_no_vehicle, v.pop_65_plus, v.pct_age_65_plus, v.pct_disability, v.mobile_homes,
         v.group_quarters_pop, v.poverty_rate, v.pct_limited_english, v.median_income,
         v.pct_mobility, v.pct_selfcare, v.pct_indeplive, v.pct_lacktrpt,
         h.HRCN_EALP, h.CFLD_EALP,
         IF(h.HRCN_EALP IS NULL AND h.CFLD_EALP IS NULL, NULL,
            IFNULL(h.HRCN_EALP, 0) + IFNULL(h.CFLD_EALP, 0)) AS ealp
  FROM `{{dataset}}.vulnerability_tracts` v
  LEFT JOIN `{{dataset}}.hazard_tracts` h USING (geo_id)
),
n AS (
  SELECT *,
    SAFE_DIVIDE(households_no_vehicle - MIN(households_no_vehicle) OVER (),
                MAX(households_no_vehicle) OVER () - MIN(households_no_vehicle) OVER ()) AS n_noveh,
    SAFE_DIVIDE(pop_65_plus - MIN(pop_65_plus) OVER (),
                MAX(pop_65_plus) OVER () - MIN(pop_65_plus) OVER ()) AS n_65,
    SAFE_DIVIDE(pct_disability - MIN(pct_disability) OVER (),
                MAX(pct_disability) OVER () - MIN(pct_disability) OVER ()) AS n_dis,
    SAFE_DIVIDE(mobile_homes - MIN(mobile_homes) OVER (),
                MAX(mobile_homes) OVER () - MIN(mobile_homes) OVER ()) AS n_mob,
    SAFE_DIVIDE(group_quarters_pop - MIN(group_quarters_pop) OVER (),
                MAX(group_quarters_pop) OVER () - MIN(group_quarters_pop) OVER ()) AS n_gq,
    SAFE_DIVIDE(ealp - MIN(ealp) OVER (), MAX(ealp) OVER () - MIN(ealp) OVER ()) AS exposure_score
  FROM t
),
dist AS (
  SELECT t.geo_id,
         MIN(ST_DISTANCE(ST_GEOGPOINT(t.lon, t.lat), ST_GEOGPOINT(s.lon, s.lat))) / 1000 AS km_any,
         MIN(IF(s.wheelchair_accessible = 'yes',
                ST_DISTANCE(ST_GEOGPOINT(t.lon, t.lat), ST_GEOGPOINT(s.lon, s.lat)), NULL)) / 1000 AS km_wc_yes
  FROM t CROSS JOIN `{{dataset}}.shelter_evidence` s
  WHERE t.lat IS NOT NULL
  GROUP BY t.geo_id
)
SELECT n.* EXCEPT (n_noveh, n_65, n_dis, n_mob, n_gq, ealp),
  IF(n.total_pop > 0,
     ROUND(0.35 * IFNULL(n_noveh, 0) + 0.20 * IFNULL(n_65, 0) + 0.20 * IFNULL(n_dis, 0)
           + 0.15 * IFNULL(n_mob, 0) + 0.10 * IFNULL(n_gq, 0), 4),
     NULL) AS need_score,
  ROUND(d.km_any, 2) AS nearest_shelter_km,
  ROUND(d.km_wc_yes, 2) AS nearest_wheelchair_yes_km
FROM n LEFT JOIN dist d USING (geo_id);

CREATE OR REPLACE TABLE `{{dataset}}.care_evidence` AS
WITH c AS (
  SELECT f.cms_certification_number_ccn AS ccn, f.provider_name AS name,
         f.provider_address, INITCAP(f.citytown) AS city, f.zip_code,
         f.latitude AS lat, f.longitude AS lon,
         f.number_of_certified_beds AS certified_beds, f.ownership_type,
         b.geo_id AS county_fips
  FROM `{{dataset}}.care_facilities` f
  LEFT JOIN `bigquery-public-data.geo_us_boundaries.counties` b
    ON ST_CONTAINS(b.county_geom, ST_GEOGPOINT(f.longitude, f.latitude))
  WHERE f.latitude IS NOT NULL AND f.longitude IS NOT NULL
),
gen AS (
  SELECT c.ccn, MIN(ST_DISTANCE(ST_GEOGPOINT(c.lon, c.lat), ST_GEOGPOINT(s.lon, s.lat))) / 1000 AS km
  FROM c CROSS JOIN `{{dataset}}.shelter_evidence` s
  WHERE s.generator_onsite = 'yes'
  GROUP BY c.ccn
)
SELECT c.*, ROUND(gen.km, 2) AS nearest_generator_yes_km
FROM c LEFT JOIN gen USING (ccn);
