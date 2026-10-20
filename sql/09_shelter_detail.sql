-- 09_shelter_detail.sql
-- Purpose: one shelter's full record (detail panel, and the input to verify_shelter).
-- Parameters: @shelter_id STRING
-- Output: every shelter_evidence column + place_id
-- Sample row: "70851", "Harborside Events Center Special Needs", "1375 Monroe St., Fort Myers, FL 33901", ...
SELECT s.*, p.place_id
FROM `{{dataset}}.shelter_evidence` s
LEFT JOIN `{{dataset}}.shelter_places` p USING (shelter_id)
WHERE s.shelter_id = @shelter_id
