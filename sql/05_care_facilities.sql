-- 05_care_facilities.sql
-- Purpose: nursing homes in one county (county placed by ST_CONTAINS in 00), with the
--   straight-line km to the nearest shelter whose generator is recorded 'yes'.
-- Parameters: @county_fips STRING
-- Output: ccn, name, lat, lon, certified_beds, nearest_generator_yes_km
-- Sample row: "105678", "Example Gardens Nursing Center", 26.61, -81.80, 120, 12.4
SELECT ccn, name, lat, lon, certified_beds, nearest_generator_yes_km
FROM `{{dataset}}.care_evidence`
WHERE county_fips = @county_fips
ORDER BY certified_beds DESC, ccn
