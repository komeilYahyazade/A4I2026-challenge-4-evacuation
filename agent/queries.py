"""SQL for the readiness tools. Every value is a named query parameter (@name).

${...} placeholders are identifiers only: table names from config, or columns discovered from
INFORMATION_SCHEMA. Three-state flags are computed here so UNRECORDED survives to the answer.
"""

# Shelter columns shared by every shelter-returning query. Alias `s`.
SHELTER_COLUMNS = """
  CAST(s.shelter_id AS STRING) AS shelter_id,
  s.shelter_name,
  s.address_1,
  s.city,
  s.zip,
  s.county_fips,
  s.county_name,
  s.latitude,
  s.longitude,
  s.usage,
  IFNULL(s.usage IN UNNEST(@pre_landfall_usage), FALSE) AS is_pre_landfall,
  s.evacuation_capacity,
  s.post_impact_capacity,
  -- Blank capacity, or 0 on a shelter that is (or may be) pre-landfall, means nobody recorded it.
  -- A 0 on a POST-only shelter is a legitimate value. A negative number (FEMA's -1) never is.
  CASE WHEN s.evacuation_capacity IS NULL OR s.evacuation_capacity < 0 THEN 'UNRECORDED'
       WHEN s.evacuation_capacity = 0 AND IFNULL(s.usage IN UNNEST(@pre_landfall_usage), TRUE) THEN 'UNRECORDED'
       ELSE 'RECORDED' END AS capacity_status,
  IFNULL(s.evacuation_capacity > @capacity_max, FALSE) AS capacity_implausible,
  CASE WHEN s.wheelchair_accessible IS TRUE THEN 'YES'
       WHEN s.wheelchair_accessible IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS wheelchair_status,
  CASE WHEN s.generator_onsite IS TRUE THEN 'YES'
       WHEN s.generator_onsite IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS generator_status,
  CASE WHEN s.in_surge_slosh_area IS TRUE THEN 'YES'
       WHEN s.in_surge_slosh_area IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS surge_status,
  CASE WHEN s.in_100_yr_floodplain IS TRUE THEN 'YES'
       WHEN s.in_100_yr_floodplain IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS floodplain_status,
  IFNULL(s.is_medical, FALSE) AS is_medical,
  -- The FEMA record's own name says the building is out of use, yet it may still be designated.
  REGEXP_CONTAINS(UPPER(IFNULL(s.shelter_name, '')), r'\\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\\b')
    AS name_says_inactive,
  s.pet_accommodations_code"""

FIND_COUNTY = """
SELECT
  c.geo_id AS county_fips,
  c.county_name,
  ROUND(ST_Y(ST_CENTROID(c.county_geom)), 5) AS lat,
  ROUND(ST_X(ST_CENTROID(c.county_geom)), 5) AS lng
FROM ${counties} AS c
WHERE c.state_fips_code = @state_fips
  AND (c.geo_id = @county_fips
       OR (@name != '' AND LOWER(c.county_name) LIKE CONCAT('%', LOWER(@name), '%')))
ORDER BY LOWER(c.county_name) = LOWER(@name) DESC, c.county_name
LIMIT 10
"""

COUNTY_SHELTERS = """
SELECT ${shelter_columns}
FROM ${shelters} AS s
WHERE s.county_fips = @county_fips
ORDER BY s.shelter_name
"""

SHELTERS_BY_ID = """
SELECT ${shelter_columns}
FROM ${shelters} AS s
WHERE CAST(s.shelter_id AS STRING) IN UNNEST(@shelter_ids)
"""

LOOKUP_SHELTER = """
SELECT ${shelter_columns}
FROM ${shelters} AS s
WHERE LOWER(s.shelter_name) LIKE CONCAT('%', LOWER(@name), '%')
  AND (@county_fips = '' OR s.county_fips = @county_fips)
ORDER BY s.shelter_name
LIMIT 20
"""

