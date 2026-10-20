-- 15_latest_assessments.sql
-- Purpose: the newest field assessment per shelter, for a list of shelters or a whole county.
--   Pass @shelter_ids (may be empty) and/or @county_fips (may be NULL). Rows with neither a
--   shelter in the list nor a shelter in the county are left out. Test rows whose shelter_id is
--   not in shelter_evidence (for example FL-TRAP-TEST-001) never match a county.
-- Parameters: @shelter_ids ARRAY<STRING>, @county_fips STRING
SELECT a.* EXCEPT (rn)
FROM (
  SELECT
    a.assessment_id, a.shelter_id, a.shelter_name, a.county, a.operator_notes,
    a.wheelchair_unaccompanied_entries, a.ramp_unlocked_24_7, a.generator_powers_medical_outlets,
    a.verified_status, a.assessed_at, a.assessor_role, a.source,
    e.county_fips,
    e.wheelchair_accessible AS record_wheelchair,
    ROW_NUMBER() OVER (PARTITION BY a.shelter_id ORDER BY a.assessed_at DESC) AS rn
  FROM `{{dataset}}.shelter_assessments` a
  LEFT JOIN `{{dataset}}.shelter_evidence` e ON e.shelter_id = a.shelter_id
  WHERE a.shelter_id IN UNNEST(@shelter_ids)
     OR (@county_fips IS NOT NULL AND e.county_fips = @county_fips)
) a
WHERE a.rn = 1
