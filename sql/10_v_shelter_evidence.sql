-- One row per registered shelter, with every flag as YES / NO / UNRECORDED.
-- Same column names and meaning as agent/queries.py SHELTER_COLUMNS, plus three columns that
-- keep "nobody wrote it down" visible: usage_status, pre_landfall_status, capacity_status.
CREATE OR REPLACE VIEW `${PROJECT}.${OPS}.v_shelter_evidence`
OPTIONS (description = "FEMA NSS shelters with three-state flags. RECORDED (historical registry), not live status.")
AS
SELECT
  CAST(s.shelter_id AS STRING) AS shelter_id,
  s.shelter_name,
  s.address_1,
  s.city,
  s.zip,
  s.county_fips,
  s.county_name,
  s.latitude,
  s.longitude,
  NULLIF(TRIM(s.usage), '') AS usage,
  IFNULL(NULLIF(TRIM(s.usage), ''), 'UNRECORDED') AS usage_status,
  IFNULL(TRIM(s.usage) IN (${PRE_LANDFALL}), FALSE) AS is_pre_landfall,
  CASE WHEN NULLIF(TRIM(s.usage), '') IS NULL THEN 'UNRECORDED'
       WHEN TRIM(s.usage) IN (${PRE_LANDFALL}) THEN 'YES' ELSE 'NO' END AS pre_landfall_status,
  s.evacuation_capacity,
  s.post_impact_capacity,
  -- A 0 on a POST shelter is real (it carries post_impact_capacity instead). A 0 or NULL on a
  -- shelter used before landfall means the number was never filled in. A negative number (FEMA
  -- files -1 in some states) is never a capacity: unrecorded, and kept out of every SUM.
  CASE WHEN s.evacuation_capacity IS NULL OR s.evacuation_capacity < 0 THEN NULL
       WHEN s.evacuation_capacity = 0 AND IFNULL(TRIM(s.usage) IN (${PRE_LANDFALL}), TRUE) THEN NULL
       ELSE CAST(s.evacuation_capacity AS INT64) END AS capacity,
  CASE WHEN s.evacuation_capacity IS NULL OR s.evacuation_capacity < 0 THEN 'UNRECORDED'
       WHEN s.evacuation_capacity = 0 AND IFNULL(TRIM(s.usage) IN (${PRE_LANDFALL}), TRUE) THEN 'UNRECORDED'
       ELSE 'RECORDED' END AS capacity_status,
  IFNULL(s.evacuation_capacity > ${CAPACITY_MAX}, FALSE) AS capacity_implausible,
  CASE WHEN s.wheelchair_accessible IS TRUE THEN 'YES'
       WHEN s.wheelchair_accessible IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS wheelchair_status,
  CASE WHEN s.generator_onsite IS TRUE THEN 'YES'
       WHEN s.generator_onsite IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS generator_status,
  CASE WHEN s.in_surge_slosh_area IS TRUE THEN 'YES'
       WHEN s.in_surge_slosh_area IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS surge_status,
  CASE WHEN s.in_100_yr_floodplain IS TRUE THEN 'YES'
       WHEN s.in_100_yr_floodplain IS FALSE THEN 'NO' ELSE 'UNRECORDED' END AS floodplain_status,
  IFNULL(s.is_medical, FALSE) AS is_medical,
  NULLIF(TRIM(s.pet_accommodations_code), '') AS pet_accommodations_code,
  -- FEMA's own name field says so ("INACTIVE - ...", "(CLOSED)", "Do Not Use"): 148 rows in FL, 24
  -- still marked EVAC/BOTH. Flagged, never dropped: the record is FEMA's, the check is Maps'.
  -- Same expression as agent/queries.py SHELTER_COLUMNS, so both report the same count.
  REGEXP_CONTAINS(UPPER(IFNULL(s.shelter_name, '')), r'\b(INACTIVE|CLOSED|DEMOLISHED|DO NOT USE)\b') AS name_says_inactive,
  -- The same building filed more than once: same name and address after TRIM and UPPER (the
  -- notebook's grain check, 25 groups in FL). Flagged, never dropped. The lowest shelter_id in a
  -- group is the record we keep counting; the others carry is_duplicate_record = TRUE.
  COUNT(*) OVER (PARTITION BY UPPER(TRIM(IFNULL(s.shelter_name, ''))), UPPER(TRIM(IFNULL(s.address_1, ''))))
    AS duplicate_group_size,
  ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(IFNULL(s.shelter_name, ''))), UPPER(TRIM(IFNULL(s.address_1, '')))
                     ORDER BY s.shelter_id) > 1 AS is_duplicate_record
FROM `${PROJECT}.${SRC}.shelters` AS s
WHERE s.latitude IS NOT NULL AND s.longitude IS NOT NULL;
