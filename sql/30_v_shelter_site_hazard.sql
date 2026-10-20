-- The ground each shelter stands on. Shelters carry a county but no tract, so each one takes the
-- nearest 2020 tract centroid by ST_DISTANCE and that tract's FEMA NRI exposure.
-- APPROXIMATE: a centroid is not a boundary, and tract exposure is not site elevation. This is the
-- labelled stand-in for the FEMA surge flag, which is blank for most shelters.
CREATE OR REPLACE VIEW `${PROJECT}.${OPS}.v_shelter_site_hazard`
OPTIONS (description = "Shelter -> nearest tract centroid -> NRI hurricane/coastal-flood exposure. Approximate.")
AS
WITH s AS (
  SELECT shelter_id, ST_GEOGPOINT(longitude, latitude) AS g
  FROM `${PROJECT}.${OPS}.v_shelter_evidence`
),
t AS (
  SELECT geo_id, ST_GEOGPOINT(lon, lat) AS g
  FROM `${PROJECT}.${SRC}.vulnerability_tracts`
  WHERE lat IS NOT NULL AND lon IS NOT NULL
),
nearest AS (
  SELECT
    s.shelter_id,
    ARRAY_AGG(STRUCT(t.geo_id AS geo_id, ST_DISTANCE(s.g, t.g) AS m)
              ORDER BY ST_DISTANCE(s.g, t.g), t.geo_id LIMIT 1)[OFFSET(0)] AS n
  FROM s
  JOIN t ON ST_DWITHIN(s.g, t.g, 25000)
  GROUP BY s.shelter_id
)
SELECT
  s.shelter_id,
  n.n.geo_id AS nearest_geo_id,
  ROUND(n.n.m / 1000, 3) AS km_to_centroid,
  h.POPULATION AS tract_population,
  h.HRCN_EXPP AS hurricane_exposed_pop,
  h.HRCN_AFREQ AS hurricane_annual_frequency,
  h.CFLD_EXPP AS coastal_flood_exposed_pop,
  h.CFLD_AFREQ AS coastal_flood_annual_frequency,
  ROUND(SAFE_DIVIDE(h.CFLD_EXPP, h.POPULATION), 4) AS coastal_flood_exposed_share,
  CASE WHEN h.geo_id IS NULL OR h.CFLD_EXPP IS NULL THEN 'UNRECORDED'
       WHEN h.CFLD_EXPP > 0 THEN 'YES' ELSE 'NO' END AS site_coastal_flood_status,
  'nearest 2020 tract centroid by ST_DISTANCE (approximate)' AS method
FROM s
LEFT JOIN nearest AS n USING (shelter_id)
LEFT JOIN `${PROJECT}.${SRC}.hazard_tracts` AS h ON h.geo_id = n.n.geo_id;
