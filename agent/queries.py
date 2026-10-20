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
    (sh.usage IN UNNEST(@pre_landfall_usage)
      AND NOT REGEXP_CONTAINS(UPPER(IFNULL(sh.shelter_name, '')), r'\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b')) AS is_pre_landfall,
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
    AND NOT REGEXP_CONTAINS(UPPER(IFNULL(shelter_name, '')), r'\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b')
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

# ---------------------------------------------------------------------------
# Deduplicated shelter queries, county summaries, care-facility proximity,
# county-centroid fallback, map layers, and operator field assessments.
# ---------------------------------------------------------------------------

DEDUPLICATED_SHELTERS_CTE = """
dedup_shelters AS (
  SELECT
    s.*,
    COUNT(*) OVER (
      PARTITION BY UPPER(TRIM(IFNULL(s.shelter_name, ''))),
                   UPPER(TRIM(IFNULL(s.address_1, '')))
    ) AS duplicate_group_size,
    ROW_NUMBER() OVER (
      PARTITION BY UPPER(TRIM(IFNULL(s.shelter_name, ''))),
                   UPPER(TRIM(IFNULL(s.address_1, '')))
      ORDER BY
        IF(s.wheelchair_accessible IS NOT NULL, 0, 1),
        IF(s.generator_onsite IS NOT NULL, 0, 1),
        IF(s.usage IN UNNEST(@pre_landfall_usage), 0, 1),
        GREATEST(IFNULL(s.evacuation_capacity, 0), IFNULL(s.post_impact_capacity, 0)) DESC,
        s.shelter_id
    ) AS dedup_rank
  FROM ${shelters} AS s
)
"""

COUNTY_SHELTERS_DEDUP = (
    "WITH "
    + DEDUPLICATED_SHELTERS_CTE
    + """
SELECT
  ${shelter_columns},
  s.duplicate_group_size
FROM dedup_shelters AS s
WHERE s.dedup_rank = 1
  AND s.county_fips = @county_fips
ORDER BY s.shelter_name
"""
)

LIST_COUNTIES = """
WITH shelter_rollup AS (
  SELECT
    county_fips,
    ANY_VALUE(county_name) AS county_name,
    COUNT(*) AS total_shelter_rows,
    COUNT(DISTINCT CONCAT(UPPER(TRIM(IFNULL(shelter_name, ''))), '|', UPPER(TRIM(IFNULL(address_1, '')))))
      AS deduplicated_shelters,
    COUNTIF(usage IN ('EVAC', 'BOTH')) AS pre_landfall_shelters,
    COUNTIF(wheelchair_accessible IS TRUE) AS wheelchair_yes,
    COUNTIF(wheelchair_accessible IS FALSE) AS wheelchair_no,
    COUNTIF(wheelchair_accessible IS NULL) AS wheelchair_unrecorded,
    COUNTIF(generator_onsite IS TRUE) AS generator_yes,
    COUNTIF(generator_onsite IS FALSE) AS generator_no,
    COUNTIF(generator_onsite IS NULL) AS generator_unrecorded,
    COUNTIF(is_medical IS TRUE) AS medical_shelters
  FROM ${shelters}
  WHERE county_fips IS NOT NULL
  GROUP BY county_fips
),
tract_rollup AS (
  SELECT
    county_fips,
    COUNT(*) AS tract_count,
    SUM(total_pop) AS total_pop,
    SUM(households) AS total_households,
    SUM(households_no_vehicle) AS households_no_vehicle,
    SUM(pop_65_plus) AS pop_65_plus,
    SUM(mobile_homes) AS mobile_homes,
    ROUND(AVG(lat), 5) AS centroid_lat,
    ROUND(AVG(lon), 5) AS centroid_lon
  FROM ${tracts}
  WHERE total_pop > 0
  GROUP BY county_fips
)
SELECT
  t.county_fips,
  COALESCE(s.county_name, p.county, t.county_fips) AS county_name,
  t.tract_count,
  t.total_pop,
  t.total_households,
  t.households_no_vehicle,
  t.pop_65_plus,
  t.mobile_homes,
  t.centroid_lat AS lat,
  t.centroid_lon AS lng,
  IFNULL(s.total_shelter_rows, 0) AS total_shelter_rows,
  IFNULL(s.deduplicated_shelters, 0) AS deduplicated_shelters,
  IFNULL(s.pre_landfall_shelters, 0) AS pre_landfall_shelters,
  IFNULL(s.wheelchair_yes, 0) AS wheelchair_yes,
  IFNULL(s.wheelchair_no, 0) AS wheelchair_no,
  IFNULL(s.wheelchair_unrecorded, 0) AS wheelchair_unrecorded,
  IFNULL(s.generator_yes, 0) AS generator_yes,
  IFNULL(s.generator_no, 0) AS generator_no,
  IFNULL(s.generator_unrecorded, 0) AS generator_unrecorded,
  IFNULL(s.medical_shelters, 0) AS medical_shelters,
  SAFE_CAST(p.all_dme AS INT64) AS power_dependent_dme
FROM tract_rollup AS t
LEFT JOIN shelter_rollup AS s USING (county_fips)
LEFT JOIN ${power} AS p ON LPAD(CAST(p.fips_code AS STRING), 5, '0') = t.county_fips
ORDER BY t.households_no_vehicle DESC, t.county_fips
"""

