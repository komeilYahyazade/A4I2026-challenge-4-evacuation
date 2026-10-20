-- 11_tract_point.sql
-- Purpose: one tract centroid (the origin for drive times and straight-line distances).
-- Parameters: @geo_id STRING
-- Output: geo_id, county_fips, lat, lon
-- Sample row: "12071020801", "12071", 26.6, -81.9
SELECT geo_id, county_fips, lat, lon
FROM `{{dataset}}.tract_scores`
WHERE geo_id = @geo_id AND lat IS NOT NULL
