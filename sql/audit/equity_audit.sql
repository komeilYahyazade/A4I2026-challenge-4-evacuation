-- Equity audit of a recommended tract list. Race enters here and only here: as an audit of the
-- output, never as an input. ACS 2020 5-year (the vintage vulnerability_tracts was built from),
-- joined back by geo_id. Compares the recommended list with the scenario's own transport measure
-- (top N tracts by no-car households), the county, and the state.
--
-- Run after any ranking change, with the new list, to get the "after" row:
--   bq query --use_legacy_sql=false \
--     --parameter='geo_ids:ARRAY<STRING>:["12071020801","12071010112"]' \
--     --parameter='county_fips:STRING:12071' --parameter='label:STRING:after' \
--     < <(sed "s/\${PROJECT}/$(gcloud config get-value project)/g" sql/audit/equity_audit.sql)
-- (SQL on stdin: a query string starting with "--" is otherwise parsed as a bq flag.)
WITH acs AS (
  SELECT
    LPAD(CAST(geo_id AS STRING), 11, '0') AS geo_id,
    SAFE_CAST(total_pop AS FLOAT64) AS pop,
    SAFE_CAST(black_pop AS FLOAT64) AS black,
    SAFE_CAST(hispanic_pop AS FLOAT64) AS hispanic,
    SAFE_CAST(white_pop AS FLOAT64) AS white_nh,
    SAFE_CAST(poverty AS FLOAT64) AS poverty,
    SAFE_CAST(pop_determined_poverty_status AS FLOAT64) AS poverty_universe
  FROM `bigquery-public-data.census_bureau_acs.censustract_2020_5yr`
  WHERE STARTS_WITH(LPAD(CAST(geo_id AS STRING), 11, '0'), SUBSTR(@county_fips, 1, 2))
),
need AS (
  SELECT n.*, ROW_NUMBER() OVER (ORDER BY n.households_no_vehicle DESC, n.geo_id) AS r
  FROM `${PROJECT}.readiness_ops.v_tract_need` AS n
  WHERE n.county_fips = @county_fips
),
county_no_car AS (SELECT SUM(households_no_vehicle) AS hh FROM need),
cohorts AS (
  SELECT @label AS cohort, 1 AS ord, geo_id FROM UNNEST(@geo_ids) AS geo_id
  UNION ALL SELECT 'top_by_no_car_households', 2, geo_id FROM need WHERE r <= ARRAY_LENGTH(@geo_ids)
  UNION ALL SELECT 'county', 3, geo_id FROM need
  UNION ALL SELECT 'state', 4, geo_id FROM acs
),
near AS (
  SELECT DISTINCT c.cohort, e.shelter_id, e.wheelchair_status
  FROM cohorts AS c
  JOIN need AS n USING (geo_id)
  JOIN `${PROJECT}.readiness_ops.v_shelter_evidence` AS e
    ON e.is_pre_landfall
   AND ST_DWITHIN(ST_GEOGPOINT(n.lon, n.lat), ST_GEOGPOINT(e.longitude, e.latitude), 15000)
),
reporting AS (
  SELECT cohort, ROUND(COUNTIF(wheelchair_status != 'UNRECORDED') / NULLIF(COUNT(*), 0), 3) AS wc_recorded_share
  FROM near
  GROUP BY cohort
)
SELECT
  c.cohort,
  COUNT(*) AS tracts,
  -- need covers the county only, so the state row carries demographics and nothing else.
  IF(c.cohort = 'state', NULL, CAST(SUM(n.households_no_vehicle) AS INT64)) AS no_car_households,
  IF(c.cohort = 'state', NULL, ROUND(SUM(n.households_no_vehicle) / ANY_VALUE(k.hh), 3)) AS share_of_county_no_car_households,
  ROUND(SAFE_DIVIDE(SUM(a.black), SUM(a.pop)), 3) AS share_black,
  ROUND(SAFE_DIVIDE(SUM(a.hispanic), SUM(a.pop)), 3) AS share_hispanic,
  ROUND(SAFE_DIVIDE(SUM(a.white_nh), SUM(a.pop)), 3) AS share_white_non_hispanic,
  ROUND(SAFE_DIVIDE(SUM(a.poverty), SUM(a.poverty_universe)), 3) AS poverty_rate,
  IF(c.cohort = 'state', NULL, ANY_VALUE(rp.wc_recorded_share)) AS wheelchair_recorded_share_within_15km
FROM cohorts AS c
JOIN acs AS a USING (geo_id)
LEFT JOIN need AS n USING (geo_id)
LEFT JOIN reporting AS rp ON rp.cohort = c.cohort
CROSS JOIN county_no_car AS k
GROUP BY c.cohort, c.ord
ORDER BY c.ord;