COUNTY_SUMMARY = """
WITH county_tracts AS (
  SELECT
    county_fips,
    COUNT(*) AS tract_count,
    COUNTIF(lat IS NULL OR lon IS NULL) AS tracts_missing_coords,
    SUM(total_pop) AS total_pop,
    SUM(households) AS households,
    SUM(households_no_vehicle) AS households_no_vehicle,
    SUM(pop_65_plus) AS pop_65_plus,
    SUM(mobile_homes) AS mobile_homes,
    ROUND(AVG(lat), 5) AS centroid_lat,
    ROUND(AVG(lon), 5) AS centroid_lon
  FROM ${tracts}
  WHERE county_fips = @county_fips AND total_pop > 0
  GROUP BY county_fips
),
county_shelters_agg AS (
  SELECT
    county_fips,
    ANY_VALUE(county_name) AS county_name,
    COUNT(*) AS total_shelters,
    COUNT(DISTINCT CONCAT(UPPER(TRIM(IFNULL(shelter_name, ''))), '|', UPPER(TRIM(IFNULL(address_1, '')))))
      AS deduplicated_shelters,
    COUNTIF(usage IN UNNEST(@pre_landfall_usage)) AS pre_landfall_shelters,
    SUM(IF(usage IN UNNEST(@pre_landfall_usage)
           AND evacuation_capacity IS NOT NULL
           AND evacuation_capacity > 0
           AND evacuation_capacity <= @capacity_max
           AND NOT REGEXP_CONTAINS(UPPER(IFNULL(shelter_name, '')), r'\\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\\b'),
           evacuation_capacity, 0)) AS plausible_pre_landfall_capacity,
    COUNTIF(wheelchair_accessible IS TRUE) AS wheelchair_yes,
    COUNTIF(wheelchair_accessible IS FALSE) AS wheelchair_no,
    COUNTIF(wheelchair_accessible IS NULL) AS wheelchair_unrecorded,
    COUNTIF(generator_onsite IS TRUE) AS generator_yes,
    COUNTIF(generator_onsite IS FALSE) AS generator_no,
    COUNTIF(generator_onsite IS NULL) AS generator_unrecorded,
    COUNTIF(in_surge_slosh_area IS TRUE) AS surge_yes,
    COUNTIF(in_surge_slosh_area IS FALSE) AS surge_no,
    COUNTIF(in_surge_slosh_area IS NULL) AS surge_unrecorded,
    COUNTIF(in_100_yr_floodplain IS TRUE) AS floodplain_yes,
    COUNTIF(in_100_yr_floodplain IS FALSE) AS floodplain_no,
    COUNTIF(in_100_yr_floodplain IS NULL) AS floodplain_unrecorded,
    COUNTIF(is_medical IS TRUE) AS medical_shelters,
    COUNTIF(REGEXP_CONTAINS(UPPER(IFNULL(shelter_name, '')), r'\\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\\b'))
      AS name_flagged_inactive
  FROM ${shelters}
  WHERE county_fips = @county_fips
  GROUP BY county_fips
)
SELECT
  t.county_fips,
  COALESCE(s.county_name, p.county, t.county_fips) AS county_name,
  t.tract_count,
  t.tracts_missing_coords,
  t.total_pop,
  t.households,
  t.households_no_vehicle,
  t.pop_65_plus,
  t.mobile_homes,
  t.centroid_lat AS lat,
  t.centroid_lon AS lng,
  IFNULL(s.total_shelters, 0) AS total_shelters,
  IFNULL(s.deduplicated_shelters, 0) AS deduplicated_shelters,
  IFNULL(s.pre_landfall_shelters, 0) AS pre_landfall_shelters,
  IFNULL(s.plausible_pre_landfall_capacity, 0) AS plausible_pre_landfall_capacity,
  IFNULL(s.wheelchair_yes, 0) AS wheelchair_yes,
  IFNULL(s.wheelchair_no, 0) AS wheelchair_no,
  IFNULL(s.wheelchair_unrecorded, 0) AS wheelchair_unrecorded,
  IFNULL(s.generator_yes, 0) AS generator_yes,
  IFNULL(s.generator_no, 0) AS generator_no,
  IFNULL(s.generator_unrecorded, 0) AS generator_unrecorded,
  IFNULL(s.surge_yes, 0) AS surge_yes,
  IFNULL(s.surge_no, 0) AS surge_no,
  IFNULL(s.surge_unrecorded, 0) AS surge_unrecorded,
  IFNULL(s.floodplain_yes, 0) AS floodplain_yes,
  IFNULL(s.floodplain_no, 0) AS floodplain_no,
  IFNULL(s.floodplain_unrecorded, 0) AS floodplain_unrecorded,
  IFNULL(s.medical_shelters, 0) AS medical_shelters,
  IFNULL(s.name_flagged_inactive, 0) AS name_flagged_inactive,
  SAFE_CAST(p.all_dme AS INT64) AS power_dependent_dme
FROM county_tracts AS t
LEFT JOIN county_shelters_agg AS s USING (county_fips)
LEFT JOIN ${power} AS p ON LPAD(CAST(p.fips_code AS STRING), 5, '0') = t.county_fips
"""

