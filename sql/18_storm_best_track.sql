-- 18_storm_best_track.sql
-- Purpose: Hurricane Ian observed best track (NHC), for the rehearsal map. Read only.
-- Parameters: @storm_id STRING
-- Output: valid_at, lat, lng, wind_kt
-- Sample row: 2022-09-28 19:05:00 UTC, 26.7, -82.2, 130
SELECT valid_at, lat, lng, wind_kt FROM `{{ops}}.storm_best_track` WHERE storm_id = @storm_id ORDER BY valid_at
