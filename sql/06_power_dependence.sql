-- 06_power_dependence.sql
-- Purpose: HHS emPOWER power-dependent Medicare beneficiaries for one county (county total only:
--   how many, never where), plus the county's medical-needs shelter count and recorded capacity.
--   An emPOWER value of exactly 11 means 1 to 10 (censored).
-- Parameters: @county_fips STRING
-- Output: county_fips, power_dependent_dme, is_censored, medical_shelters, medical_capacity
-- Sample row: "12071", 5480, FALSE, 9, 2100
SELECT @county_fips AS county_fips,
       (SELECT CAST(power_dependent_dme AS INT64) FROM `{{dataset}}.power_dependent_counties`
        WHERE fips_code = @county_fips) AS power_dependent_dme,
       IFNULL((SELECT power_dependent_is_censored FROM `{{dataset}}.power_dependent_counties`
               WHERE fips_code = @county_fips), FALSE) AS is_censored,
       (SELECT COUNTIF(is_medical) FROM `{{dataset}}.shelter_evidence`
        WHERE county_fips = @county_fips) AS medical_shelters,
       (SELECT SUM(IF(is_medical, evacuation_capacity, 0)) FROM `{{dataset}}.shelter_evidence`
        WHERE county_fips = @county_fips) AS medical_capacity,
       (SELECT COUNTIF(is_medical AND evacuation_capacity IS NULL) FROM `{{dataset}}.shelter_evidence`
        WHERE county_fips = @county_fips) AS medical_capacity_unrecorded