CARE_FACILITIES_PROXIMITY = """
WITH county AS (
  SELECT county_geom FROM ${counties} WHERE geo_id = @county_fips
),
cf AS (
  SELECT
    CAST(ccn AS STRING) AS facility_id,
    facility_name,
    address,
    city,
    state,
    zip_code,
    SAFE_CAST(beds AS INT64) AS certified_beds,
    SAFE_CAST(avg_residents_per_day AS FLOAT64) AS avg_residents_per_day,
    SAFE_CAST(latitude AS FLOAT64) AS latitude,
    SAFE_CAST(longitude AS FLOAT64) AS longitude,
    ST_GEOGPOINT(SAFE_CAST(longitude AS FLOAT64), SAFE_CAST(latitude AS FLOAT64)) AS g
  FROM ${care}
  WHERE latitude IS NOT NULL AND longitude IS NOT NULL
),
inside AS (
  SELECT cf.*
  FROM cf
  CROSS JOIN county
  WHERE ST_CONTAINS(county.county_geom, cf.g)
),
gen_shelters AS (
  SELECT
    CAST(shelter_id AS STRING) AS shelter_id,
    shelter_name,
    IFNULL(is_medical, FALSE) AS is_medical,
    CASE WHEN wheelchair_accessible IS TRUE THEN 'YES'
         WHEN wheelchair_accessible IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS wheelchair_status,
    ST_GEOGPOINT(longitude, latitude) AS g
  FROM ${shelters}
  WHERE generator_onsite IS TRUE
    AND usage IN UNNEST(@pre_landfall_usage)
    AND NOT REGEXP_CONTAINS(UPPER(IFNULL(shelter_name, '')), r'\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b')
    AND latitude IS NOT NULL AND longitude IS NOT NULL
)
SELECT
  i.facility_id,
  i.facility_name,
  i.address,
  i.city,
  i.zip_code,
  i.certified_beds,
  i.avg_residents_per_day,
  i.latitude,
  i.longitude,
  ROUND(MIN(ST_DISTANCE(i.g, gs.g)) / 1000, 2) AS km_to_generator_recorded_shelter,
  ROUND(MIN(IF(gs.is_medical, ST_DISTANCE(i.g, gs.g), NULL)) / 1000, 2)
    AS km_to_medical_generator_recorded_shelter,
  ARRAY_AGG(
     IF(gs.shelter_id IS NOT NULL,
        STRUCT(gs.shelter_id, gs.shelter_name, gs.is_medical, gs.wheelchair_status,
               ROUND(ST_DISTANCE(i.g, gs.g) / 1000, 2) AS distance_km),
        NULL)
    IGNORE NULLS
    ORDER BY ST_DISTANCE(i.g, gs.g)
    LIMIT 3
  ) AS nearest_generator_shelters
FROM inside AS i
LEFT JOIN gen_shelters AS gs ON ST_DWITHIN(i.g, gs.g, @car_m)
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9
ORDER BY i.certified_beds DESC
"""

