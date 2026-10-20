# Data contract (owner: data lane)

BigQuery project `qwiklabs-gcp-03-7a0c015d2e8a`, location **US**. Two datasets:

| Dataset | Who writes | What |
|---|---|---|
| `evacuation_readiness` | `scripts/load.sh FL` only | The 5 federal source tables. **Read-only** to everything else |
| `readiness_ops` | data lane (views), agent (ops-table rows) | Views over the source tables, the Ian replay, and operational tables |

Rebuild: `bash scripts/build_views.sh`, which runs `sql/*.sql` in order. Views use CREATE OR REPLACE, and
ops tables use CREATE IF NOT EXISTS, so they are never truncated. The Ian replay is loaded with `bash sql/ian/load_ian.sh`.

**There is one source of thresholds.** `build_views.sh` reads `WALK_KM`, `PARATRANSIT_PROXY_KM`, `CAR_KM`,
`CAPACITY_PLAUSIBLE_MAX` and `PRE_LANDFALL_USAGE` from `agent/reachability.py` at build time. Change them
there and re-run the script. The views never define their own.

Three rules hold in every view:
- Flags are `'YES' | 'NO' | 'UNRECORDED'`. NULL or blank becomes UNRECORDED, never NO.
- Nothing is a person: the smallest unit is a census tract.
- Race appears only in the equity audit output (`data/EQUITY_AUDIT.md`), never as an input.

---

## Source tables (`evacuation_readiness`, FL)

Row counts checked after load: shelters **2,793**, vulnerability_tracts **5,160**, hazard_tracts **5,114**,
care_facilities **694**, power_dependent_counties **67**.

### `shelters`: FEMA NSS, one row per registered facility (RECORDED, historical)
| Column | Type | Notes |
|---|---|---|
| shelter_id | INT64 | Unique (2,793 distinct). CAST to STRING on the wire |
| shelter_name, address_1, city, zip | STRING | 146 names say "INACTIVE …" (see `name_says_inactive`) |
| county_parish, state | STRING | FEMA text; use `county_fips` instead |
| county_fips, county_name | STRING | **Derived** by the notebook from coordinates (point-in-polygon). Present for all 2,793 rows, all in FL |
| latitude, longitude | FLOAT64 | No nulls, all in range |
| evacuation_capacity, post_impact_capacity | FLOAT64 | NULL for 529 rows, 0 for 731. POST shelters legitimately have evac = 0 (620 of 622 carry a post-impact capacity). On EVAC/BOTH, NULL or 0 means **unrecorded** (188 NULL + 109 zero). Max 5,750: FL has no capacity above 100k |
| wheelchair_accessible, generator_onsite, self_sufficient_electricity, in_surge_slosh_area, in_100_yr_floodplain | BOOL | Parsed from FEMA's YES/NO strings, with blank or UNK turned into NULL. Wheelchair: 247 TRUE / 83 FALSE / **2,463 NULL (88%)**. Generator: 2,318 NULL. Surge: 2,269 NULL. Floodplain: 2,323 NULL |
| usage | STRING | `EVAC` 1,391 · `POST` 698 · `BOTH` 372 · **NULL 332**. Pre-landfall means EVAC or BOTH |
| population_code, is_medical, is_functional | STRING/BOOL | is_medical is never NULL (155 TRUE) |
| pet_accommodations_code, shelter_status_code | STRING | shelter_status_code is **not** operational status. FEMA says not to use this file for that |
| incident_id | FLOAT64 | 1,751 rows tagged to a past incident |

`ada_compliant` is deliberately absent; it's the decoy field. The `org_poc_*` personal-contact fields are excluded too.

### `vulnerability_tracts`: one row per 2020 tract
| Column | Type | Source and label |
|---|---|---|
| geo_id | STRING(11) | Always 11 characters. The first 5 are county_fips |
| total_pop, households, households_no_vehicle, group_quarters_pop, mobile_homes, pop_65_plus, limited_english_spanish, median_income, poverty_rate | FLOAT64 | Census ACS, newest tract table: **RECORDED** (survey estimate). `households_no_vehicle` measures vehicle *ownership*, not access to a ride |
| county_fips | STRING(5) | |
| lat, lon | FLOAT64 | NRI 2020 centroid, falling back to the 2010 internal point. None missing |
| pct_disability, pct_age_65_plus, pct_no_vehicle, pct_limited_english, pct_mobile_homes, pct_group_quarters, pct_no_internet, svi_* | FLOAT64 | CDC SVI 2022 (ACS-derived): **RECORDED**, not modeled. The -999 sentinel is already nulled, and FL has no negatives. pct_disability is NULL for 82 tracts. SVI Theme 3 and RPL_THEMES are excluded |
| pct_mobility, pct_selfcare, pct_indeplive, pct_lacktrpt | FLOAT64 | CDC PLACES: **MODELED** prevalence. **pct_lacktrpt is NULL for all 5,160 FL tracts** because PLACES does not publish LACKTRPT for FL |

