-- 15_tract_shelter_edges.sql
-- Purpose: allocator supply. Each tract to its 15 nearest pre-landfall shelters within @max_km
--   straight line, shelters in any county (people cross county lines). Pre-landfall = FEMA usage
--   EVAC or BOTH. Flags are three-state. The 15-nearest cap matches main's v_tract_shelter_candidates,
--   so both branches report the same seat counts.
-- Parameters: @county_fips STRING, @max_km FLOAT64
-- Output: geo_id, shelter_id, shelter_name, shelter_county_fips, shelter_lat, shelter_lng, straight_line_km,
--   usage, evacuation_capacity, capacity_implausible, wheelchair_accessible, generator_onsite,
--   in_surge_slosh_area, is_medical
-- Sample row: "12071001401", "134167", "Littleton Elementary School", "12071", 26.69, -81.91, 3.2, "EVAC", 800, ...
SELECT t.geo_id, s.shelter_id, s.name AS shelter_name, s.county_fips AS shelter_county_fips,
       s.lat AS shelter_lat, s.lon AS shelter_lng,
       ROUND(ST_DISTANCE(ST_GEOGPOINT(t.lon, t.lat), ST_GEOGPOINT(s.lon, s.lat)) / 1000, 3) AS straight_line_km,
       s.usage, s.evacuation_capacity, s.capacity_implausible, s.wheelchair_accessible, s.generator_onsite,
       s.in_surge_slosh_area, s.is_medical
FROM `{{dataset}}.tract_scores` t
JOIN `{{dataset}}.shelter_evidence` s
  ON ST_DWITHIN(ST_GEOGPOINT(t.lon, t.lat), ST_GEOGPOINT(s.lon, s.lat), @max_km * 1000)
WHERE t.county_fips = @county_fips AND t.total_pop > 0 AND t.lat IS NOT NULL
  AND s.usage IN ('EVAC', 'BOTH')
QUALIFY ROW_NUMBER() OVER (PARTITION BY t.geo_id
                           ORDER BY ST_DISTANCE(ST_GEOGPOINT(t.lon, t.lat), ST_GEOGPOINT(s.lon, s.lat)), s.shelter_id) <= 15