TRACTS_WITH_COUNTY_CENTROID_FALLBACK = """
WITH county_centroids AS (
  SELECT
    county_fips,
    AVG(lat) AS county_avg_lat,
    AVG(lon) AS county_avg_lon
  FROM ${tracts}
  WHERE lat IS NOT NULL AND lon IS NOT NULL
  GROUP BY county_fips
),
public_counties AS (
  SELECT
    geo_id AS county_fips,
    ST_Y(ST_CENTROID(county_geom)) AS public_county_lat,
    ST_X(ST_CENTROID(county_geom)) AS public_county_lon
  FROM ${counties}
  WHERE state_fips_code = @state_fips
)
SELECT
  t.geo_id,
  t.county_fips,
  COALESCE(t.lat, cc.county_avg_lat, pc.public_county_lat) AS lat,
  COALESCE(t.lon, cc.county_avg_lon, pc.public_county_lon) AS lon,
  CASE
    WHEN t.lat IS NOT NULL AND t.lon IS NOT NULL THEN 'TRACT_CENTROID'
    WHEN cc.county_avg_lat IS NOT NULL AND cc.county_avg_lon IS NOT NULL THEN 'COUNTY_TRACT_AVG_FALLBACK'
    WHEN pc.public_county_lat IS NOT NULL AND pc.public_county_lon IS NOT NULL THEN 'COUNTY_POLYGON_CENTROID_FALLBACK'
    ELSE 'MISSING'
  END AS coord_source,
  t.total_pop,
  t.households,
  t.households_no_vehicle,
  SAFE_DIVIDE(t.households_no_vehicle, t.households) AS share_no_vehicle,
  t.pop_65_plus,
  t.pct_age_65_plus,
  t.pct_disability,
  t.pct_mobile_homes,
  t.mobile_homes,
  t.group_quarters_pop,
  t.pct_mobility,
  t.pct_lacktrpt
FROM ${tracts} AS t
LEFT JOIN county_centroids AS cc USING (county_fips)
LEFT JOIN public_counties AS pc USING (county_fips)
WHERE t.total_pop > 0
  AND (@county_fips = '' OR t.county_fips = @county_fips)
ORDER BY t.households_no_vehicle DESC, t.geo_id
LIMIT @limit
"""

SHELTER_ASSESSMENTS_BY_COUNTY = """
SELECT
  shelter_id,
  shelter_name,
  county,
  operator_notes,
  CASE WHEN wheelchair_unaccompanied_entries IS TRUE THEN 'YES'
       WHEN wheelchair_unaccompanied_entries IS FALSE THEN 'NO'
       ELSE 'UNRECORDED' END AS wheelchair_unaccompanied_status,
  CASE WHEN ramp_unlocked_24_7 IS TRUE THEN 'YES'
       WHEN ramp_unlocked_24_7 IS FALSE THEN 'NO'
       ELSE 'UNRECORDED' END AS ramp_unlocked_status,
  CASE WHEN generator_powers_medical_outlets IS TRUE THEN 'YES'
       WHEN generator_powers_medical_outlets IS FALSE THEN 'NO'
       ELSE 'UNRECORDED' END AS generator_medical_outlets_status,
  wheelchair_unaccompanied_entries,
  ramp_unlocked_24_7,
  generator_powers_medical_outlets,
  verified_status,
  assessed_at
FROM ${shelter_assessments}
WHERE (@county = '' OR LOWER(county) = LOWER(@county))
ORDER BY assessed_at DESC
"""

