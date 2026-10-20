-- Per county: what the registry can and cannot say. YES, NO and UNRECORDED are always separate
-- columns, so "11 I can speak to; 64 nobody has assessed" is one row away.
-- These count paperwork, not buildings. Never rank counties on them.
CREATE OR REPLACE VIEW `${PROJECT}.${OPS}.v_county_shelter_summary`
OPTIONS (description = "Per-county shelter record quality. Measures what was recorded, not what exists.")
AS
SELECT
  e.county_fips,
  ANY_VALUE(e.county_name) AS county_name,
  COUNT(*) AS shelters,
  COUNTIF(e.pre_landfall_status = 'YES') AS pre_landfall,
  COUNTIF(e.pre_landfall_status = 'NO') AS post_only,
  COUNTIF(e.pre_landfall_status = 'UNRECORDED') AS usage_unrecorded,
  -- Capacity follows the brief (agent/brief.py _shelter_summary): a shelter whose own FEMA name says
  -- inactive is never counted as spaces, and is reported separately instead.
  COUNTIF(e.is_pre_landfall AND e.capacity_status = 'RECORDED' AND NOT e.capacity_implausible
          AND NOT e.name_says_inactive) AS pre_landfall_capacity_recorded,
  COUNTIF(e.is_pre_landfall AND e.capacity_status = 'UNRECORDED' AND NOT e.name_says_inactive)
    AS pre_landfall_capacity_unrecorded,
  IFNULL(SUM(IF(e.is_pre_landfall AND e.capacity_status = 'RECORDED' AND NOT e.capacity_implausible
                AND NOT e.name_says_inactive, e.capacity, 0)), 0) AS pre_landfall_capacity_plausible,
  COUNTIF(e.is_pre_landfall AND e.name_says_inactive) AS pre_landfall_name_says_inactive,
  IFNULL(SUM(IF(e.is_pre_landfall AND e.name_says_inactive AND e.capacity_status = 'RECORDED', e.capacity, 0)), 0)
    AS pre_landfall_name_says_inactive_spaces,
  COUNTIF(e.capacity_implausible) AS capacity_implausible,
  -- Same building filed twice. Counted, not removed: totals above still include them, like the brief.
  COUNTIF(e.is_duplicate_record) AS duplicate_records,
  COUNTIF(e.is_pre_landfall AND e.is_duplicate_record) AS pre_landfall_duplicate_records,
  COUNTIF(e.is_pre_landfall AND e.wheelchair_status = 'YES') AS pre_landfall_wheelchair_yes,
  COUNTIF(e.is_pre_landfall AND e.wheelchair_status = 'NO') AS pre_landfall_wheelchair_no,
  COUNTIF(e.is_pre_landfall AND e.wheelchair_status = 'UNRECORDED') AS pre_landfall_wheelchair_unrecorded,
  COUNTIF(e.is_pre_landfall AND e.generator_status = 'YES') AS pre_landfall_generator_yes,
  COUNTIF(e.is_pre_landfall AND e.generator_status = 'NO') AS pre_landfall_generator_no,
  COUNTIF(e.is_pre_landfall AND e.generator_status = 'UNRECORDED') AS pre_landfall_generator_unrecorded,
  COUNTIF(e.is_pre_landfall AND e.surge_status = 'YES') AS pre_landfall_surge_yes,
  COUNTIF(e.is_pre_landfall AND e.surge_status = 'NO') AS pre_landfall_surge_no,
  COUNTIF(e.is_pre_landfall AND e.surge_status = 'UNRECORDED') AS pre_landfall_surge_unrecorded,
  COUNTIF(e.is_pre_landfall AND h.site_coastal_flood_status = 'YES') AS pre_landfall_site_coastal_flood_exposed,
  -- medical_needs counts every usage code, including POST, to match the brief's GENERATOR_GAP fix.
  -- The pre_landfall_ pair is the one to quote for an evacuation plan.
  COUNTIF(e.is_medical) AS medical_needs,
  COUNTIF(e.is_medical AND e.generator_status = 'YES') AS medical_needs_generator_yes,
  COUNTIF(e.is_pre_landfall AND e.is_medical) AS pre_landfall_medical_needs,
  COUNTIF(e.is_pre_landfall AND e.is_medical AND e.generator_status = 'YES') AS pre_landfall_medical_needs_generator_yes
FROM `${PROJECT}.${OPS}.v_shelter_evidence` AS e
LEFT JOIN `${PROJECT}.${OPS}.v_shelter_site_hazard` AS h USING (shelter_id)
GROUP BY e.county_fips;
