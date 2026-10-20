-- 16_ian_shelters_down.sql
-- Purpose: Hurricane Ian (2022) tabletop replay. Which pre-landfall shelters serving this county's
--   tracts (within @max_km) the drill takes offline at one NHC advisory. Rehearsal only: never used
--   with Google Maps. A shelter goes down when it meets the hazard rule AND sits inside that
--   advisory's 5-day cone (advisory 'ANY' = no cone, the cone-agnostic list):
--     IN_SURGE_ZONE              FEMA in_surge_slosh_area = yes                        (RECORDED)
--     IN_CONE_AND_FLOOD_EXPOSED  nearest tract has >= 50% of residents exposed to       (APPROXIMATE)
--                                coastal flooding (FEMA NRI CFLD_EXPP / POPULATION)
--   The surge flag alone cannot carry this: it is blank for most Lee pre-landfall shelters.
--   Rule and 50% cut ported from main's sql/ian/70_v_ian_shelters_down.sql. Cones are the data
--   lane's NHC archive in {{ops}}.storm_advisories (read only).
-- Parameters: @county_fips STRING, @max_km FLOAT64, @advisory STRING
-- Output: shelter_id, name, lat, lon, evacuation_capacity, reason, flood_share, flood_freq, down_rank
-- Sample row: "70851", "Harborside Events Center Special Needs", 26.64, -81.87, 300, "IN_CONE_AND_FLOOD_EXPOSED", 0.62, 0.11, 1
WITH reach AS (
  SELECT DISTINCT s.shelter_id
  FROM `{{dataset}}.tract_scores` t
  JOIN `{{dataset}}.shelter_evidence` s
    ON ST_DWITHIN(ST_GEOGPOINT(t.lon, t.lat), ST_GEOGPOINT(s.lon, s.lat), @max_km * 1000)
  WHERE t.county_fips = @county_fips AND t.total_pop > 0 AND t.lat IS NOT NULL AND s.usage IN ('EVAC', 'BOTH')
),
s AS (
  SELECT e.shelter_id, e.name, e.lat, e.lon, e.evacuation_capacity, e.in_surge_slosh_area,
         SAFE_DIVIDE(h.CFLD_EXPP, h.POPULATION) AS flood_share, h.CFLD_AFREQ AS flood_freq
  FROM `{{dataset}}.shelter_evidence` e
  JOIN reach USING (shelter_id)
  LEFT JOIN `{{dataset}}.hazard_tracts` h ON h.geo_id = e.nearest_geo_id
),
adv AS (
  SELECT cone FROM `{{ops}}.storm_advisories` WHERE storm_id = 'AL092022' AND advisory = @advisory
)
SELECT s.shelter_id, s.name, s.lat, s.lon, s.evacuation_capacity,
       IF(s.in_surge_slosh_area = 'yes', 'IN_SURGE_ZONE', 'IN_CONE_AND_FLOOD_EXPOSED') AS reason,
       ROUND(s.flood_share, 4) AS flood_share, ROUND(s.flood_freq, 4) AS flood_freq,
       ROW_NUMBER() OVER (ORDER BY s.in_surge_slosh_area = 'yes' DESC, s.flood_freq DESC, s.flood_share DESC,
                                   s.evacuation_capacity DESC, s.shelter_id) AS down_rank
FROM s
LEFT JOIN adv ON TRUE
WHERE (s.in_surge_slosh_area = 'yes' OR s.flood_share >= 0.5)
  AND (@advisory = 'ANY' OR (adv.cone IS NOT NULL AND ST_CONTAINS(adv.cone, ST_GEOGPOINT(s.lon, s.lat))))
ORDER BY down_rank