81 tracts have total_pop = 0 and 90 have households = 0. Every view excludes total_pop = 0.

### `hazard_tracts`: FEMA NRI, per tract (RECORDED; "planning purposes only")
geo_id STRING, POPULATION, BUILDVALUE, HRCN_AFREQ, HRCN_EXPP, HRCN_EALT, HRCN_EALP, CFLD_AFREQ, CFLD_EXPP,
CFLD_EALT and CFLD_EALP, all FLOAT64. CFLD_* is NULL for 330 tracts (no coastal-flood exposure computed), and HRCN_EXPP is never NULL.
No RISK_SCORE or *_RISKS composites are loaded, because using them on top of vulnerability would double-count.

### `care_facilities`: CMS nursing homes (RECORDED)
cms_certification_number_ccn STRING, provider_name, provider_address, citytown, state and zip_code STRING,
number_of_certified_beds INT64, ownership_type STRING, latitude and longitude FLOAT64 (none missing). The table has **no county column**,
so place facilities with ST_CONTAINS against `bigquery-public-data.geo_us_boundaries.counties`.

### `power_dependent_counties`: HHS emPOWER (COUNTY-LEVEL; purpose: emergency preparedness only)
Key columns are fips_code STRING(5), county STRING, medicare_benes, power_dependent_dme (identical to
power_dependent_devices_dme in all 67 rows), o2_services_any_dme, facility_esrd_dialysis_any_dme and power_dependent_is_censored BOOL.
No FL county is censored. **Lee (12071): 7,381 power-dependent beneficiaries out of 224,109 Medicare beneficiaries.** The
data gives a count only, never a location, and no attempt is made to identify anyone.

---

## Views (`readiness_ops`)

### `v_shelter_evidence`: one row per shelter, the flags made honest
This view has the same columns and meaning as `agent/queries.py SHELTER_COLUMNS`, plus the columns that keep blanks visible.

| Column | Type | Meaning |
|---|---|---|
| shelter_id | STRING | |
| shelter_name, address_1, city, zip, county_fips, county_name | STRING | |
| latitude, longitude | FLOAT64 | |
| usage | STRING | TRIMmed, with blank turned into NULL |
| usage_status | STRING | `EVAC` / `POST` / `BOTH` / `UNRECORDED` |
| is_pre_landfall | BOOL | usage is in PRE_LANDFALL_USAGE. FALSE when usage is unrecorded |
| pre_landfall_status | STRING | YES / NO / UNRECORDED |
| evacuation_capacity, post_impact_capacity | FLOAT64 | The raw FEMA values |
| capacity | INT64 | The usable evacuation capacity. **NULL** when FEMA's value is NULL, or 0 on a shelter that is pre-landfall or has unrecorded usage |
| capacity_status | STRING | `RECORDED` / `UNRECORDED` |
| capacity_implausible | BOOL | evacuation_capacity > CAPACITY_PLAUSIBLE_MAX. Never TRUE in FL |
| wheelchair_status, generator_status, surge_status, floodplain_status | STRING | YES / NO / UNRECORDED |
| is_medical | BOOL | |
| pet_accommodations_code | STRING | |
| name_says_inactive | BOOL | The FEMA name contains INACTIVE, CLOSED, DEMOLISHED or DO NOT USE. **148 FL rows, 24 still EVAC/BOTH** (6,876 spaces). This is the same expression as `queries.py`. The row is flagged, never dropped; Google Maps is what checks it |

