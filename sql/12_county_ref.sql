-- 12_county_ref.sql
-- Purpose: one county's name and its shelters' recorded-flag counts (three-state), for the
--   brief header and the "what we could not tell you" counts. Counts measure paperwork.
-- Parameters: @county_fips STRING
-- Output: county_fips, name, shelters_total, wheelchair_yes/no/unrecorded, generator_yes/no/unrecorded,
--   medical_needs, in_surge_zone, evac_total
-- Sample row: "12071", "Lee County", 100, 2, 5, 93, ...
SELECT @county_fips AS county_fips,
       (SELECT lsad_name FROM `bigquery-public-data.geo_us_boundaries.counties` WHERE geo_id = @county_fips) AS name,
       COUNT(*) AS shelters_total,
       COUNTIF(wheelchair_accessible = 'yes') AS wheelchair_yes,
       COUNTIF(wheelchair_accessible = 'no') AS wheelchair_no,
       COUNTIF(wheelchair_accessible = 'unrecorded') AS wheelchair_unrecorded,
       COUNTIF(generator_onsite = 'yes') AS generator_yes,
       COUNTIF(generator_onsite = 'no') AS generator_no,
       COUNTIF(generator_onsite = 'unrecorded') AS generator_unrecorded,
       COUNTIF(is_medical) AS medical_needs,
       COUNTIF(in_surge_slosh_area = 'yes') AS in_surge_zone,
       COUNTIF(usage = 'EVAC') AS evac_total,
       (SELECT COUNTIF(pct_lacktrpt IS NOT NULL) FROM `{{dataset}}.tract_scores`
        WHERE county_fips = @county_fips) AS lacktrpt_published_tracts
FROM `{{dataset}}.shelter_evidence`
WHERE county_fips = @county_fips