# Tract rankings, selected by @ranking:
#   no_vehicle     households without a vehicle (count), share as tiebreak. "Who cannot leave."
#   may_need_help  state z-scores of COUNTS: mobile homes, people 65+, people with a disability.
#                  "Who must leave and may need help."
#   composite_v0   the original ranking, kept for the before-and-after audit: four state z-scores of
#                  SHARES plus a walking penalty. An equity audit on Lee County showed it covered only
#                  14% of the county's car-less households in its top 15, because small tracts with
#                  high mobile-home and 65+ shares dominated it.
# Inputs are physical and demographic only. No accessibility field, no NRI composite, no race.
RANK_TRACTS = """
WITH v AS (
  SELECT
    t.geo_id,
    t.county_fips,
    t.lat,
    t.lon,
    t.total_pop,
    t.households,
    t.households_no_vehicle,
    SAFE_DIVIDE(t.households_no_vehicle, t.households) AS share_no_vehicle,
    t.pop_65_plus,
    IF(t.pct_age_65_plus < 0, NULL, t.pct_age_65_plus) AS pct_age_65_plus,
    IF(t.pct_disability < 0, NULL, t.pct_disability) AS pct_disability,
    IF(t.pct_mobile_homes < 0, NULL, t.pct_mobile_homes) AS pct_mobile_homes,
    ROUND(t.total_pop * IF(t.pct_disability < 0, NULL, t.pct_disability) / 100) AS est_people_with_disability,
    t.mobile_homes,
    t.group_quarters_pop,
    t.pct_mobility,
    t.pct_lacktrpt
  FROM ${tracts} AS t
  WHERE t.total_pop > 0 AND t.lat IS NOT NULL AND t.lon IS NOT NULL
),
z AS (
  SELECT
    v.*,
    AVG(v.share_no_vehicle) OVER () AS state_share_no_vehicle,
    AVG(v.pct_age_65_plus) OVER () AS state_pct_age_65_plus,
    AVG(v.pct_disability) OVER () AS state_pct_disability,
    AVG(v.pct_mobile_homes) OVER () AS state_pct_mobile_homes,
    SAFE_DIVIDE(v.share_no_vehicle - AVG(v.share_no_vehicle) OVER (),
                STDDEV(v.share_no_vehicle) OVER ()) AS z_no_vehicle,
    SAFE_DIVIDE(v.pct_age_65_plus - AVG(v.pct_age_65_plus) OVER (),
                STDDEV(v.pct_age_65_plus) OVER ()) AS z_age_65_plus,
    SAFE_DIVIDE(v.pct_disability - AVG(v.pct_disability) OVER (),
                STDDEV(v.pct_disability) OVER ()) AS z_disability,
    SAFE_DIVIDE(v.pct_mobile_homes - AVG(v.pct_mobile_homes) OVER (),
                STDDEV(v.pct_mobile_homes) OVER ()) AS z_mobile_homes,
    SAFE_DIVIDE(v.mobile_homes - AVG(v.mobile_homes) OVER (),
                STDDEV(v.mobile_homes) OVER ()) AS zc_mobile_homes,
    SAFE_DIVIDE(v.pop_65_plus - AVG(v.pop_65_plus) OVER (),
                STDDEV(v.pop_65_plus) OVER ()) AS zc_age_65_plus,
    SAFE_DIVIDE(v.est_people_with_disability - AVG(v.est_people_with_disability) OVER (),
                STDDEV(v.est_people_with_disability) OVER ()) AS zc_disability
  FROM v
),
c AS (
  SELECT z.*, ST_GEOGPOINT(z.lon, z.lat) AS g
  FROM z
  WHERE z.county_fips = @county_fips
),
s AS (
  SELECT
    sh.usage IN UNNEST(@pre_landfall_usage) AS is_pre_landfall,
    sh.wheelchair_accessible,
    ST_GEOGPOINT(sh.longitude, sh.latitude) AS g
  FROM ${shelters} AS sh
  WHERE sh.latitude IS NOT NULL AND sh.longitude IS NOT NULL
),
pairs AS (
  SELECT c.geo_id, s.is_pre_landfall, s.wheelchair_accessible, ST_DISTANCE(c.g, s.g) AS m
  FROM c
  LEFT JOIN s ON ST_DWITHIN(c.g, s.g, @car_m)
),
reach AS (
  SELECT
    geo_id,
    MIN(m) / 1000 AS km_any_shelter,
    MIN(IF(is_pre_landfall, m, NULL)) / 1000 AS km_pre_landfall_shelter,
    MIN(IF(is_pre_landfall AND wheelchair_accessible IS TRUE, m, NULL)) / 1000
      AS km_pre_landfall_wheelchair_recorded_yes,
    COUNTIF(m <= @walk_m) AS n_any_walkable,
    COUNTIF(is_pre_landfall AND m <= @proxy_m) AS n_pre_landfall_in_run,
    COUNTIF(is_pre_landfall AND m <= @proxy_m AND wheelchair_accessible IS TRUE)
      AS n_in_run_wheelchair_yes,
    COUNTIF(is_pre_landfall AND m <= @proxy_m AND wheelchair_accessible IS FALSE)
      AS n_in_run_wheelchair_no,
    COUNTIF(is_pre_landfall AND m <= @proxy_m AND wheelchair_accessible IS NULL)
      AS n_in_run_wheelchair_unrecorded
  FROM pairs
  GROUP BY geo_id
),
scored AS (
SELECT
  c.geo_id,
  c.county_fips,
  c.lat,
  c.lon,
  c.total_pop,
  c.households,
  c.households_no_vehicle,
  ROUND(c.share_no_vehicle, 4) AS share_no_vehicle,
  c.pop_65_plus,
  c.pct_age_65_plus,
  c.pct_disability,
  c.pct_mobile_homes,
  c.mobile_homes,
  c.est_people_with_disability,
  c.group_quarters_pop,
  c.pct_mobility,
  c.pct_lacktrpt,
  ROUND(c.state_share_no_vehicle, 4) AS state_share_no_vehicle,
  ROUND(c.state_pct_age_65_plus, 2) AS state_pct_age_65_plus,
  ROUND(c.state_pct_disability, 2) AS state_pct_disability,
  ROUND(c.state_pct_mobile_homes, 2) AS state_pct_mobile_homes,
  ROUND(r.km_any_shelter, 2) AS km_any_shelter,
  ROUND(r.km_pre_landfall_shelter, 2) AS km_pre_landfall_shelter,
  ROUND(r.km_pre_landfall_wheelchair_recorded_yes, 2) AS km_pre_landfall_wheelchair_recorded_yes,
  r.n_any_walkable,
  r.n_pre_landfall_in_run,
  r.n_in_run_wheelchair_yes,
  r.n_in_run_wheelchair_no,
  r.n_in_run_wheelchair_unrecorded,
  h.HRCN_EXPP AS hurricane_exposed_pop,
  h.CFLD_EXPP AS coastal_flood_exposed_pop,
  -- ranking inputs start
  ROUND(
    COALESCE(c.zc_mobile_homes, 0) + COALESCE(c.zc_age_65_plus, 0) + COALESCE(c.zc_disability, 0),
    3) AS may_need_help_score,
  ROUND(
    COALESCE(c.z_no_vehicle, 0) + COALESCE(c.z_age_65_plus, 0)
    + COALESCE(c.z_disability, 0) + COALESCE(c.z_mobile_homes, 0)
    + IF(r.n_any_walkable = 0, @no_walkable_penalty, 0),
    3) AS composite_v0_score
  -- ranking inputs end
FROM c
LEFT JOIN reach AS r USING (geo_id)
LEFT JOIN ${hazard} AS h USING (geo_id)
)
SELECT
  scored.geo_id,
  scored.county_fips,
  scored.lat,
  scored.lon,
  scored.total_pop,
  scored.households,
  scored.households_no_vehicle,
  scored.share_no_vehicle,
  scored.pop_65_plus,
  scored.pct_age_65_plus,
  scored.pct_disability,
  scored.pct_mobile_homes,
  scored.mobile_homes,
  scored.est_people_with_disability,
  scored.group_quarters_pop,
  scored.pct_mobility,
  scored.pct_lacktrpt,
  scored.state_share_no_vehicle,
  scored.state_pct_age_65_plus,
  scored.state_pct_disability,
  scored.state_pct_mobile_homes,
  scored.km_any_shelter,
  scored.km_pre_landfall_shelter,
  scored.km_pre_landfall_wheelchair_recorded_yes,
  scored.n_any_walkable,
  scored.n_pre_landfall_in_run,
  scored.n_in_run_wheelchair_yes,
  scored.n_in_run_wheelchair_no,
  scored.n_in_run_wheelchair_unrecorded,
  scored.hurricane_exposed_pop,
  scored.coastal_flood_exposed_pop,
  scored.may_need_help_score,
  scored.composite_v0_score,
  @ranking AS ranking,
  CASE @ranking
    WHEN 'no_vehicle' THEN IFNULL(scored.households_no_vehicle, 0)
    WHEN 'may_need_help' THEN scored.may_need_help_score
    ELSE scored.composite_v0_score
  END AS priority_score
FROM scored
ORDER BY priority_score DESC, scored.share_no_vehicle DESC, scored.geo_id
LIMIT @limit
"""

