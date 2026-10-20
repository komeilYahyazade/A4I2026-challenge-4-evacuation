-- Hurricane Ian (2022) tabletop replay: which pre-landfall shelters in Lee and its neighbours
-- (Charlotte, Collier, Glades, Hendry: the counties whose polygons touch Lee's) would be taken
-- offline at each NHC advisory. Rehearsal input only: never used with Google Maps.
--
-- A shelter goes down when it meets the hazard criterion AND (for a real advisory) sits inside
-- that advisory's 5-day cone:
--   IN_SURGE_ZONE       FEMA in_surge_slosh_area = YES                      (RECORDED)
--   IN_CONE_AND_SURGE   nearest tract has >= 50% of its people exposed to   (APPROXIMATE)
--                       coastal flooding (FEMA NRI), inside the cone
--   COASTAL_FLOOD_TRACT same exposure test with no cone: advisory = 'ANY',  (APPROXIMATE)
--                       the cone-agnostic list for an August drill
-- The 50% cut (SITE_FLOOD_SHARE_HIGH in agent/reachability.py, substituted by build_views.sh) is a
-- data-lane hazard decision: "most of the neighbourhood floods". The FEMA surge
-- flag alone cannot carry this, because it is blank for 81 of Lee's 84 pre-landfall shelters.
--
-- down_rank is deterministic: FEMA surge flag first, then flood frequency, exposed share,
-- capacity, id. down_rank_with_capacity ranks only shelters whose capacity was recorded, so
-- "take the top 2 offline" visibly changes a capacity-constrained plan.
CREATE OR REPLACE VIEW `${PROJECT}.${OPS}.v_ian_shelters_down`
OPTIONS (description = "Ian replay: shelters taken offline per NHC advisory (Lee + neighbours). Rehearsal only.")
AS
WITH region AS (
  SELECT e.*, h.coastal_flood_exposed_share, h.coastal_flood_annual_frequency,
         ST_GEOGPOINT(e.longitude, e.latitude) AS g
  FROM `${PROJECT}.${OPS}.v_shelter_evidence` AS e
  JOIN `${PROJECT}.${OPS}.v_shelter_site_hazard` AS h USING (shelter_id)
  WHERE e.is_pre_landfall
    AND e.county_fips IN ('12071', '12015', '12021', '12043', '12051')
    AND (e.surge_status = 'YES' OR h.coastal_flood_exposed_share >= ${FLOOD_SHARE})
),
advisories AS (
  SELECT storm_id, advisory, issued_at, hours_before_landfall, cone FROM `${PROJECT}.${OPS}.storm_advisories`
  UNION ALL
  SELECT 'AL092022', 'ANY', CAST(NULL AS TIMESTAMP), CAST(NULL AS FLOAT64), CAST(NULL AS GEOGRAPHY)
),
down AS (
  SELECT
    a.storm_id, a.advisory, a.issued_at, a.hours_before_landfall,
    r.*,
    IF(a.cone IS NULL, NULL, ST_CONTAINS(a.cone, r.g)) AS in_cone,
    CASE WHEN r.surge_status = 'YES' THEN 'IN_SURGE_ZONE'
         WHEN a.cone IS NULL THEN 'COASTAL_FLOOD_TRACT'
         ELSE 'IN_CONE_AND_SURGE' END AS reason
  FROM advisories AS a
  CROSS JOIN region AS r
  WHERE a.cone IS NULL OR ST_CONTAINS(a.cone, r.g)
)
SELECT
  storm_id,
  advisory,
  issued_at,
  hours_before_landfall,
  shelter_id,
  shelter_name,
  county_fips,
  latitude AS lat,
  longitude AS lng,
  capacity,
  capacity AS evacuation_capacity,
  capacity_status,
  surge_status,
  ROUND(coastal_flood_exposed_share, 4) AS site_coastal_flood_exposed_share,
  ROUND(coastal_flood_annual_frequency, 4) AS site_coastal_flood_annual_frequency,
  in_cone,
  reason,
  IF(reason = 'IN_SURGE_ZONE', 'RECORDED: FEMA in_surge_slosh_area = YES',
     'APPROXIMATE: FEMA NRI coastal-flood exposure of the nearest 2020 tract centroid') AS evidence,
  ROW_NUMBER() OVER w AS down_rank,
  IF(capacity_status = 'RECORDED',
     ROW_NUMBER() OVER (PARTITION BY advisory, capacity_status = 'RECORDED' ORDER BY
                        surge_status = 'YES' DESC, coastal_flood_annual_frequency DESC,
                        coastal_flood_exposed_share DESC, capacity DESC, shelter_id),
     NULL) AS down_rank_with_capacity
FROM down
WINDOW w AS (PARTITION BY advisory ORDER BY surge_status = 'YES' DESC,
             coastal_flood_annual_frequency DESC, coastal_flood_exposed_share DESC,
             capacity DESC NULLS LAST, shelter_id);