### `v_tract_need`: one row per populated tract, statewide (filter on `county_fips`)
| Column | Type | Meaning |
|---|---|---|
| geo_id, county_fips | STRING | |
| lat, lon | FLOAT64 | Tract centroid |
| total_pop, households, households_no_vehicle, group_quarters_pop | FLOAT64 | ACS, RECORDED |
| share_no_vehicle | FLOAT64 | households_no_vehicle / households |
| persons_per_household | FLOAT64 | (total_pop − group_quarters_pop) / households. NULL when households = 0 |
| est_persons_no_vehicle | FLOAT64 | households_no_vehicle × persons_per_household. **This is the allocator's demand.** It is an estimate, labelled as such |
| pop_65_plus, mobile_homes | FLOAT64 | ACS |
| pct_age_65_plus, pct_disability, pct_mobile_homes, pct_no_vehicle_svi | FLOAT64 | CDC SVI, RECORDED. Negative sentinels become NULL |
| pct_mobility | FLOAT64 | CDC PLACES, **MODELED** |
| hurricane_exposed_pop, coastal_flood_exposed_pop | FLOAT64 | NRI HRCN_EXPP / CFLD_EXPP |
| n_usage_unrecorded_within_run | INT64 | Shelters with unrecorded usage within PARATRANSIT_PROXY_KM. They exist, but nobody designated them |

There is no priority score here on purpose. Ranking lives only in `queries.RANK_TRACTS`.

### `v_shelter_site_hazard`: the ground each shelter stands on (**APPROXIMATE**)
Each shelter is matched to its nearest 2020 tract centroid by ST_DISTANCE, within 25 km. All 2,793 match, at most 16.4 km away.
Display label: **"APPROXIMATE: FEMA NRI coastal-flood exposure of the nearest 2020 tract centroid. It describes the
neighbourhood, not the building's elevation."** This is the stand-in for the FEMA surge flag, which is blank for
81 of Lee's 84 pre-landfall shelters.

| Column | Type | Meaning |
|---|---|---|
| shelter_id, nearest_geo_id | STRING | |
| km_to_centroid | FLOAT64 | |
| tract_population | FLOAT64 | NRI POPULATION |
| hurricane_exposed_pop, hurricane_annual_frequency | FLOAT64 | HRCN_EXPP, HRCN_AFREQ |
| coastal_flood_exposed_pop, coastal_flood_annual_frequency | FLOAT64 | CFLD_EXPP, CFLD_AFREQ |
| coastal_flood_exposed_share | FLOAT64 | CFLD_EXPP / POPULATION, from 0 to 1. **Use this one** |
| site_coastal_flood_status | STRING | YES (CFLD_EXPP > 0) / NO / UNRECORDED. Too coarse to act on: it is YES for 71 of Lee's 84 |
| method | STRING | `nearest 2020 tract centroid by ST_DISTANCE (approximate)` |

### `v_tract_shelter_candidates`: the allocator's edge list
Each row links a populated tract to one of its **15 nearest pre-landfall (EVAC/BOTH)** shelters within CAR_KM, in **any county**.
Filter with `WHERE county_fips = @county_fips`, which is the tract's county. Distance is **STRAIGHT-LINE**, a lower bound
on road distance. A Lee query takes about 3 s.

| Column | Type | Meaning |
|---|---|---|
| geo_id, county_fips | STRING | Tract and its county |
| shelter_id, shelter_name, shelter_county_fips | STRING | |
| shelter_lat, shelter_lng | FLOAT64 | |
| straight_line_km | FLOAT64 | From the tract centroid |
| rank_k | INT64 | 1 = nearest. Ties are broken by shelter_id |
| usage | STRING | EVAC / BOTH |
| is_pre_landfall | BOOL | Always TRUE in this view |
| evacuation_capacity | INT64 | Usable capacity. NULL when unrecorded, so exclude it from the solve and report it |
| capacity_status | STRING | RECORDED / UNRECORDED |
| capacity_implausible | BOOL | |
| wheelchair_status, generator_status, surge_status | STRING | YES / NO / UNRECORDED |
| is_medical, name_says_inactive | BOOL | |
| within_walk | BOOL | straight_line_km ≤ WALK_KM |
| within_paratransit_proxy | BOOL | straight_line_km ≤ PARATRANSIT_PROXY_KM |
| site_coastal_flood_status, site_coastal_flood_exposed_share, site_coastal_flood_exposed_pop, site_hurricane_exposed_pop | | From `v_shelter_site_hazard`, APPROXIMATE |

Lee: 3,315 edges, 221 tracts, 112 shelters (28 outside Lee), 432 edges to shelters with unrecorded capacity.

### `v_county_shelter_summary`: registry record quality per county
shelters, pre_landfall, post_only, usage_unrecorded, pre_landfall_capacity_recorded/unrecorded,
pre_landfall_capacity_plausible (recorded and plausible capacity only), capacity_implausible, and
pre_landfall_{wheelchair,generator,surge}_{yes,no,unrecorded}, plus pre_landfall_site_coastal_flood_exposed, medical_needs and
medical_needs_generator_yes. All are INT64. **These count paperwork, not buildings. Never rank counties on them.**