COUNTY_NO_VEHICLE_TOTAL = """
SELECT
  SUM(households_no_vehicle) AS households_no_vehicle,
  SUM(households) AS households,
  SUM(mobile_homes) AS mobile_homes,
  SUM(pop_65_plus) AS pop_65_plus
FROM ${tracts}
WHERE county_fips = @county_fips AND total_pop > 0
"""

SHELTERS_FOR_TRACT = """
WITH t AS (
  SELECT geo_id, lat, lon, ST_GEOGPOINT(lon, lat) AS g
  FROM ${tracts}
  WHERE geo_id = @geo_id AND lat IS NOT NULL AND lon IS NOT NULL
)
SELECT
  ${shelter_columns},
  ROUND(ST_DISTANCE(t.g, ST_GEOGPOINT(s.longitude, s.latitude)) / 1000, 2) AS straight_line_km,
  t.geo_id AS origin_geo_id,
  t.lat AS origin_lat,
  t.lon AS origin_lng
FROM t
JOIN ${shelters} AS s
  ON s.latitude IS NOT NULL
 AND s.longitude IS NOT NULL
 AND ST_DWITHIN(t.g, ST_GEOGPOINT(s.longitude, s.latitude), @car_m)
WHERE s.usage IN UNNEST(@pre_landfall_usage)
ORDER BY straight_line_km
LIMIT @limit
"""

