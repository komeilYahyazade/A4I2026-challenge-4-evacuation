-- 10_merge_place.sql
-- Purpose: persist the ONLY Maps-derived value we may keep: place_id, keyed by shelter_id.
--   Never cache, store or export any other Maps content; re-ground at runtime.
-- Parameters: @shelter_id STRING, @place_id STRING
-- Output: none (DML)
MERGE `{{dataset}}.shelter_places` t
USING (SELECT @shelter_id AS shelter_id, @place_id AS place_id) s
ON t.shelter_id = s.shelter_id
WHEN MATCHED AND t.place_id != s.place_id THEN UPDATE SET place_id = s.place_id
WHEN NOT MATCHED THEN INSERT (shelter_id, place_id, first_seen) VALUES (s.shelter_id, s.place_id, CURRENT_TIMESTAMP())