### Ian replay (rehearsal only, never with Google Maps)
**`storm_advisories`**: one row per NHC advisory for AL092022: advisories 9, 11, 13, 17 and 19, which are 82.1, 70.1, 58.1, 34.1 and
22.1 h before the 2022-09-28 19:05Z landfall.
| Column | Type | Meaning |
|---|---|---|
| storm_id, storm_name, advisory, storm_type | STRING | advisory is '9', '11', … |
| issued_at | TIMESTAMP | UTC |
| cone | GEOGRAPHY | The 5-day cone |
| cone_geojson | STRING | A GeoJSON **Geometry** (Polygon) in [lng, lat], 27–35 KB |
| track_json | STRING | An array of {lat, lng, valid_at, tau_h, wind_kt, mslp_mb, category}. This is `storm_track.v1.track` |
| track_geojson | STRING | A FeatureCollection of Points with those properties |
| hours_before_landfall | FLOAT64 | |
| source | STRING | NOAA NHC GIS archive (public domain) |

**`storm_best_track`**: what actually happened. 36 points: storm_id, valid_at TIMESTAMP, lat, lng, wind_kt, mslp_mb,
storm_type, source.

**`v_ian_shelters_down`**: one row per (advisory, shelter) for pre-landfall shelters in Lee and the counties touching
it (Charlotte 12015, Collier 12021, Glades 12043, Hendry 12051). advisory includes the cone-agnostic `'ANY'`.
| Column | Type | Meaning |
|---|---|---|
| storm_id, advisory | STRING | |
| issued_at | TIMESTAMP | NULL for ANY |
| hours_before_landfall | FLOAT64 | |
| shelter_id, shelter_name, county_fips | STRING | |
| lat, lng | FLOAT64 | |
| capacity, evacuation_capacity | INT64 | evacuation_capacity is an alias of capacity |
| capacity_status, surge_status | STRING | |
| site_coastal_flood_exposed_share, site_coastal_flood_annual_frequency | FLOAT64 | |
| in_cone | BOOL | NULL for ANY |
| reason | STRING | `IN_SURGE_ZONE` (FEMA flag, RECORDED) · `IN_CONE_AND_SURGE` (in the cone, and ≥ 50% of the tract coastal-flood-exposed; APPROXIMATE) · `COASTAL_FLOOD_TRACT` (the same test with no cone, for advisory ANY) |
| evidence | STRING | The label text |
| down_rank | INT64 | Within the advisory: surge flag first, then flood frequency, exposed share, capacity, id |
| down_rank_with_capacity | INT64 | The same order, counting only RECORDED capacity |

The 50% cut means "most of the neighbourhood floods". It is the data lane's hazard decision, not a reachability threshold.
Rows per advisory, with spaces lost: 9 → 7 (1,550) · 11 → 7 (1,550) · 13 → 19 (7,545) · 17 → 19 (7,545) · **19 → 46 (14,128)** · ANY → 105 (38,053).

---

## Operational tables (`readiness_ops`, the agent writes, nothing truncates)
No row may describe a resident. Actor columns hold a staff role or service account.

| Table | Columns | Rule |
|---|---|---|
| `shelter_place_ids` | shelter_id STRING, place_id STRING, verified_at TIMESTAMP, verdict STRING (KEEP / DROP / KEEP_UNVERIFIED) | **place_id is the only thing kept from Google Maps.** Re-ground at runtime |
| `plans` | plan_id STRING, county_fips STRING, version INT64, mode STRING (PLANNING / REHEARSAL), created_at TIMESTAMP, created_by STRING, approved_by STRING, approved_at TIMESTAMP, status STRING (DRAFT / APPROVED / SUPERSEDED), plan_json JSON | Before writing, strip Maps sources, supports, live names and Maps text from plan_json. A person approves before publication |
| `status_reports` | report_id, shelter_id STRING, reported_at TIMESTAMP, status STRING (OPEN / FULL / CLOSED / FLOODED), occupancy INT64, source STRING | First-party reports only. This is the input to re-solves |
| `decision_log` | ts TIMESTAMP, actor STRING, action STRING, detail JSON | Append-only |
| `assessments` | assessment_id, shelter_id STRING, assessed_at TIMESTAMP, assessor_role STRING, wheelchair_unaccompanied STRING (YES / NO / UNRECORDED), notes STRING | Answers "can a wheelchair user get in alone, today?" |

`_stage_storm_advisories` is a load staging table. Ignore it.
