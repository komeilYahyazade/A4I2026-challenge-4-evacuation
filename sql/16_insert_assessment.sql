-- 16_insert_assessment.sql
-- Purpose: append one field assessment. Append-only: nothing ever updates or deletes a row, and
--   the newest row per shelter wins (15_latest_assessments.sql). NULL BOOL = unclear / not asked.
INSERT INTO `{{dataset}}.shelter_assessments`
  (assessment_id, shelter_id, shelter_name, county, operator_notes, wheelchair_unaccompanied_entries,
   ramp_unlocked_24_7, generator_powers_medical_outlets, verified_status, assessed_at, assessor_role,
   session_id, source)
VALUES
  (@assessment_id, @shelter_id, @shelter_name, @county, @operator_notes, @wheelchair,
   @ramp, @generator, @verified_status, CURRENT_TIMESTAMP(), @assessor_role,
   @session_id, @source)
