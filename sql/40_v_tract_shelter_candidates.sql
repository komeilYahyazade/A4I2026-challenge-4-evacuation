-- The edge list the OR-Tools allocator solves over: each populated tract to its 15 nearest
-- pre-landfall shelters within the search radius, in ANY county (people cross county lines).
-- Filter with WHERE county_fips = @county_fips (the tract's county).
-- Distance is STRAIGHT-LINE from the tract centroid: a lower bound on road distance.
--
-- MATERIALIZED: the logic lives in the view _v_tract_shelter_candidates_src; the name the agent
-- reads, v_tract_shelter_candidates, is a table rebuilt from it on every build_views.sh run
-- (3 s view -> sub-second table, clustered on county_fips). This file sorts after 10/20/30, so a
-- full build always refreshes it after its inputs. Rebuilding only 10 or 30 leaves it stale.
CREATE OR REPLACE VIEW `${PROJECT}.${OPS}._v_tract_shelter_candidates_src`
OPTIONS (description = "Source logic for the materialized v_tract_shelter_candidates table.")
AS
WITH t AS (
  SELECT geo_id, county_fips, ST_GEOGPOINT(lon, lat) AS g
  FROM `${PROJECT}.${SRC}.vulnerability_tracts`
  WHERE total_pop > 0 AND lat IS NOT NULL AND lon IS NOT NULL
),
s AS (
  SELECT e.*, ST_GEOGPOINT(e.longitude, e.latitude) AS g
  FROM `${PROJECT}.${OPS}.v_shelter_evidence` AS e
  WHERE e.is_pre_landfall
),
pairs AS (
  SELECT
    t.geo_id,
    t.county_fips,
    s.shelter_id,
    s.shelter_name,
    s.county_fips AS shelter_county_fips,
    s.latitude AS shelter_lat,
    s.longitude AS shelter_lng,
    ST_DISTANCE(t.g, s.g) / 1000 AS km,
    s.usage,
    s.is_pre_landfall,
    s.capacity,
    s.capacity_status,
    s.capacity_implausible,
    s.wheelchair_status,
    s.generator_status,
    s.surge_status,
    s.is_medical,
    s.name_says_inactive
  FROM t
  JOIN s ON ST_DWITHIN(t.g, s.g, ${CAR_KM} * 1000)
),
ranked AS (
  SELECT pairs.*, ROW_NUMBER() OVER (PARTITION BY geo_id ORDER BY km, shelter_id) AS rank_k
  FROM pairs
)
SELECT
  r.geo_id,
  r.county_fips,
  r.shelter_id,
  r.shelter_name,
  r.shelter_county_fips,
  r.shelter_lat,
  r.shelter_lng,
  ROUND(r.km, 3) AS straight_line_km,
  r.rank_k,
  r.usage,
  r.is_pre_landfall,
  r.capacity AS evacuation_capacity,
  r.capacity_status,
  r.capacity_implausible,
  r.wheelchair_status,
  r.generator_status,
  r.surge_status,
  r.is_medical,
  r.name_says_inactive,
  r.km <= ${WALK_KM} AS within_walk,
  r.km <= ${PROXY_KM} AS within_paratransit_proxy,
  h.site_coastal_flood_status,
  h.coastal_flood_exposed_share AS site_coastal_flood_exposed_share,
  h.coastal_flood_exposed_pop AS site_coastal_flood_exposed_pop,
  h.hurricane_exposed_pop AS site_hurricane_exposed_pop
FROM ranked AS r
LEFT JOIN `${PROJECT}.${OPS}.v_shelter_site_hazard` AS h USING (shelter_id)
WHERE r.rank_k <= 15;

-- One-time migration: this name used to be a view. CREATE OR REPLACE TABLE cannot replace a view.
IF EXISTS (SELECT 1 FROM `${PROJECT}.${OPS}.INFORMATION_SCHEMA.TABLES`
           WHERE table_name = 'v_tract_shelter_candidates' AND table_type = 'VIEW') THEN
  DROP VIEW `${PROJECT}.${OPS}.v_tract_shelter_candidates`;
END IF;

CREATE OR REPLACE TABLE `${PROJECT}.${OPS}.v_tract_shelter_candidates`
CLUSTER BY county_fips
OPTIONS (description = "MATERIALIZED from _v_tract_shelter_candidates_src by scripts/build_views.sh. Tract -> 15 nearest EVAC/BOTH shelters within the search radius. Straight-line km.")
AS SELECT * FROM `${PROJECT}.${OPS}._v_tract_shelter_candidates_src`;