TRACT_ORIGIN = """
SELECT geo_id, lat, lon, households, households_no_vehicle
FROM ${tracts}
WHERE geo_id = @geo_id
"""

POWER_DEPENDENCE = """
SELECT
  ${fips} AS county_fips,
  SAFE_CAST(${dme} AS INT64) AS power_dependent_dme,
  ${censored} AS power_dependent_is_censored
FROM ${power}
WHERE LPAD(CAST(${fips} AS STRING), 5, '0') = @county_fips
"""

# care_facilities carries coordinates but no county, so place each one in its county polygon.
CARE_FACILITIES = """
WITH county AS (
  SELECT county_geom FROM ${counties} WHERE geo_id = @county_fips
),
cf AS (
  SELECT
    CAST(${cf_id} AS STRING) AS facility_id,
    ${cf_name} AS facility_name,
    ${cf_city} AS city,
    SAFE_CAST(${cf_beds} AS FLOAT64) AS certified_beds,
    SAFE_CAST(${cf_lat} AS FLOAT64) AS latitude,
    SAFE_CAST(${cf_lon} AS FLOAT64) AS longitude
  FROM ${care}
  WHERE ${cf_lat} IS NOT NULL AND ${cf_lon} IS NOT NULL
),
inside AS (
  SELECT cf.*, ST_GEOGPOINT(cf.longitude, cf.latitude) AS g
  FROM cf
  CROSS JOIN county
  WHERE ST_CONTAINS(county.county_geom, ST_GEOGPOINT(cf.longitude, cf.latitude))
),
gen AS (
  SELECT ST_GEOGPOINT(longitude, latitude) AS g, IFNULL(is_medical, FALSE) AS is_medical
  FROM ${shelters}
  WHERE generator_onsite IS TRUE
    AND usage IN UNNEST(@pre_landfall_usage)
    AND latitude IS NOT NULL AND longitude IS NOT NULL
)
SELECT
  i.facility_id,
  i.facility_name,
  i.city,
  i.certified_beds,
  i.latitude,
  i.longitude,
  ROUND(MIN(ST_DISTANCE(i.g, gen.g)) / 1000, 2) AS km_to_generator_recorded_shelter,
  ROUND(MIN(IF(gen.is_medical, ST_DISTANCE(i.g, gen.g), NULL)) / 1000, 2)
    AS km_to_medical_generator_recorded_shelter
FROM inside AS i
LEFT JOIN gen ON ST_DWITHIN(i.g, gen.g, @car_m)
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY i.certified_beds DESC
"""

