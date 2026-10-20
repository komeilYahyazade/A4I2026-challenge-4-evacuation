-- 08_equity_audit.sql
-- Purpose: did the plan land where people cannot leave, or where somebody filled in the form?
--   Compares the tracts the brief recommends (who_cannot_leave) with every tract in the county and
--   in the state. Rates are population-weighted. Race and ethnicity shares (ACS) are AUDIT ONLY:
--   they are never an input to need_score. used_as_input marks the metrics that feed the ranking.
--   shelters_access_unrecorded: share of shelters within @reachable_km (straight line) of the
--   group's tracts whose wheelchair field is unrecorded.
-- Parameters: @geo_ids ARRAY<STRING>, @county_fips STRING, @reachable_km FLOAT64
-- Output: metric, unit, recommended, county, state, used_as_input, sort
-- Sample row: "poverty_rate", "%", 24.1, 13.2, 12.7, FALSE, 1
WITH t AS (
  SELECT s.geo_id, s.county_fips, s.lat, s.lon, s.total_pop, s.households, s.households_no_vehicle,
         s.poverty_rate, s.pct_limited_english, s.pct_disability, s.pct_age_65_plus,
         -- ACS writes -666666666 when a median could not be computed: a sentinel, not income.
         IF(s.median_income > 0, s.median_income, NULL) AS median_income,
         SAFE_CAST(a.white_pop AS FLOAT64) AS white_pop, SAFE_CAST(a.black_pop AS FLOAT64) AS black_pop,
         SAFE_CAST(a.hispanic_pop AS FLOAT64) AS hispanic_pop, SAFE_CAST(a.asian_pop AS FLOAT64) AS asian_pop,
         SAFE_CAST(a.total_pop AS FLOAT64) AS acs_pop
  FROM `{{dataset}}.tract_scores` s
  LEFT JOIN `bigquery-public-data.census_bureau_acs.censustract_2020_5yr` a
    ON LPAD(a.geo_id, 11, '0') = s.geo_id
  WHERE s.total_pop > 0
),
g AS (
  SELECT 'recommended' AS grp, * FROM t WHERE geo_id IN UNNEST(@geo_ids)
  UNION ALL SELECT 'county', * FROM t WHERE county_fips = @county_fips
  UNION ALL SELECT 'state', * FROM t
),
m AS (
  SELECT grp,
    100 * SAFE_DIVIDE(SUM(poverty_rate * total_pop), SUM(IF(poverty_rate IS NULL, 0, total_pop))) AS poverty_rate,
    SAFE_DIVIDE(SUM(pct_limited_english * total_pop), SUM(IF(pct_limited_english IS NULL, 0, total_pop))) AS pct_limited_english,
    SAFE_DIVIDE(SUM(median_income * total_pop), SUM(IF(median_income IS NULL, 0, total_pop))) AS median_income,
    100 * SAFE_DIVIDE(SUM(households_no_vehicle), SUM(households)) AS pct_households_no_vehicle,
    SAFE_DIVIDE(SUM(pct_disability * total_pop), SUM(IF(pct_disability IS NULL, 0, total_pop))) AS pct_disability,
    SAFE_DIVIDE(SUM(pct_age_65_plus * total_pop), SUM(IF(pct_age_65_plus IS NULL, 0, total_pop))) AS pct_age_65_plus,
    100 * SAFE_DIVIDE(SUM(black_pop), SUM(acs_pop)) AS pct_black,
    100 * SAFE_DIVIDE(SUM(hispanic_pop), SUM(acs_pop)) AS pct_hispanic,
    100 * SAFE_DIVIDE(SUM(white_pop), SUM(acs_pop)) AS pct_white_non_hispanic,
    100 * SAFE_DIVIDE(SUM(asian_pop), SUM(acs_pop)) AS pct_asian
  FROM g GROUP BY grp
),
near AS (  -- shelters near each group's tracts, and how many have wheelchair access unrecorded
  SELECT g.grp, COUNT(DISTINCT s.shelter_id) AS n,
         COUNT(DISTINCT IF(s.wheelchair_accessible = 'unrecorded', s.shelter_id, NULL)) AS unrec
  FROM g JOIN `{{dataset}}.shelter_evidence` s
    ON ST_DWITHIN(ST_GEOGPOINT(g.lon, g.lat), ST_GEOGPOINT(s.lon, s.lat), @reachable_km * 1000)
  WHERE g.lat IS NOT NULL
  GROUP BY g.grp
),
wide AS (
  SELECT m.*, 100 * SAFE_DIVIDE(near.unrec, near.n) AS shelters_access_unrecorded
  FROM m LEFT JOIN near USING (grp)
),
long_rows AS (
  SELECT grp, metric, value FROM wide
  UNPIVOT (value FOR metric IN (poverty_rate, pct_limited_english, median_income, pct_households_no_vehicle,
                                pct_disability, pct_age_65_plus, shelters_access_unrecorded, pct_black,
                                pct_hispanic, pct_white_non_hispanic, pct_asian))
)
SELECT metric,
       IF(metric = 'median_income', 'USD', '%') AS unit,
       ROUND(MAX(IF(grp = 'recommended', value, NULL)), 1) AS recommended,
       ROUND(MAX(IF(grp = 'county', value, NULL)), 1) AS county,
       ROUND(MAX(IF(grp = 'state', value, NULL)), 1) AS state,
       metric IN ('pct_households_no_vehicle', 'pct_disability', 'pct_age_65_plus') AS used_as_input,
       CASE metric WHEN 'pct_households_no_vehicle' THEN 1 WHEN 'pct_age_65_plus' THEN 2
                   WHEN 'pct_disability' THEN 3 WHEN 'poverty_rate' THEN 4 WHEN 'median_income' THEN 5
                   WHEN 'pct_limited_english' THEN 6 WHEN 'shelters_access_unrecorded' THEN 7
                   WHEN 'pct_black' THEN 8 WHEN 'pct_hispanic' THEN 9 WHEN 'pct_white_non_hispanic' THEN 10
                   ELSE 11 END AS sort
FROM long_rows
GROUP BY metric
ORDER BY sort
