# Lee County findings (FL, FIPS 12071)

These come from queries against `evacuation_readiness` and `readiness_ops` in the team project, 2026-10-20.
🌍 = measures the world. 📄 = measures the paperwork (what somebody recorded).

## Headline

Lee's 14,553 households without a car hold about **36,800 people** (🌍, an estimate: households ×
persons per household, with group quarters excluded). Lee's pre-landfall shelters have **34,000 recorded spaces** (📄).

## Lee pre-landfall shelters (EVAC or BOTH): 84 📄

| Field | YES | NO | Nobody recorded it |
|---|---:|---:|---:|
| Wheelchair accessible | 2 | 5 | **77 (92%)** |
| Generator on site | 6 | 0 | 78 |
| In surge (SLOSH) area | 2 | 1 | **81** |
| Capacity recorded | 72 | – | 12 (NULL or 0) |

- 9 medical-needs shelters, and none has a generator recorded 📄.
- **"INACTIVE Grace Church" (shelter 345200) is still designated EVAC with 100 spaces** 📄. The live
  Google Maps check returns DROP (NOT_FOUND, NAME_SAYS_INACTIVE).
- Fort Myers Beach churches are designated EVAC shelters in tracts where 100% of residents are
  exposed to coastal flooding (🌍 FEMA NRI exposure; the shelter-to-tract match is APPROXIMATE).
- Statewide, 148 shelters have INACTIVE, CLOSED, DEMOLISHED or DO NOT USE in FEMA's own name, and 24 of them are still pre-landfall.

## Distance

- Median Lee tract: **2.0 km** to the nearest pre-landfall shelter, but **11.2 km** to one *recorded*
  wheelchair accessible. That is 5.5× farther. (Straight-line distance, a lower bound on road distance.)
- 142 of 221 tracts, holding 6,401 no-car households, have no shelter within walking distance (1.5 km).
- 59 tracts have no recorded-accessible pre-landfall shelter within a 15 km para-transit run.

## People who depend on power, and nursing homes

- HHS emPOWER: **7,381** Medicare beneficiaries in Lee depend on electricity for medical equipment
  (out of 224,109 beneficiaries; not censored). This is COUNTY-LEVEL: it says how many, never which tract.
- 20 nursing homes with 2,177 certified beds. 2 of them (205 beds) are more than 15 km from a pre-landfall shelter
  with a generator recorded.

## Hurricane Ian replay (NOAA NHC GIS archive, public domain) 🌍

- Across advisories 9 → 11 → 13 → 17 → 19, the number of Lee shelters inside the official cone went **5 → 4 → 18 → 18 → 74**.
- Fort Myers entered the cone only about 22 h before landfall. Tampa was inside every cone. **That is the
  case for planning in August, not during the storm.**
- Rehearsal (agent lane's run, no Maps): advisory 19 takes Skyline Elementary and Estero High
  offline, losing 2,695 seats. People without a placement rise from 5,589 to 7,284.
- The two shelters FEMA records as in the surge zone have no recorded capacity, so the plan never counted on
  them.

## Validation

- **PASS:** joins, county placement (all 2,793 shelters in-state), capacity range (FL max 5,750,
  so no implausible capacities).
- **WARN:**
  - `pct_lacktrpt` is empty for every FL tract (CDC PLACES does not publish it for FL).
  - Wheelchair access is unrecorded for 88% of shelters statewide.
  - 332 shelters have no usage code.
  - 297 pre-landfall shelters have no recorded capacity.
  - 148 shelters have INACTIVE-style names.
  - There are 25 duplicate name+address groups.

## Notes for the deck

- **The 36,800 is an upper-leaning estimate.** It multiplies Lee's no-car households by the tract's *average*
  household size. Households without a car skew smaller (older people living alone), so the true number is
  probably lower. Quote it as "about", give the direction, and lead with the 14,553 households, which is counted.
- **Who is not in that number.** Tracts with residents but no households (only group quarters: dorms, prisons,
  nursing homes) get no estimate. Nursing homes are covered separately (20 homes, 2,177 beds); the rest are not.
- **Some shelter rows are the same building filed twice** (25 name+address groups statewide). They are flagged
  (`is_duplicate_record`) and still counted, so check `pre_landfall_duplicate_records` before quoting Lee's
  34,000 spaces.

- `pct_disability` is CDC SVI (an ACS estimate), so it is RECORDED. CDC PLACES measures (`pct_mobility`, etc.)
  are MODELED.
- "Not in the surge zone" is never true just because the flag is blank: 81 of 84 are blank.
