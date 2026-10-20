-- One row per populated census tract (2020 boundaries), statewide. The people side of the
-- problem: who cannot drive themselves out, as counts the allocator can move.
-- No ranking here on purpose: the priority score lives in agent/queries.py RANK_TRACTS only.
CREATE OR REPLACE VIEW `${PROJECT}.${OPS}.v_tract_need`
OPTIONS (description = "Tract need: ACS vehicle ownership converted to persons, SVI/PLACES shares, NRI exposure.")
AS
WITH t AS (
  SELECT
    v.geo_id,
    v.county_fips,
    v.lat,
    v.lon,
    v.total_pop,
    v.households,
    v.households_no_vehicle,
    v.group_quarters_pop,
    SAFE_DIVIDE(v.households_no_vehicle, v.households) AS share_no_vehicle,
    -- Household size excludes group quarters: dorms, prisons and nursing homes are not households.
    IF(v.households > 0, (v.total_pop - IFNULL(v.group_quarters_pop, 0)) / v.households, NULL)
      AS persons_per_household,
    v.pop_65_plus,
    IF(v.pct_age_65_plus < 0, NULL, v.pct_age_65_plus) AS pct_age_65_plus,
    IF(v.pct_disability < 0, NULL, v.pct_disability) AS pct_disability,
    IF(v.pct_mobile_homes < 0, NULL, v.pct_mobile_homes) AS pct_mobile_homes,
    v.mobile_homes,
    IF(v.pct_mobility < 0, NULL, v.pct_mobility) AS pct_mobility,
    IF(v.pct_no_vehicle < 0, NULL, v.pct_no_vehicle) AS pct_no_vehicle_svi,
    ST_GEOGPOINT(v.lon, v.lat) AS g
  FROM `${PROJECT}.${SRC}.vulnerability_tracts` AS v
  WHERE v.total_pop > 0 AND v.lat IS NOT NULL AND v.lon IS NOT NULL
),
usage_unrecorded AS (
  -- Shelters nobody designated EVAC, POST or BOTH. They cannot be planned on, but they exist.
  SELECT t.geo_id, COUNT(*) AS n
  FROM t
  JOIN `${PROJECT}.${OPS}.v_shelter_evidence` AS e
    ON e.usage_status = 'UNRECORDED'
   AND ST_DWITHIN(t.g, ST_GEOGPOINT(e.longitude, e.latitude), ${PROXY_KM} * 1000)
  GROUP BY t.geo_id
)
SELECT
  t.geo_id,
  t.county_fips,
  t.lat,
  t.lon,
  t.total_pop,
  t.households,
  t.households_no_vehicle,
  t.group_quarters_pop,
  ROUND(t.share_no_vehicle, 4) AS share_no_vehicle,
  ROUND(t.persons_per_household, 3) AS persons_per_household,
  ROUND(t.households_no_vehicle * t.persons_per_household, 1) AS est_persons_no_vehicle,
  t.pop_65_plus,
  t.pct_age_65_plus,
  t.pct_disability,
  t.pct_mobile_homes,
  t.mobile_homes,
  t.pct_mobility,
  t.pct_no_vehicle_svi,
  h.HRCN_EXPP AS hurricane_exposed_pop,
  h.CFLD_EXPP AS coastal_flood_exposed_pop,
  IFNULL(u.n, 0) AS n_usage_unrecorded_within_run
FROM t
LEFT JOIN `${PROJECT}.${SRC}.hazard_tracts` AS h USING (geo_id)
LEFT JOIN usage_unrecorded AS u USING (geo_id);
