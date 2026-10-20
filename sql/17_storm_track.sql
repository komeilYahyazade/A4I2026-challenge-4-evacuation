-- 17_storm_track.sql
-- Purpose: Hurricane Ian NHC advisories (5-day cone, forecast track) for the rehearsal map.
--   NOAA NHC GIS archive (public domain), loaded by the data lane into {{ops}} (read only).
-- Parameters: @storm_id STRING
-- Output: advisory, issued_at, hours_before_landfall, cone_geojson (simplified), track_json
-- Sample row: "19", 2022-09-27 21:00:00 UTC, 22.1, '{"type":"Polygon",...}', '[{"lat":...}]'
SELECT advisory, issued_at, hours_before_landfall,
       ST_ASGEOJSON(ST_SIMPLIFY(cone, 2000)) AS cone_geojson, track_json
FROM `{{ops}}.storm_advisories`
WHERE storm_id = @storm_id
ORDER BY issued_at
