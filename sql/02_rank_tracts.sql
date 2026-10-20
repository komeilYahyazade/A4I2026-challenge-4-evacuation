-- 02_rank_tracts.sql
-- Purpose: tracts in one county ranked by need_score (who cannot leave), with the drivers.
--   Tracts are centroid points (2020 vintage), never polygons. Distances are straight-line km.
--   households_no_vehicle / pct_no_vehicle measure car OWNERSHIP, not lacking a ride.
-- Parameters: @county_fips STRING, @limit INT64
-- Output: geo_id, lat, lon, total_pop, households_no_vehicle, pct_no_vehicle, pop_65_plus,
--   pct_age_65_plus, pct_disability, mobile_homes, group_quarters_pop, need_score,
--   nearest_shelter_km, nearest_wheelchair_yes_km, pct_mobility, exposure_score
-- Sample row: "12071020801", 26.6, -81.9, 2062, 401, ..., 0.3448, 0.84, 9.6
SELECT geo_id, lat, lon, total_pop,
       households_no_vehicle, pct_no_vehicle, pop_65_plus, pct_age_65_plus, pct_disability,
       mobile_homes, group_quarters_pop, need_score, nearest_shelter_km, nearest_wheelchair_yes_km,
       pct_mobility, ROUND(exposure_score, 4) AS exposure_score
FROM `{{dataset}}.tract_scores`
WHERE county_fips = @county_fips AND total_pop > 0 AND lat IS NOT NULL
ORDER BY need_score DESC
LIMIT @limit
