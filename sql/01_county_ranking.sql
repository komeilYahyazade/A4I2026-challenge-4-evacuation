-- 01_county_ranking.sql
-- Purpose: rank the state's counties by need x exposure for the county picker.
--   need_score / exposure_score = mean of the county's tract scores, min-max normalised across
--   counties. Shelter counts are shown, never used to rank (recorded counts measure paperwork).
-- Parameters: none.
-- Output: county_fips, name, total_pop, need_score, exposure_score, rank, shelters_total,
--   medical_needs, in_surge_zone, wheelchair_yes, wheelchair_no, wheelchair_unrecorded, lat, lon
-- Sample row (illustrative): 12071, "Lee County", 760822, 0.91, 0.97, 1, 100, 9, 11, 2, 5, 93, 26.56, -81.89
WITH t AS (
  SELECT county_fips, SUM(total_pop) AS total_pop, AVG(need_score) AS need, AVG(exposure_score) AS expo
  FROM `{{dataset}}.tract_scores`
  WHERE total_pop > 0
  GROUP BY county_fips
),
n AS (
  SELECT *,
    SAFE_DIVIDE(need - MIN(need) OVER (), MAX(need) OVER () - MIN(need) OVER ()) AS need_score,
    SAFE_DIVIDE(expo - MIN(expo) OVER (), MAX(expo) OVER () - MIN(expo) OVER ()) AS exposure_score
  FROM t
),
s AS (
  SELECT county_fips,
         COUNT(*) AS shelters_total,
         COUNTIF(is_medical) AS medical_needs,
         COUNTIF(in_surge_slosh_area = 'yes') AS in_surge_zone,
         COUNTIF(wheelchair_accessible = 'yes') AS wheelchair_yes,
         COUNTIF(wheelchair_accessible = 'no') AS wheelchair_no,
         COUNTIF(wheelchair_accessible = 'unrecorded') AS wheelchair_unrecorded
  FROM `{{dataset}}.shelter_evidence`
  GROUP BY county_fips
)
SELECT n.county_fips, b.lsad_name AS name, n.total_pop,
       ROUND(n.need_score, 3) AS need_score, ROUND(n.exposure_score, 3) AS exposure_score,
       RANK() OVER (ORDER BY IFNULL(n.need_score, 0) * IFNULL(n.exposure_score, 0) DESC) AS rank,
       IFNULL(s.shelters_total, 0) AS shelters_total, IFNULL(s.medical_needs, 0) AS medical_needs,
       IFNULL(s.in_surge_zone, 0) AS in_surge_zone, IFNULL(s.wheelchair_yes, 0) AS wheelchair_yes,
       IFNULL(s.wheelchair_no, 0) AS wheelchair_no, IFNULL(s.wheelchair_unrecorded, 0) AS wheelchair_unrecorded,
       b.int_point_lat AS lat, b.int_point_lon AS lon
FROM n
LEFT JOIN s USING (county_fips)
LEFT JOIN `bigquery-public-data.geo_us_boundaries.counties` b ON b.geo_id = n.county_fips
ORDER BY rank