INSERT_SHELTER_ASSESSMENT = """
INSERT INTO ${shelter_assessments} (
  shelter_id,
  shelter_name,
  county,
  operator_notes,
  wheelchair_unaccompanied_entries,
  ramp_unlocked_24_7,
  generator_powers_medical_outlets,
  verified_status,
  assessed_at
)
VALUES (
  @shelter_id,
  @shelter_name,
  @county,
  @operator_notes,
  @wheelchair_unaccompanied_entries,
  @ramp_unlocked_24_7,
  @generator_powers_medical_outlets,
  @verified_status,
  CURRENT_TIMESTAMP()
)
"""


def build_deduplicated_shelters_query(project_id: str, dataset: str) -> str:
    """Build parameterized BigQuery SQL for deduplicated county shelters."""
    shelters_fqn = f"`{project_id}.{dataset}.shelters`"
    return (
        COUNTY_SHELTERS_DEDUP.replace("${shelters}", shelters_fqn)
        .replace("${shelter_columns}", SHELTER_COLUMNS)
    )


def build_county_list_query(project_id: str, dataset: str) -> str:
    """Build BigQuery SQL listing all counties with shelter, tract, and DME summaries."""
    return (
        LIST_COUNTIES.replace("${shelters}", f"`{project_id}.{dataset}.shelters`")
        .replace("${tracts}", f"`{project_id}.{dataset}.vulnerability_tracts`")
        .replace("${power}", f"`{project_id}.{dataset}.power_dependent_counties`")
    )


def build_county_summary_query(project_id: str, dataset: str) -> str:
    """Build parameterized BigQuery SQL for a single county readiness summary."""
    return (
        COUNTY_SUMMARY.replace("${shelters}", f"`{project_id}.{dataset}.shelters`")
        .replace("${tracts}", f"`{project_id}.{dataset}.vulnerability_tracts`")
        .replace("${power}", f"`{project_id}.{dataset}.power_dependent_counties`")
    )


def build_care_facilities_proximity_query(
    project_id: str,
    dataset: str,
    counties_table: str = "bigquery-public-data.geo_us_boundaries.counties",
) -> str:
    """Build parameterized BigQuery SQL for nursing homes with nearest generator shelter distances."""
    return (
        CARE_FACILITIES_PROXIMITY.replace("${care}", f"`{project_id}.{dataset}.care_facilities`")
        .replace("${shelters}", f"`{project_id}.{dataset}.shelters`")
        .replace("${counties}", f"`{counties_table}`")
    )


def build_vulnerable_tracts_fallback_query(
    project_id: str,
    dataset: str,
    counties_table: str = "bigquery-public-data.geo_us_boundaries.counties",
) -> str:
    """Build parameterized BigQuery SQL for tracts with county-centroid fallback when lat/lon is NULL."""
    return (
        TRACTS_WITH_COUNTY_CENTROID_FALLBACK.replace(
            "${tracts}", f"`{project_id}.{dataset}.vulnerability_tracts`"
        ).replace("${counties}", f"`{counties_table}`")
    )


def build_shelter_assessments_query(project_id: str, dataset: str) -> str:
    """Build parameterized BigQuery SQL to fetch operator field assessments."""
    return SHELTER_ASSESSMENTS_BY_COUNTY.replace(
        "${shelter_assessments}", f"`{project_id}.{dataset}.shelter_assessments`"
    )


def build_map_layer_queries(
    project_id: str,
    dataset: str,
    counties_table: str = "bigquery-public-data.geo_us_boundaries.counties",
) -> dict[str, str]:
    """Return parameterized BigQuery SQL strings for shelters, tracts, and care facilities map layers."""
    return {
        "shelters": build_deduplicated_shelters_query(project_id, dataset),
        "tracts": build_vulnerable_tracts_fallback_query(project_id, dataset, counties_table),
        "care_facilities": build_care_facilities_proximity_query(project_id, dataset, counties_table),
    }

