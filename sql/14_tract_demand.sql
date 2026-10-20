-- 14_tract_demand.sql
-- Purpose: allocator demand. People in households without a car, per tract:
--   households_no_vehicle x persons per household, where persons per household excludes group
--   quarters (nursing homes and dorms are not households). ACS vehicle ownership, not ride access,
--   so this is an upper bound on who needs a public-shelter seat.
-- Parameters: @county_fips STRING
-- Output: geo_id, lat, lon, households_no_vehicle, persons_per_household, est_persons_no_vehicle
-- Sample row: "12071001401", 26.6, -81.87, 639, 2.41, 1540
SELECT geo_id, lat, lon, households_no_vehicle,
       ROUND(SAFE_DIVIDE(total_pop - IFNULL(group_quarters_pop, 0), households), 3) AS persons_per_household,
       ROUND(households_no_vehicle * SAFE_DIVIDE(total_pop - IFNULL(group_quarters_pop, 0), households)) AS est_persons_no_vehicle
FROM `{{dataset}}.tract_scores`
WHERE county_fips = @county_fips AND total_pop > 0 AND households > 0 AND lat IS NOT NULL
