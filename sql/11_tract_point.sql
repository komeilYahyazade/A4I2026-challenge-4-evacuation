-- 11_tract_point.sql
-- Purpose: one tract centroid (the origin for drive times and straight-line distances).
-- Parameters: @geo_id STRING (a tract id, or a nursing-home CCN prefixed 'ccn:')
-- Output: geo_id, county_fips, lat, lon
-- Sample row: "12071020801", "12071", 26.6, -81.9
SELECT geo_id, county_fips, lat, lon
FROM `{{dataset}}.tract_scores`
WHERE geo_id = @geo_id AND lat IS NOT NULL
UNION ALL
SELECT CONCAT('ccn:', ccn), county_fips, lat, lon
FROM `{{dataset}}.care_evidence`
WHERE CONCAT('ccn:', ccn) = @geo_id AND lat IS NOT NULL
