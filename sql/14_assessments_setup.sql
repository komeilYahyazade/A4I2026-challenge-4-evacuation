-- 14_assessments_setup.sql
-- Purpose: field assessments, the record nobody wrote down, collected by asking the operator.
--   The table already exists in the shared project (a teammate created it with the first six
--   columns). This adds what the assessment agent needs and is safe to re-run.
--   A BOOL left NULL means the answer was unclear or not asked: unrecorded, never "no".
CREATE TABLE IF NOT EXISTS `{{dataset}}.shelter_assessments` (
  shelter_id STRING,
  shelter_name STRING,
  county STRING,
  operator_notes STRING,
  wheelchair_unaccompanied_entries BOOL,
  ramp_unlocked_24_7 BOOL,
  generator_powers_medical_outlets BOOL,
  verified_status STRING,
  assessed_at TIMESTAMP
);
ALTER TABLE `{{dataset}}.shelter_assessments` ADD COLUMN IF NOT EXISTS assessment_id STRING;
ALTER TABLE `{{dataset}}.shelter_assessments` ADD COLUMN IF NOT EXISTS assessor_role STRING;
ALTER TABLE `{{dataset}}.shelter_assessments` ADD COLUMN IF NOT EXISTS session_id STRING;
ALTER TABLE `{{dataset}}.shelter_assessments` ADD COLUMN IF NOT EXISTS source STRING;
