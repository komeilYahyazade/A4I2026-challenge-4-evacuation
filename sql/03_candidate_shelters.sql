-- 03_candidate_shelters.sql
-- Purpose: candidate shelters for one tract (nearest first, any county) or for a whole county.
--   straight_line_km is from the tract centroid; within_reach = straight_line_km <= @reachable_km.
--   The caller summarises counts by wheelchair state (yes / no / unrecorded) over within_reach rows.
-- Parameters: @geo_id STRING (nullable), @county_fips STRING (nullable), @reachable_km FLOAT64,
--   @usage STRING (nullable; e.g. 'EVAC'), @limit INT64
-- Output: Shelter fields in three-state form + place_id + straight_line_km + within_reach
-- Sample row: "70851", "Harborside Events Center Special Needs", ..., "unrecorded", 1.2, TRUE
WITH origin AS (
  SELECT ST_GEOGPOINT(lon, lat) AS pt FROM `{{dataset}}.tract_scores` WHERE geo_id = @geo_id
)
SELECT s.shelter_id, s.name, s.address, s.address_1, s.city, s.zip, s.county_fips, s.lat, s.lon,
       s.nearest_geo_id, s.usage, s.evacuation_capacity, s.post_impact_capacity,
       s.wheelchair_accessible, s.generator_onsite, s.self_sufficient_electricity,
       s.in_surge_slosh_area, s.in_100_yr_floodplain, s.is_medical, s.capacity_implausible,
       p.place_id,
       ROUND(ST_DISTANCE(o.pt, ST_GEOGPOINT(s.lon, s.lat)) / 1000, 2) AS straight_line_km,
       IFNULL(ST_DISTANCE(o.pt, ST_GEOGPOINT(s.lon, s.lat)) / 1000 <= @reachable_km, FALSE) AS within_reach
FROM `{{dataset}}.shelter_evidence` s
LEFT JOIN origin o ON TRUE
LEFT JOIN `{{dataset}}.shelter_places` p USING (shelter_id)
WHERE (@usage IS NULL OR s.usage = @usage)
  AND (@geo_id IS NOT NULL OR s.county_fips = @county_fips)
  AND (@geo_id IS NULL OR o.pt IS NOT NULL)
ORDER BY straight_line_km IS NULL, straight_line_km, s.shelter_id
LIMIT @limit