# Race and ethnicity enter here and only here: as an audit of the output, never as an input.
EQUITY_AUDIT = """
WITH acs AS (
  SELECT
    LPAD(CAST(a.geo_id AS STRING), 11, '0') AS geo_id,
    SAFE_CAST(a.total_pop AS FLOAT64) AS total_pop,
    ${acs_measures}
  FROM `${acs_dataset}.${acs_table}` AS a
  WHERE STARTS_WITH(LPAD(CAST(a.geo_id AS STRING), 11, '0'), @state_fips)
),
labeled AS (
  SELECT 'recommended' AS cohort, acs.* FROM acs WHERE geo_id IN UNNEST(@geo_ids)
  UNION ALL
  SELECT 'county' AS cohort, acs.* FROM acs WHERE STARTS_WITH(geo_id, @county_fips)
  UNION ALL
  SELECT 'state' AS cohort, acs.* FROM acs
)
SELECT
  cohort,
  COUNT(*) AS tracts,
  SUM(total_pop) AS population,
  ${acs_shares}
FROM labeled
GROUP BY cohort
"""

# Does the plan land where people cannot leave, or where somebody filled in the form?
REPORTING_BIAS = """
WITH rec AS (
  SELECT ST_GEOGPOINT(lon, lat) AS g
  FROM ${tracts}
  WHERE geo_id IN UNNEST(@geo_ids) AND lat IS NOT NULL AND lon IS NOT NULL
),
near AS (
  SELECT DISTINCT CAST(s.shelter_id AS STRING) AS shelter_id, s.wheelchair_accessible
  FROM ${shelters} AS s
  JOIN rec ON ST_DWITHIN(rec.g, ST_GEOGPOINT(s.longitude, s.latitude), @proxy_m)
  WHERE s.usage IN UNNEST(@pre_landfall_usage)
)
SELECT 'near_recommended' AS cohort, COUNT(*) AS shelters,
       COUNTIF(wheelchair_accessible IS NOT NULL) AS wheelchair_recorded
FROM near
UNION ALL
SELECT 'county' AS cohort, COUNT(*) AS shelters,
       COUNTIF(wheelchair_accessible IS NOT NULL) AS wheelchair_recorded
FROM ${shelters}
WHERE county_fips = @county_fips AND usage IN UNNEST(@pre_landfall_usage)
UNION ALL
SELECT 'state' AS cohort, COUNT(*) AS shelters,
       COUNTIF(wheelchair_accessible IS NOT NULL) AS wheelchair_recorded
FROM ${shelters}
WHERE usage IN UNNEST(@pre_landfall_usage)
"""

# Data-lane view: FEMA NRI exposure of each shelter's nearest 2020 tract centroid. APPROXIMATE.
SITE_HAZARD = """
SELECT
  shelter_id,
  nearest_geo_id,
  km_to_centroid,
  coastal_flood_exposed_share,
  coastal_flood_annual_frequency,
  hurricane_annual_frequency,
  site_coastal_flood_status,
  method
FROM ${site_hazard}
WHERE shelter_id IN UNNEST(@shelter_ids)
"""
