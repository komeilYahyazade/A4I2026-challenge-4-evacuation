# BUILD SPEC: Evacuation Readiness Agent (Agents for Impact 2026, Challenge 4)

> **For the LLM reading this.** This file is the single source of truth for building the
> system end to end. Follow it in order (section 18 is the build order). Where this file gives an exact
> import, class, field or command, use it as written: each one was checked against
> `google-adk==2.12.0` and `google-genai==2.31.0` on 2026-10-20. Do not substitute APIs from memory.
> Where a value is marked *illustrative*, it is an example shape, not real data. Never invent
> numbers in code, prompts, mocks labelled live, or UI copy: every number shown to a user must come
> from a SQL result or a tool result.

---

## 0. Contents

1. Mission, user and scenario
2. Hard requirements (judged) and scoring
3. Non-negotiable rules (data honesty and compliance)
4. Architecture
5. The season journey and scope tiers
6. Data: tables, real columns, known defects
7. SQL files
8. Agents
9. Tools
10. API contract (v1)
11. Google Maps attribution
12. Front end (Streamlit)
13. Repository layout and configuration
14. Deployment
15. Verified API reference (ADK 2.12 / google-genai 2.31)
16. Testing, evaluation and acceptance
17. Question coverage map
18. Build order, gates and cut list
19. Phase 2 extensions (additive contract)
20. Path to production
21. Known limitations to state in the demo
22. Open decisions and defaults

---

## 1. Mission, user and scenario

**Scenario sentence (opens the demo):**
*"Our agent helps the Lee County (Florida) emergency manager decide which shelters can really take
the people who cannot drive themselves out, and what to fix, before the September peak of
hurricane season."*

- **User:** a county emergency manager writing the August readiness brief (internal user, not the public).
- **When:** mid-August, planning weeks ahead of a storm that has not formed. This is a **planning
  tool, never an emergency-response tool**.
- **State:** Florida (`STATE = "FL"`). Default focus county: **Lee County, FIPS `12071`** (Hurricane
  Ian's landfall in 2022; the median age of Ian's dead was in the seventies).
- **Track:** Shelter readiness, with no-car tracts as the "who". Every track uses the same API.
- **Core idea:** *Our data is the memory. Google Maps is the eyes. Neither knows whether a place works
  for a person who cannot drive.* FEMA's shelter file is a historical registry; Maps grounding
  checks whether each building still exists and what it is now; our tools supply every number; the
  model supplies judgments.

The **Readiness Brief** is the output artifact, with four segments:

1. **Who cannot leave:** tracts and facilities holding people who cannot self-evacuate, ranked, each with a reason and numbers.
2. **Where they would go, and whether it holds up:** candidate shelters with capacity, recorded accessibility/generator/surge flags, and **live Maps verification** (exists, renamed/closed, what it is now, drive time).
3. **What to fix before the season:** the gaps, each tied to the tracts/shelters/facilities it affects.
4. **What we could not tell you:** named and counted unknowns. This is a finding, not a disclaimer.

---

## 2. Hard requirements (judged) and scoring

Every item is mandatory:

- [ ] **ADK** agent in Python.
- [ ] **Gemini 3.x** model: `gemini-3.6-flash` (lets `google_maps_grounding` and function tools share one agent).
- [ ] **BigQuery** holds all data; the agent queries it.
- [ ] **At least one Google-managed MCP server consumed**: the BigQuery remote MCP server (`https://bigquery.googleapis.com/mcp`). Do not author your own MCP server.
- [ ] **At least one custom tool** that holds real logic (we have six).
- [ ] **Grounding with Google Maps genuinely used and acted on.** A Maps result must change the output: drop a closed shelter, flag a rename, swap in the next candidate, or change a verdict. Decoration does not count.
- [ ] **Deployed to Google Cloud** (Cloud Run).
- [ ] Maps **attribution** rendered correctly (section 11).
- [ ] Submission: GitHub repo link, Google Skills project link, pitch deck, pitch video (screen-share narrated demo). Judges score these after the event; there is no Q&A, so every judging question must be answered in the deck or the video.

**Scoring:** Impact & insight 30 · Technical execution 30 · Rigor & judgment 25 · Craft & communication 15 · Bonus range +10.
Rigor means: defensible data decisions (what "reachable" means and why), validation (read the notebook's WARN rows), bias handling (the equity audit, with numbers), and knowing what the system cannot do.

---

## 3. Non-negotiable rules (data honesty and compliance)

These go into every system instruction, every tool and the UI.

**Honesty**
1. **Unrecorded is not no.** Shelter flags are three-state: `yes`, `no`, `unrecorded`. Never collapse unrecorded into no anywhere in the pipeline. Say *"11 of these I can speak to; 64 nobody has assessed"*, never *"no accessible shelters"*.
2. **Label freshness.** Every claim is `historical` (FEMA or other federal record) or `live` (a Maps lookup, with `checked_at`).
3. **Power dependence is county-level** (HHS emPOWER): say how many, never which block. An emPOWER value of exactly 11 means 1 to 10 (censored).
4. **Name the transport measure.** `households_no_vehicle` (ACS) and `pct_no_vehicle` (CDC SVI) measure car **ownership**, not having a ride. `pct_lacktrpt` (CDC PLACES, lack of reliable transport) is **empty for FL and TX**.
5. **Modeled is not counted.** CDC PLACES values (mobility, self-care, independent living, transport) are modeled prevalence.
6. **Distances** are `straight_line_km` unless `drive_minutes` came back from Maps routing. Always say which.
7. **Paperwork vs world.** Counts of recorded flags measure what was filled in, not what exists. Never rank counties or states on recorded counts.

**Compliance**
8. **Planning only.** Google's terms forbid Grounding with Google Maps for *"high risk activities including emergency response services"*. The agent refuses dispatch or live-emergency requests ("is this shelter open right now, during the storm?") and points to the county emergency operations centre or 911. Set `refused: true`.
9. **"Open now" on Maps means business hours**, not that the county has opened the building as a shelter.
10. **Store only `place_id` (and `review_id`) from Maps.** Never cache, store or export any other Maps content. Re-ground at runtime.
11. **No individual-level data.** Census tract and facility only. Never use or display `org_poc_*` or any named-person column.
12. **Race is never an input.** It is used only in the equity audit of the output.
13. **emPOWER purpose condition:** emergency preparedness only; no attempt to identify individuals.
14. Do not use FEMA NRI `RISK_SCORE` or any `*_RISKS` composite (it double-counts social vulnerability).
15. Do not quietly overwrite federal records (for example impossible capacities); flag them.

---

## 4. Architecture

Two Cloud Run services and one ADK app. A Streamlit UI calls `readiness-api`, a FastAPI service
that serves map data straight from BigQuery and runs the agents for briefs and chat.
**Numbers come from tools, judgments come from the model, and the API joins them by id. No count
ever passes through the LLM.**

```
                 readiness-ui  (Streamlit, Cloud Run)    map · brief · chat
                           │  JSON over HTTPS: the /v1 contract
 ┌─────────────────────────▼───────────────────────────────────────────────────────┐
 │ readiness-api  (FastAPI + ADK, Cloud Run)                                        │
 │                                                                                  │
 │  Data routes ───────┐   brief_pipeline (SequentialAgent)     readiness_agent     │
 │  /counties /map     │   brief_analyst → brief_writer         (chat; Maps + MCP)  │
 │  /shelters          │   API joins facts + judgments                 │            │
 │  SQL from sql/      │              │                                 │            │
 │                     │   Custom tools (Python), each writes facts to session state │
 │                     │   rank_vulnerable_tracts · find_candidate_shelters          │
 │                     │   list_care_facilities · get_power_dependence               │
 │                     │   find_readiness_gaps · verify_shelter (one building/call)  │
 └─────────┬───────────┴──────────────┬──────────────────────────────┬─────────────┘
           │ SQL                      │ verify_shelter               │ chat only
   BigQuery (US)            Gemini 3.x on Vertex AI          BigQuery MCP server
   evacuation_readiness     Grounding with Google Maps       Google-managed, read-only SQL
   + shelter_places          (+ routing for drive times)
     (place_id only)
```

- Only `readiness-api` talks to Google services; the UI never needs BigQuery or Gemini credentials.
- The same service mounts ADK's own routes (`/dev-ui`, `/run`, `/run_sse`), so the ADK web UI on the deployed agent is the demo backup.
- Model location `global`; BigQuery in `US`; Cloud Run in `us-central1`.

---

## 5. The season journey and scope tiers

The product is designed for the whole year. The hackathon builds tier 1; tiers 2 and 3 are additive.

| Phase | When | What happens | Components | Maps grounding? |
|---|---|---|---|---|
| Inventory | Oct–Mar, ongoing | Refresh who cannot leave when ACS/SVI/PLACES update; verify every shelter; field assessments fill the unrecorded gap | Detective (rank tools), Shelter Verifier, Assessment agent | Yes |
| Plan and invest | Apr–Jul | Match vulnerable tracts to verified shelters within capacity; choose budgeted fixes; sign transport and transfer agreements | Resource Allocator (OR-Tools) | Yes (drive times, nearby places) |
| Readiness brief | August | The four-segment brief, plus rehearsal drills | brief_pipeline, rehearsal | Yes |
| Watch | Jun–Nov, every 6 h | Poll NHC/NWS; phase and countdown; select the pre-agreed playbook | Storm Watch | **No** |
| Activate | ~5 days out to landfall | Re-solve assignments as shelters open, fill or flood, from operator-reported counts; human approval | Evacuation Optimizer | **Never** |
| Learn | After the storm | After-action data (which shelters really opened, real capacities) feeds next year | Coordinator | No |

**Scope tiers**
- **Tier 1 (hackathon MVP, sections 6–18):** the v1 contract: map data, `verify_shelter`, the brief pipeline, chat, the equity audit, deployment.
- **Tier 2 (stretch, section 19):** assessment agent; Resource Allocator (match + invest) with OR-Tools; Ian rehearsal (re-solve when shelters drop out); Storm Watch on archived Ian advisories.
- **Tier 3 (production, section 20):** scheduling, live NHC polling, the operational re-solve without Maps, governance.

Corrections built into the design: the "hurricane predictor" consumes official forecasts (NHC 7-day outlook; NOAA CPC weeks 2–3, low confidence) and never forecasts itself. The "detective" finds **concentrations**, never people. The allocator designates or retrofits **evacuation** shelters and pickup hubs (homeless shelters are a different category; that is the notebook's section-2 trap).

---

## 6. Data: tables, real columns, known defects

Dataset: `<project>.evacuation_readiness` (location `US`), loaded by the challenge notebook
`notebooks/c4_01_load_explore.ipynb` (Colab Enterprise, set `STATE="FL"`) or the fallback
`bash scripts/load.sh FL`. Always confirm columns with `INFORMATION_SCHEMA.COLUMNS` before relying on them.

### 6.1 `shelters` (FEMA National Shelter System), FL ≈ 2,793 rows
Columns as loaded by the notebook:
`shelter_id, shelter_name, address_1, city, county_parish, state, zip, latitude, longitude,
evacuation_capacity, post_impact_capacity, wheelchair_accessible, generator_onsite,
self_sufficient_electricity, in_surge_slosh_area, in_100_yr_floodplain, pet_accommodations_code,
population_code, is_medical, is_functional, usage, shelter_status_code, incident_id,
county_fips, county_name`

- Flags (`wheelchair_accessible, generator_onsite, self_sufficient_electricity, in_surge_slosh_area, in_100_yr_floodplain`) are **BOOL with NULL for unrecorded**.
- `county_fips` comes from `ST_CONTAINS` against `bigquery-public-data.geo_us_boundaries.counties`.
- `usage` is FEMA's facility usage code (`EVAC` = before landfall, `POST` = after impact) or NULL.
- `is_medical` = `population_code` contains `MEDICAL`.
- `evacuation_capacity` may be **-1** (FEMA's unknown). Treat `< 0` as unrecorded and emit `null`.
- Coordinates are `latitude`/`longitude` (the API renames them to `lat`/`lon`).
- Dropped on purpose: `ada_compliant` (a decoy; 63 YES nationally), `in_500_yr_floodplain`, `pre_landfall_shelter`, `fips_code`, and all `org_poc_*` contact columns.
- Shelters have **no tract id**. Place each on the nearest `vulnerability_tracts` centroid by `ST_DISTANCE`, and say so in the brief.

### 6.2 `vulnerability_tracts` (ACS + CDC SVI 2022 + CDC PLACES 2025), FL ≈ 5,160 rows
- Keys: `geo_id` (11-digit STRING, 2020 vintage), `county_fips` (= first 5 chars), `lat`, `lon` (NRI 2020 centroid, else 2010 internal point).
- ACS: `total_pop, households, households_no_vehicle, group_quarters_pop, mobile_homes, limited_english_spanish, median_income, poverty_rate, pop_65_plus`.
- SVI (renamed): `pct_disability, pct_age_65_plus, pct_no_vehicle, pct_limited_english, pct_mobile_homes, pct_group_quarters, pct_no_internet, svi_total_pop, svi_socioeconomic, svi_household, svi_housing_transport` (other SVI columns may remain with source names). **SVI Theme 3 and `RPL_THEMES` are excluded.**
- PLACES (modeled prevalence, %): `pct_mobility, pct_selfcare, pct_indeplive, pct_lacktrpt` (`pct_lacktrpt` is empty for FL).

### 6.3 `hazard_tracts` (FEMA National Risk Index), FL ≈ 5,114 rows
`geo_id, POPULATION, BUILDVALUE, HRCN_AFREQ, HRCN_EXPP, HRCN_EALT, HRCN_EALP, CFLD_AFREQ, CFLD_EXPP, CFLD_EALT, CFLD_EALP`
(uppercase as loaded; `-9999` already converted to NULL). Hurricane (`HRCN_*`) and coastal flooding (`CFLD_*`) only. "Planning purposes only", attribution required.

### 6.4 `care_facilities` (CMS Nursing Home Provider Information), FL ≈ 694 rows
Lower-cased CMS names; CMS renames columns between releases, so check them. Expected:
`cms_certification_number_ccn, provider_name, provider_address, citytown, state, zip_code,
number_of_certified_beds, ownership_type, latitude, longitude`. **No county column**: place each facility in a county by point-in-polygon (`ST_CONTAINS` with `geo_us_boundaries.counties`). Expose `ccn`, `name`, `certified_beds`.

### 6.5 `power_dependent_counties` (HHS emPOWER), FL 67 rows
Lower-cased emPOWER columns, including `fips_code` (5-digit county FIPS), `power_dependent_dme`
(the power-dependent devices count) and `power_dependent_is_censored` (TRUE when the value is 11,
meaning 1–10).

### 6.6 Tables we create

| Table | Columns | Purpose |
|---|---|---|
| `evacuation_readiness.shelter_places` | `shelter_id STRING, place_id STRING, first_seen TIMESTAMP` | The only Maps-derived thing we persist. MERGE on `shelter_id` |
| `evacuation_readiness.shelter_assessments` (tier 2) | `assessment_id, shelter_id, assessed_at, assessor_role, step_free_entrance, unlocked_outside_hours, lift_working, accessible_restroom, generator_confirmed, can_enter_unaccompanied_today, notes, source` | First-party records that close the unrecorded gap |
| `evacuation_readiness.plans` (tier 2) | `plan_id, version, created_at, approved_by, county_fips, payload JSON` | Approved allocation/rehearsal plans |
| `evacuation_readiness.decision_log` (tier 2) | `ts, actor, action, target_ids, detail JSON` | Audit trail |

### 6.7 Six defects to handle (from the challenge README)
1. 2,205 Wisconsin shelters have lat/lon swapped; one Texas shelter sits in Sonoma County, CA. Coordinates win silently in spatial joins: trust `county_fips` derived from coordinates and flag state/coordinate mismatches.
2. A blank string is not NULL (46,338 of 49,356 non-null `ada_compliant` values are a single space). Trim before testing.
3. `ada_compliant` is a decoy; use `wheelchair_accessible`.
4. Source flags are `YES`/`NO`, not `Y`/`N`. The notebook converts to BOOL. **`sql/01_county_ranking.sql` may compare to `'YES'` strings: if it errors or returns zeros, switch to `IS TRUE / IS FALSE / IS NULL`.**
5. Tract boundaries changed in 2020; the BigQuery geometry table is 2010 vintage, and about a third of 2020 tract ids will not match it. Use centroids (points), not polygons.
6. Impossible capacities (Commerce, GA rows: 270,135 and 225,112; St. Anthony Retreat, CA: 135,036). Flag them, never drop or overwrite them.

**Headline FL numbers (from the README, for the story lane):** 2,793 shelters, 848,784 spaces;
wheelchair yes 247 / no 83 / **unrecorded 2,463 (88%)**; 155 medical-needs shelters; median
tract-to-any-shelter distance under 2 km vs just over 7 km to one recorded as wheelchair
accessible; 493,461 Floridians more than 10 km from any shelter. Recompute from your tables before
quoting.

---

## 7. SQL files

All SQL lives in `sql/` as files with named parameters (`@county_fips`, `@limit`, `@reachable_km`,
`@geo_id`, `@usage`). Tools **and** data routes run the same files through
`google.cloud.bigquery` with `QueryJobConfig(query_parameters=[...])`. Every file starts with a
comment block: purpose, parameters, output columns, one sample output row.

**Three-state rule at the SQL boundary** (apply to every flag):
```sql
CASE WHEN wheelchair_accessible IS TRUE THEN 'yes'
     WHEN wheelchair_accessible IS FALSE THEN 'no'
     ELSE 'unrecorded' END AS wheelchair_accessible
```
Capacity: `IF(evacuation_capacity < 0, NULL, evacuation_capacity) AS evacuation_capacity`.
Ids: `CAST(shelter_id AS STRING)`, `geo_id` and `county_fips` as STRING.

| File | Parameters | Returns (one row per …) |
|---|---|---|
| `00_shelter_evidence.sql` (view) | none | shelter: all Shelter fields in three-state form, plus `nearest_geo_id` (nearest tract centroid via `ST_DISTANCE`) and its `HRCN_EALP`/`CFLD_EALP` |
| `01_county_ranking.sql` | none | county: `county_fips, name, total_pop, need_score, exposure_score, rank`, shelter counts (`total, medical_needs, in_surge_zone`, wheelchair yes/no/unrecorded), `center` |
| `02_rank_tracts.sql` | `@county_fips, @limit` | tract: `geo_id, lat, lon, total_pop, households_no_vehicle, pct_no_vehicle, pop_65_plus, pct_age_65_plus, pct_disability, mobile_homes, group_quarters_pop, need_score, nearest_shelter_km, nearest_wheelchair_yes_km` |
| `03_candidate_shelters.sql` | `@geo_id` or `@county_fips, @reachable_km, @usage` | shelter: Shelter fields + `straight_line_km` from the tract, ordered by distance; plus a summary row group of counts by wheelchair state |
| `04_gaps.sql` (or a Python rules function) | `@county_fips, @reachable_km` | gap: `category, affected ids, metric values` (see `find_readiness_gaps`) |
| `05_care_facilities.sql` | `@county_fips` | facility: `ccn, name, lat, lon, certified_beds`, nearest generator-`yes` shelter km |
| `06_power_dependence.sql` | `@county_fips` | one row: `county_fips, power_dependent_dme, is_censored` |
| `07_county_map.sql` | `@county_fips` | the four map layers (tracts, shelters, care facilities, power) |
| `08_equity_audit.sql` | `@geo_ids ARRAY<STRING>, @county_fips` | metric: `recommended` vs `county` vs `state` averages (see the audit route) |

**need_score** (state the formula in `assumptions` and the deck; recommended default): min-max
normalise within the state, then weight
`0.35·households_no_vehicle + 0.20·pop_65_plus + 0.20·pct_disability + 0.15·mobile_homes + 0.10·group_quarters_pop`.
**exposure_score:** normalised `HRCN_EALP + CFLD_EALP` (expected annual loss, population).
Neither may use race, `RISK_SCORE` or `*_RISKS`.

**Reachable without a car:** default `reachable_km_no_vehicle = 3.0` (roughly a 40-minute walk for an
older adult, and beyond typical para-transit "near" thresholds). The data lane may change it, but
must state the number and one sentence of why; it travels in `assumptions`.

---

## 8. Agents

| Agent | ADK type | Job | Tools | Model |
|---|---|---|---|---|
| `readiness_agent` (root) | `LlmAgent` | Answers follow-up questions in chat; knows when Maps can help and when it cannot | all custom tools, `google_maps_grounding`, BigQuery MCP toolset | `gemini-3.6-flash` |
| `brief_analyst` | `LlmAgent` | For one county: rank who cannot leave, pick candidate shelters, verify them one building at a time, **act on results** (drop closed, flag renamed, swap in the next candidate), find gaps | custom tools only | `gemini-3.6-flash` |
| `brief_writer` | `LlmAgent` with `output_schema=BriefJudgments`, `output_key="judgments"` | Turns facts into judgments: reasons, verdicts, fixes text, unknowns text, headline. Refers to facts **by id only** | none | `gemini-3.6-flash` (try a Pro model if wording is weak) |
| `brief_pipeline` | `SequentialAgent(sub_agents=[brief_analyst, brief_writer])` | Runs for `POST /v1/briefs` | its agents' tools | same |
| `assessment_agent` (tier 2) | `LlmAgent` | Interviews a shelter operator: can a wheelchair user get in, **unaccompanied, today**? | `append_assessment` | `gemini-3.6-flash` |

- **Why the writer is separate:** `output_schema` is built for a final reply (older ADK disabled tools with it). A tool-free writer gives guaranteed JSON; the analyst keeps its tools.
- **Fallback if forced onto Gemini 2.5:** move Maps into `maps_agent = LlmAgent(tools=[google_maps_grounding])` and give the root `AgentTool(agent=maps_agent)`. On 2.5 a built-in tool plus function tools in one agent returns `400 INVALID_ARGUMENT ... Multiple tools are supported only when they are all search tools`.
- Ignore the warning `Tools at indices [0] are not compatible with automatic function calling (AFC)`. It is noise.

### 8.1 System instruction blocks (`agent/readiness_agent/prompts.py`)

**Instruction templating:** ADK injects session state into instructions where it sees `{key}`
(`{key?}` = optional, empty if missing). Use braces **only** for state keys; write literal braces
nowhere else in instructions or the run will fail.

`COMMON_RULES` (included in every agent):
```
You support a county emergency manager doing hurricane-season PLANNING weeks ahead of any storm.
Rules:
- Unrecorded is not no. Shelter flags are yes, no or unrecorded. Report counts of each, e.g.
  "11 of these I can speak to; 64 nobody has assessed". Never say "no accessible shelters" when
  the field is blank.
- Label every claim historical (federal record) or live (Google Maps lookup, with the time).
- Power dependence (emPOWER) is county-level: say how many, never which block. A value of 11 means 1 to 10.
- Name the transport measure: households_no_vehicle and pct_no_vehicle measure car ownership, not
  lacking a ride. pct_lacktrpt is not published for Florida.
- CDC PLACES values are modeled prevalence, not counts. Say so.
- Distances are straight-line km unless a drive time came back from Google Maps routing. Say which.
- Every number you state must come from a tool result. If you do not have it, say so.
- Google Maps answers about one named building at one address. Never ask Maps area questions about
  accessibility ("which shelters take wheelchairs"); it cannot answer them.
- "Open now" on Google Maps means business hours, not that the county opened it as a shelter.
- Planning only. If asked to dispatch help, or whether a shelter is open during an active storm,
  refuse and point to the county emergency operations center or 911.
- Individual people are never identified. Work by census tract and facility only.
```

`ANALYST` (adds): step order `rank_vulnerable_tracts` → `list_care_facilities` →
`get_power_dependence` → `find_candidate_shelters` for the top tracts → `verify_shelter` for up to
`{max_shelters_to_verify}` candidates (closest first, prefer `usage = EVAC`) → act on each result
(closed or not_found: drop it and verify the next candidate; renamed: keep and flag;
disagree on wheelchair: flag `record_mismatch`) → `find_readiness_gaps` with the verification
results. Finish with a short plain list of the ids you kept, dropped and flagged.

`WRITER` (adds): *"You receive facts as JSON in `{facts_digest}`. Produce BriefJudgments only. Refer
to tracts, shelters and facilities by their ids exactly as given. Do not write any number that is
not in the facts. Keep each reason under 30 words. The headline states the single most important
finding with its numbers."*

`CHAT` (adds): when to use which tool; prefer custom tools; use the BigQuery MCP toolset only for
questions no custom tool covers (read-only); use `verify_shelter` for any "is it still there / what is
it now / can a wheelchair get in" question about a named shelter; return ids of anything the map
should outline.

---

## 9. Tools

Location: `agent/readiness_agent/tools/`. Plain Python functions with type hints and a docstring
(the docstring is what Gemini reads); register with `FunctionTool(fn)`. Accept a
`tool_context: ToolContext` parameter where they write state.

**Rules every tool follows**
- Return at most ~50 rows, always with ids (`geo_id`, `shelter_id`, `ccn`), plus `status` and `source`.
- Write the full structured result to `tool_context.state["facts"]` (a JSON-serialisable dict keyed by kind and id) and refresh `tool_context.state["facts_digest"]` (compact JSON string the writer reads). The API builds the brief's numbers from `facts`, never from model text.
- Three-state flags and capacity rules at the SQL boundary (section 7).
- Run the same `sql/` files as the data routes.

| Tool | Kind | Input | Returns |
|---|---|---|---|
| `rank_vulnerable_tracts` | Python + BigQuery | `county_fips: str, limit: int = 10` | tracts ranked by need with drivers: `households_no_vehicle, pct_no_vehicle, pop_65_plus, pct_disability, mobile_homes, group_quarters_pop`, `nearest_shelter_km`, `nearest_wheelchair_yes_km` |
| `list_care_facilities` | Python + BigQuery | `county_fips: str` | nursing homes in the county (point-in-polygon) with `ccn`, `certified_beds`, nearest generator-yes shelter km |
| `get_power_dependence` | Python + BigQuery | `county_fips: str` | `power_dependent_dme`, `is_censored`, caveat text "County total: how many people, not where they live." |
| `find_candidate_shelters` | Python + BigQuery | `geo_id: str \| None, county_fips: str \| None, reachable_km: float = 3.0, usage: str \| None = None` | nearest shelters with `straight_line_km`, capacities, three-state flags, and counts by wheelchair state (`yes/no/unrecorded`) |
| `verify_shelter` | Python, **one Gemini call with Maps grounding per building** (+ routing) | `shelter_id: str, from_geo_id: str \| None = None, include_route: bool = True` | a `ShelterVerification` (section 10) |
| `find_readiness_gaps` | Python + BigQuery + rules | `county_fips: str, reachable_km_no_vehicle: float, verification_results` (read from state if omitted) | gaps by category (below) |

### 9.1 `verify_shelter`, the differentiator (build and test it first)

Implementation (direct `google.genai` call inside the tool, so grounding metadata is in hand):

```python
from google import genai
from google.genai import types

client = genai.Client(vertexai=True, project=PROJECT, location="global")

maps_tool = types.Tool(google_maps=types.GoogleMaps(
    grounding_types=types.GoogleMapsGroundingTypes(
        places=types.GoogleMapsPlaces(),
        routing=types.GoogleMapsRouting() if include_route else None,
    )))
config = types.GenerateContentConfig(
    tools=[maps_tool],
    tool_config=types.ToolConfig(retrieval_config=types.RetrievalConfig(
        lat_lng=types.LatLng(latitude=shelter_lat, longitude=shelter_lon))),
    temperature=0,
)
resp = await client.aio.models.generate_content(model=MODEL, contents=prompt, config=config)
```

Prompt (one named building at one address, the only form that gets definite answers, about 5 in 8 in the README's tests):
```
I am verifying one building for a hurricane-season planning record. Use Google Maps.
Building on file: "<shelter_name>", <address_1>, <city>, FL <zip>. Coordinates on file: <lat>, <lon>.
[If routing: Also give the driving time by car from <from_lat>, <from_lon> to this building.]
Answer with exactly these lines and nothing else:
STATUS: confirmed | renamed | closed | not_found | unclear
MAPS_NAME: <name on Google Maps, or none>
PLACE_TYPE: <what the place is now, e.g. high school, church, community center>
OPEN_NOW: yes | no | unknown
WHEELCHAIR_ENTRANCE: yes | no | no_answer
DRIVE_MINUTES: <number, or none>
NOTE: <one sentence>
```
- `confirmed` = same place, same name; `renamed` = same address, different name (for example First Presbyterian Church of Maitland → Maitland Presbyterian Church); `closed` = permanently closed; `not_found` = nothing at that address; `unclear` = ambiguous.
- Parse lines with a regex; validate each enum; any parse failure → `status: "unclear"`; exceptions → `status: "error"`.
- **Sources:** read `resp.candidates[0].grounding_metadata`. Each `grounding_chunks[i].maps` has `place_id, title, uri` (and `route` with `distance_meters, duration, encoded_polyline` when routing returned one). Each `grounding_supports[j]` maps a text `segment` to `grounding_chunk_indices`; treat `segment.start_index is None` as 0; ignore `confidence_scores` (never populated). Build `summary[]` segments and `sources[]` (`src_1`, `src_2`… unique within the response).
- **Drive time:** prefer a chunk's `route.duration` / `distance_meters`; else the parsed `DRIVE_MINUTES`; else `drive: null`. Never fabricate one.
- **Wheelchair agreement** (deterministic Python): record ∈ {yes, no, unrecorded}, maps ∈ {yes, no, no_answer} →
  `agree` (both answered, same), `disagree` (both answered, differ), `record_blank` (record unrecorded, Maps answered), `maps_blank` (record answered, Maps no_answer), `both_blank`.
- **Persist** only `place_id` via `MERGE` into `shelter_places` (shelter_id, place_id, first_seen).
- **Concurrency:** verify in parallel with `asyncio.Semaphore(6)`; 30 s timeout per call; one retry with backoff on 429/503. 5–8 s per call is normal; 12 shelters ≈ 10–20 s in parallel. Check Vertex AI quota.
- Write the verification into `state["facts"]["verifications"][shelter_id]` with `checked_at` (UTC ISO).

### 9.2 `find_readiness_gaps` categories
| category | Rule (defaults; the data lane owns the numbers) |
|---|---|
| `no_accessible_destination` | a top-need tract has no shelter with wheelchair `yes` within `reachable_km_no_vehicle`; split into "recorded no" vs "nobody assessed" |
| `unassessed_access` | candidate shelters whose wheelchair flag is unrecorded and Maps `no_answer` |
| `generator_gap` | a nursing home with no generator-`yes` shelter within 10 km |
| `shelter_in_surge_zone` | `in_surge_slosh_area = yes` or `in_100_yr_floodplain = yes` (EVAC shelters especially) |
| `record_mismatch` | Maps `closed`/`not_found`/`renamed`, or wheelchair `disagree` |
| `medical_needs_gap` | medical-needs shelter capacity in the county is small relative to `power_dependent_dme` (state the ratio; never assign people to blocks) |

### 9.3 BigQuery MCP toolset (chat only; satisfies the managed-MCP requirement)
```python
import google.auth, google.auth.transport.requests
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
def _bq_headers(_ctx):
    if not _creds.valid:
        _creds.refresh(google.auth.transport.requests.Request())
    return {"Authorization": f"Bearer {_creds.token}"}

bigquery_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url="https://bigquery.googleapis.com/mcp"),
    header_provider=_bq_headers,
)
```
- **Requires the `mcp` package** (`pip install mcp`); without it the MCP classes silently do not import.
- Identity roles: `roles/mcp.toolUser`, `roles/bigquery.jobUser`, `roles/bigquery.dataViewer`.
- Limits: `execute_sql`/`execute_sql_readonly` time out after 3 minutes; results capped at 3,000 rows. Use the read-only tool (`tool_filter` to read-only tools if needed).

---

## 10. API contract (v1)

Nine routes under `/v1` on `readiness-api`. Map data comes straight from SQL in seconds; anything
involving the model goes through an agent.

| Method | Path | Front end uses it for | Answered by | Expected time |
|---|---|---|---|---|
| GET | `/v1/health` | Mock or live badge, model name | API | < 0.1 s |
| GET | `/v1/counties` | County picker, ranked by need × exposure | `sql/01_county_ranking.sql` | ~2 s, cached |
| GET | `/v1/counties/{county_fips}/map` | Map layers: tracts, shelters, care facilities, power | SQL | ~3 s, cached |
| GET | `/v1/shelters/{shelter_id}` | Shelter detail panel | SQL | ~1 s |
| POST | `/v1/shelters/{shelter_id}/verify` | "Check on Google Maps" button | `verify_shelter` | 5–8 s |
| POST | `/v1/briefs` | "Generate brief": starts a job, returns 202 at once | `brief_pipeline` | 60–120 s in background |
| GET | `/v1/briefs/{brief_id}` | Polled every 2 s for progress, then the brief | API | < 0.1 s |
| GET | `/v1/briefs/{brief_id}/audit` | Equity audit panel | SQL | ~3 s |
| POST | `/v1/chat` | Follow-up questions, map highlights | `readiness_agent` | 5–30 s |

**Conventions**
- JSON, snake_case. Ids are strings: `county_fips` (5 digits), `geo_id` (11 digits), `shelter_id`, `ccn`.
- Recorded shelter flags are `"yes"`, `"no"` or `"unrecorded"`, never booleans or null. The UI shows unrecorded as grey "Not recorded", never as no.
- Every fact carries `source` ∈ {`fema_nss`, `acs`, `cdc_svi`, `cdc_places`, `fema_nri`, `cms`, `hhs_empower`, `google_maps`} and `freshness` ∈ {`historical`, `live`} (live with `checked_at`).
- Distances are `straight_line_km`; `drive_minutes` appears only when Maps routing returned one.
- Coordinates WGS84 decimal degrees as `lat`, `lon`.
- Errors, one shape, HTTP 400/404/409/503/504: `{"error": {"code": "county_not_found", "message": "No county 12999 in FL", "retryable": false}}`. Model quota errors → 503 with `retryable: true`.

### 10.1 Shared objects (exact shapes; values illustrative)

**Shelter**
```json
{
  "shelter_id": "352718",
  "name": "Eastside High School",
  "address": "1200 Example Ave, Fort Myers, FL 33916",
  "county_fips": "12071",
  "lat": 26.6391,
  "lon": -81.8412,
  "nearest_geo_id": "12071050100",
  "usage": "EVAC",
  "evacuation_capacity": 1200,
  "post_impact_capacity": 600,
  "wheelchair_accessible": "unrecorded",
  "generator_onsite": "yes",
  "in_surge_slosh_area": "no",
  "in_100_yr_floodplain": "unrecorded",
  "is_medical": false,
  "place_id": null,
  "source": "fema_nss",
  "freshness": "historical"
}
```
`usage` is FEMA's code or null. `evacuation_capacity` is null when FEMA recorded a negative number.
`place_id` stays null until the shelter has been verified once.

**Source and Segment** (the attribution pair)
```json
{
  "source": {"id": "src_1", "provider": "Google Maps", "title": "Eastside Magnet High School", "uri": "https://maps.google.com/?cid=0000000000", "place_id": "ChIJ_example_1"},
  "segment": {"text": "Google Maps lists it as an active public high school.", "source_ids": ["src_1"], "freshness": "live"}
}
```

**ShelterVerification**
```json
{
  "shelter_id": "352718",
  "checked_at": "2026-10-20T15:41:02Z",
  "status": "renamed",
  "maps_name": "Eastside Magnet High School",
  "record_name": "Eastside High School",
  "place_type": "high school",
  "open_now": true,
  "wheelchair": {"maps": "yes", "record": "unrecorded", "agreement": "record_blank"},
  "drive": {"from_geo_id": "12071050100", "drive_minutes": 9, "drive_km": 4.0},
  "summary": [
    {"text": "Same address, now listed as Eastside Magnet High School.", "source_ids": ["src_1"], "freshness": "live"},
    {"text": "Google Maps reports a wheelchair-accessible entrance.", "source_ids": ["src_1"], "freshness": "live"}
  ],
  "sources": ["<Source>"]
}
```
- `status`: `confirmed | renamed | closed | not_found | unclear | error`
- `open_now`: `true | false | null` (null = unknown)
- `wheelchair.maps`: `yes | no | no_answer`; `wheelchair.agreement`: `agree | disagree | record_blank | maps_blank | both_blank`
- `drive` is null when routing was not requested or not returned.

### 10.2 Route payloads

**GET /v1/health**
```json
{"status": "ok", "mode": "mock", "model": "gemini-3.6-flash", "state": "FL", "dataset": "evacuation_readiness", "version": "0.1.0"}
```

**GET /v1/counties**
```json
{
  "state": "FL",
  "counties": [
    {
      "county_fips": "12071",
      "name": "Lee County",
      "rank": 1,
      "need_score": 0.91,
      "exposure_score": 0.97,
      "total_pop": 760822,
      "shelters": {"total": 74, "medical_needs": 3, "in_surge_zone": 11, "wheelchair": {"yes": 9, "no": 2, "unrecorded": 63}},
      "center": {"lat": 26.57, "lon": -81.87}
    }
  ]
}
```

**GET /v1/counties/{county_fips}/map**
```json
{
  "county_fips": "12071",
  "center": {"lat": 26.57, "lon": -81.87},
  "tracts": [
    {
      "geo_id": "12071050100", "lat": 26.6406, "lon": -81.8723, "total_pop": 4210,
      "households_no_vehicle": 412, "pct_no_vehicle": 25.3, "pop_65_plus": 1180,
      "pct_disability": 19.4, "mobile_homes": 310, "need_score": 0.88,
      "nearest_shelter_km": 1.9, "nearest_wheelchair_yes_km": 11.8
    }
  ],
  "shelters": ["<Shelter>"],
  "care_facilities": [
    {"ccn": "105678", "name": "Example Gardens Nursing Center", "lat": 26.6102, "lon": -81.8015, "certified_beds": 120}
  ],
  "power_dependence": {"county_fips": "12071", "power_dependent_dme": 5480, "is_censored": false, "scope": "county"}
}
```
Tracts are centroid points, not polygons (2010 geometry would drop a third of 2020 ids).

**GET /v1/shelters/{shelter_id}**: the Shelter object plus `pet_accommodations_code`, `population_code`, `shelter_status_code`, `incident_id` and `last_verification` (a ShelterVerification from this session, or null).

**POST /v1/shelters/{shelter_id}/verify**: request `{"from_geo_id": "12071050100", "include_route": true}`; response a ShelterVerification.

**POST /v1/briefs**
```json
{"county_fips": "12071", "track": "shelter_readiness", "options": {"reachable_km_no_vehicle": 3.0, "max_shelters_to_verify": 12, "include_routes": true}}
```
`track`: `shelter_readiness | getting_people_out | medically_dependent`. Response **202**:
```json
{"brief_id": "brf_01JB7X", "status": "running", "poll_url": "/v1/briefs/brf_01JB7X"}
```

**GET /v1/briefs/{brief_id}**, while running:
```json
{"brief_id": "brf_01JB7X", "status": "running", "progress": {"step": "verify_shelters", "message": "Checking shelters on Google Maps", "done": 5, "total": 12}, "brief": null, "error": null}
```
`step` runs `rank_tracts → find_shelters → verify_shelters → find_gaps → write_brief → complete`; `status` ends `complete` or `failed`.

When complete:
```json
{
  "brief_id": "brf_01JB7X",
  "status": "complete",
  "progress": {"step": "complete", "message": "Done", "done": 12, "total": 12},
  "error": null,
  "brief": {
    "county": {"county_fips": "12071", "name": "Lee County", "state": "FL"},
    "track": "shelter_readiness",
    "generated_at": "2026-10-20T15:42:10Z",
    "model": "gemini-3.6-flash",
    "headline": "Of 12 shelters serving the five least mobile tracts, 2 can be counted on for wheelchair users; 9 nobody has assessed.",
    "assumptions": {
      "reachable_km_no_vehicle": 3.0,
      "transport_measure": "households_no_vehicle (ACS): owning no car, not lacking a ride",
      "distance": "Straight line unless drive_minutes is shown",
      "shelter_to_tract": "Nearest tract centroid by ST_DISTANCE"
    },
    "who_cannot_leave": [
      {
        "rank": 1, "kind": "tract", "id": "12071050100", "label": "Tract 501",
        "lat": 26.6406, "lon": -81.8723, "people": 4210,
        "drivers": [
          {"metric": "households_no_vehicle", "value": 412, "unit": "households", "source": "acs"},
          {"metric": "pct_age_65_plus", "value": 28.0, "unit": "%", "source": "cdc_svi"}
        ],
        "reason": "412 households own no car, and the nearest shelter recorded as wheelchair accessible is 11.8 km away."
      },
      {
        "rank": 2, "kind": "care_facility", "id": "105678", "label": "Example Gardens Nursing Center",
        "lat": 26.6102, "lon": -81.8015, "people": 120,
        "drivers": [{"metric": "certified_beds", "value": 120, "unit": "beds", "source": "cms"}],
        "reason": "120 certified beds and no shelter with a recorded generator within 10 km."
      }
    ],
    "power_dependence": {"power_dependent_dme": 5480, "is_censored": false, "caveat": "County total: how many people, not where they live."},
    "destinations": [
      {
        "shelter": "<Shelter>",
        "serves": ["12071050100"],
        "straight_line_km": 3.1,
        "verification": "<ShelterVerification>",
        "verdict": "verify_before_season",
        "verdict_reason": [
          {"text": "Still an active school under a new name.", "source_ids": ["src_1"], "freshness": "live"},
          {"text": "FEMA never recorded wheelchair access, so confirm the entrance on site.", "source_ids": [], "freshness": "historical"}
        ]
      }
    ],
    "fixes": [
      {
        "priority": 1,
        "category": "no_accessible_destination",
        "title": "No shelter recorded as wheelchair accessible within 3 km of tracts 501 and 603",
        "recommendation": "Assess Eastside High School's entrance before September 1. It is the closest shelter to both tracts and its record is blank.",
        "affected": {"tracts": ["12071050100", "12071060300"], "shelters": ["352718"], "care_facilities": []},
        "source_ids": ["src_1"]
      }
    ],
    "unknowns": [
      {"topic": "Wheelchair access", "count": 63, "of": 74, "unit": "shelters", "why": "FEMA's field is blank: nobody recorded it, which is not the same as no.", "how_to_close": "Call or visit each site, or run the assessment agent."},
      {"topic": "Where power-dependent residents live", "count": 5480, "of": null, "unit": "people", "why": "emPOWER publishes county totals only.", "how_to_close": "No public source. Ask the county health department."}
    ],
    "sources": ["<Source>"]
  }
}
```
- `kind`: `tract | care_facility`. `verdict`: `holds_up | verify_before_season | do_not_rely`.
- `category`: `no_accessible_destination | unassessed_access | generator_gap | shelter_in_surge_zone | record_mismatch | medical_needs_gap`.
- **The writer produces only** `headline`, `reason`, `verdict`, `verdict_reason`, the text of `fixes` (`title`, `recommendation`, `priority`, `category`), and `why`/`how_to_close` in `unknowns`. Every number and id comes from tool results; **the API drops any id the writer invents** and logs it. Optional guard: flag writer text containing a number that is not in `facts`.

**`BriefJudgments`** (writer `output_schema`, in `contract/schemas.py`):
```
headline: str
tract_reasons: list[{id: str, reason: str}]
destination_verdicts: list[{shelter_id: str, verdict: Verdict, verdict_reason: list[Segment]}]
fixes: list[{priority: int, category: FixCategory, title: str, recommendation: str,
             affected: {tracts: list[str], shelters: list[str], care_facilities: list[str]},
             source_ids: list[str]}]
unknowns_text: list[{topic: str, why: str, how_to_close: str}]
```

**GET /v1/briefs/{brief_id}/audit**
```json
{
  "brief_id": "brf_01JB7X",
  "compares": "Tracts in who_cannot_leave against every tract in the county and the state",
  "rows": [
    {"metric": "poverty_rate", "unit": "%", "recommended": 24.1, "county": 13.2, "state": 12.7, "used_as_input": false},
    {"metric": "pct_limited_english", "unit": "%", "recommended": 9.8, "county": 5.1, "state": 6.5, "used_as_input": false},
    {"metric": "shelters_access_unrecorded", "unit": "%", "recommended": 85, "county": 85, "state": 88, "used_as_input": false}
  ],
  "finding": "The ranking lands on poorer tracts, and their shelters are recorded no better than average, so it follows need, not paperwork."
}
```
Add ACS race and ethnicity shares as rows (audit only, never an input). `finding` is written by a person (data or story lane), so it may be null. The question it answers: *did the plan land where people cannot leave, or where somebody filled in the form?*

**POST /v1/chat**
Request:
```json
{"session_id": null, "county_fips": "12071", "brief_id": "brf_01JB7X", "message": "Is Eastside High School still a school, and can a wheelchair user get in?"}
```
Response:
```json
{
  "session_id": "ses_9c1d",
  "reply": [
    {"text": "Yes. Google Maps lists it as an active public high school, now named Eastside Magnet High School.", "source_ids": ["src_1"], "freshness": "live"},
    {"text": "FEMA's record does not say whether it is wheelchair accessible. That field was never filled in.", "source_ids": [], "freshness": "historical"}
  ],
  "sources": ["<Source>"],
  "highlight": {"tracts": [], "shelters": ["352718"], "care_facilities": []},
  "tool_calls": [{"name": "verify_shelter", "duration_ms": 6400}],
  "refused": false
}
```
`highlight` lists ids the map should outline. `refused` is true when the agent declined (for example a live-emergency request). The client sends `session_id` back with the next message.

### 10.3 How the API runs the agents
```python
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

sessions = InMemorySessionService()
runner = Runner(app_name="readiness", agent=brief_pipeline, session_service=sessions)
s = await sessions.create_session(app_name="readiness", user_id="api",
        state={"county_fips": fips, "max_shelters_to_verify": 12, "reachable_km": 3.0, "facts": {}})
async for event in runner.run_async(user_id="api", session_id=s.id,
        new_message=types.Content(role="user", parts=[types.Part(text=f"Prepare the brief for county {fips}.")])):
    ...  # update job progress from tool-call events; collect event.grounding_metadata in chat
final = await sessions.get_session(app_name="readiness", user_id="api", session_id=s.id)
facts, judgments = final.state["facts"], final.state["judgments"]
brief = assemble_brief(facts, judgments)   # numbers from facts; text from judgments; drop unknown ids
```
- Brief jobs run as `asyncio` background tasks after the 202, stored in an in-memory dict with expiry (one instance; see section 14).
- Progress: map tool names to steps in a `before_tool_callback`/`after_tool_callback` (signature `(tool, args, tool_context) -> dict | None`) or by inspecting function-call events.
- Chat attribution: ADK `Event` has `grounding_metadata`; convert as in 9.1.
- Mount ADK routes: `app = get_fast_api_app(agents_dir="agent", web=True, allow_origins=["*"])` from `google.adk.cli.fast_api`, then add the `/v1` router to the same app.
- `MOCK_MODE=1` serves `contract/mocks/*.json` for every route; `LIVE_ROUTES=counties,map,verify,...` switches routes to real code one by one.

---

## 11. Google Maps attribution

The API does the attribution work; the front end only renders it.

1. **Agent side:** `verify_shelter` and chat read `grounding_metadata`: `grounding_chunks[i].maps` gives `place_id`, `title`, `uri`; `grounding_supports` maps text segments to chunk indices. A `None` `segment.start_index` means 0; ignore `confidence_scores`.
2. **API side:** convert to `segments[]` and `sources[]` with ids `src_1, src_2…` unique within the response.
3. **Front end:** render each segment, then its sources **on the next line in the same card**. Never in another tab or behind more than one click.
4. **The words:** render the provider exactly as `<span translate="no" style="white-space: nowrap">Google Maps</span>`: no restyling, no capitalisation change, no line break. In Streamlit use `st.html` or `st.markdown(..., unsafe_allow_html=True)`.
5. **Storage:** persist only `place_id` in `shelter_places`. Re-ground at runtime. Brief jobs live in memory and expire.
6. **Download:** a brief export carries FEMA-derived content and `place_id` only, never Maps text.
7. **Maps on a map:** plot only our own coordinates (FEMA, ACS, CMS) on the basemap. Maps-derived content appears in cards with attribution. If you ever plot Maps results geographically, use a Google map (Maps JavaScript API).

---

## 12. Front end (Streamlit)

`frontend/app.py` + `frontend/api_client.py` (one function per route; `USE_MOCK=1` reads `contract/mocks/`).

**Layout: a season console**
- **Header:** title, scenario sentence, mode badge from `/v1/health` (Mock / Live, model name).
- **Left column:** county picker (from `/v1/counties`, ranked), track picker, "reachable without a car" slider (default 3 km), "Generate brief" button; a phase strip Oct→Nov (Inventory · Plan · Brief · Watch · Activate · Learn) with Brief active (other phases appear as tier-2 features land).
- **Centre: map** (`st.pydeck_chart` or `st.map` with our own coordinates):
  - tracts as circles sized by `households_no_vehicle`, coloured by `need_score`;
  - shelters as pins coloured by **evidence state**: green = holds up / verified, amber = verify before season or renamed, red = do not rely / closed / in surge zone, **grey = not recorded** (never red for unrecorded);
  - nursing homes sized by beds; highlight rings for ids in `highlight`; lines from tracts to the shelters that serve them (`destinations[].serves`).
- **Right column: tabs** "Brief" | "Shelter" | "Chat" | "Audit".
  - **Brief:** progress bar while polling every 2 s (step + done/total), then the four segments as cards: Who cannot leave (ranked, numbers, reason) · Where they would go (shelter card: record flags as chips, verification summary segments, sources directly under them, verdict) · What to fix (priority list, affected ids clickable to highlight) · What we could not tell you (count of total, why, how to close). Assumptions shown in a small "How we measured" box.
  - **Shelter:** detail from `/v1/shelters/{id}`; "Check on Google Maps" button → `/verify`; side by side **"FEMA record (historical)"** vs **"Google Maps (checked HH:MM)"**.
  - **Chat:** messages as segments with sources under each; refused replies shown in a distinct style with the EOC/911 pointer.
  - **Audit:** table from `/audit` with `used_as_input` shown.
- Attribution component reused everywhere (section 11).
- Streamlit session keeps `session_id`, `brief_id`. Deploy with `--session-affinity`.

---

## 13. Repository layout and configuration

```
BUILD_SPEC.md                 this file (also usable as AGENTS.md / CLAUDE.md)
Dockerfile                    API image, built from repo root (needs agent/, contract/, sql/, api/)
requirements.txt / pyproject  google-adk==2.12.0, mcp, google-cloud-bigquery, db-dtypes, pandas,
                              fastapi, uvicorn, pydantic, ortools (tier 2), pytest
contract/
  schemas.py                  Pydantic models: THE contract (enums as Literal types)
  mocks/                      one JSON file per route, valid against schemas.py
sql/                          00_shelter_evidence.sql … 08_equity_audit.sql
agent/
  readiness_agent/
    __init__.py               from . import agent
    agent.py                  root_agent (= readiness_agent), brief_pipeline, assessment_agent
    prompts.py                COMMON_RULES, ANALYST, WRITER, CHAT
    tools/                    bq.py (runs sql files), tracts.py, shelters.py, verify.py,
                              gaps.py, care.py, power.py, mcp.py, optimize.py (tier 2)
api/
  main.py                     FastAPI: get_fast_api_app(...) + /v1 router + MOCK_MODE/LIVE_ROUTES
  briefs.py                   job store, background runner, assemble_brief()
  attribution.py              grounding_metadata → segments/sources
frontend/
  app.py                      Streamlit
  api_client.py               one function per route; USE_MOCK
  Dockerfile                  UI image
tests/
  test_contract.py            every mock validates against schemas.py
  test_tools.py               three-state, capacity, agreement, parser, gap rules
  evals/readiness.evalset.json   section 16 questions
notebooks/                    challenge notebook (unchanged)
scripts/load.sh               challenge fallback loader (unchanged)
```

**Branches:** one per lane: `data`, `agent`, `frontend`, `story`. The contract goes on `main` first.
**Contract changes after v0:** additive only (new optional fields), in a PR labelled `contract`,
approved by one front-end and one agent person, with mocks updated in the same PR.

**Environment variables**

| Var | Example | Used by |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | `<your project id>` | API |
| `GOOGLE_CLOUD_LOCATION` | `global` | API (Gemini) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` | API |
| `BQ_DATASET` | `evacuation_readiness` | API |
| `BQ_LOCATION` | `US` | API |
| `STATE` | `FL` | API |
| `MODEL` | `gemini-3.6-flash` | API |
| `MOCK_MODE` | `1` | API |
| `LIVE_ROUTES` | `counties,map,verify` | API |
| `MAX_PARALLEL_VERIFY` | `6` | API |
| `API_BASE_URL` | Cloud Run URL of readiness-api | UI |
| `USE_MOCK` | `1` | UI |

**Credentials:** never hard-code a project id, key or account. Local dev uses Application Default Credentials (`gcloud auth login`, `gcloud auth application-default login`, `gcloud config set project <id>`, `gcloud auth application-default set-quota-project <id>`). Cloud Run uses the `readiness-api` service account. Keep `.env` out of git.

---

## 14. Deployment

```bash
PROJECT=$(gcloud config get-value project)
REGION=us-central1
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com

# Identity for the API: read and write BigQuery, call Gemini, use the BigQuery MCP server
gcloud iam service-accounts create readiness-api
SA=readiness-api@$PROJECT.iam.gserviceaccount.com
for role in roles/bigquery.dataEditor roles/bigquery.jobUser roles/aiplatform.user roles/mcp.toolUser; do
  gcloud projects add-iam-policy-binding $PROJECT --member serviceAccount:$SA --role $role
done

# API (agents + /v1). Start with MOCK_MODE=1, then LIVE_ROUTES as routes land
gcloud run deploy readiness-api --source . --region $REGION --service-account $SA \
  --set-env-vars GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=True,BQ_DATASET=evacuation_readiness,MODEL=gemini-3.6-flash,MOCK_MODE=1 \
  --min-instances 1 --max-instances 1 --no-cpu-throttling --timeout 300 --memory 2Gi \
  --allow-unauthenticated

# UI
API_URL=$(gcloud run services describe readiness-api --region $REGION --format 'value(status.url)')
gcloud run deploy readiness-ui --source frontend --region $REGION \
  --set-env-vars API_BASE_URL=$API_URL \
  --session-affinity --min-instances 1 --allow-unauthenticated
```

| Flag | Why |
|---|---|
| `--min-instances 1` | No cold start on the first demo request |
| `--max-instances 1` (API) | Brief jobs and chat sessions live in memory; one instance means polling always finds them |
| `--no-cpu-throttling` (API) | The brief runs after the 202 returns; by default Cloud Run cuts CPU outside requests |
| `--timeout 300` (API) | Chat turns with several Maps calls can run long |
| `--session-affinity` (UI) | Streamlit keeps each session in memory over a WebSocket |

- Containers listen on `$PORT` (8080): `uvicorn api.main:app --host 0.0.0.0 --port $PORT` and `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`.
- Local dev in Cloud Shell: run both, open with Web Preview.
- If org policy blocks `--allow-unauthenticated`: deploy the API without it, grant `roles/run.invoker` to the UI's service account, and have `api_client.py` send an ID token from `google.oauth2.id_token.fetch_id_token`.
- Note `--max-instances 1` + in-memory jobs is a hackathon choice; production moves jobs to Firestore/BigQuery and lifts the cap.

---

## 15. Verified API reference (ADK 2.12 / google-genai 2.31)

Checked against the installed packages on 2026-10-20. Prefer these exact spellings.

```python
# Agents
from google.adk import Agent, Runner                     # Agent == LlmAgent
from google.adk.agents import LlmAgent, SequentialAgent   # LlmAgent fields include: output_schema,
                                                          # output_key, include_contents, tools,
                                                          # sub_agents, before_tool_callback, after_tool_callback
from google.adk.tools import FunctionTool, ToolContext, google_maps_grounding  # GoogleMapsGroundingTool instance
from google.adk.tools.agent_tool import AgentTool        # Gemini 2.5 fallback only
from google.adk.sessions import InMemorySessionService
from google.adk.cli.fast_api import get_fast_api_app     # (agents_dir=..., web=True, allow_origins=[...], ...)

# Human approval on a tool: the run pauses with an `adk_request_confirmation` call
FunctionTool(fn, require_confirmation=True)

# MCP (needs `pip install mcp`)
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
# McpToolset(connection_params=..., tool_filter=..., header_provider=callable(ctx)->dict, ...)

# Gemini + Maps grounding
from google.genai import types
types.GoogleMaps(grounding_types=types.GoogleMapsGroundingTypes(
    places=types.GoogleMapsPlaces(), routing=types.GoogleMapsRouting()), enable_widget=None)
types.ToolConfig(retrieval_config=types.RetrievalConfig(lat_lng=types.LatLng(latitude=..., longitude=...)))
# grounding_chunks[i].maps fields: place_id, title, uri, text, place_answer_sources, route
# route fields: distance_meters, duration, encoded_polyline

# Runner
Runner(app_name=..., agent=..., session_service=...)
runner.run_async(user_id=..., session_id=..., new_message=types.Content(...), state_delta={...})
# Event fields include: content, author, grounding_metadata, partial, turn_complete
```
- State injection in instructions: `{key}` (required) and `{key?}` (optional). Literal braces elsewhere in an instruction break the run.
- ADK's BigQuery helper toolset moved: `google.adk.integrations.bigquery` (old `google.adk.tools.bigquery` is deprecated). Not needed here: tools run `sql/` files with `google-cloud-bigquery`, and chat uses the managed MCP server.
- `adk deploy cloud_run` packages only the agent folder; this design uses `gcloud run deploy --source .` with our own Dockerfile instead, because the API needs `api/`, `contract/` and `sql/` too.
- Pin `google-adk==2.12.0` for the event (2.x ships breaking changes about every two weeks).

---

## 16. Testing, evaluation and acceptance

**Unit tests (`pytest`)**
- Every file in `contract/mocks/` validates against `contract/schemas.py`.
- Three-state conversion: TRUE→yes, FALSE→no, NULL→unrecorded; blank strings handled; capacity −1 → null.
- `verify_shelter` parser: each enum, malformed output → `unclear`; agreement matrix (all five outcomes).
- Attribution: `start_index None` → 0; source ids unique; "Google Maps" span markup exact.
- `assemble_brief` drops invented ids and never takes numbers from writer text.
- Gap rules on a small fixture (including "recorded no" vs "nobody assessed").

**Agent eval set (`adk eval`)**: the fourteen questions in section 17 with expected behaviour
(tool called, caveat present, refusal where required). Minimum pass: every "must say" phrase class
present; refusal case refuses; no sentence claims "no accessible shelters" when the field is blank.

**Acceptance (demo-ready) checklist**
- [ ] `/v1/health` live; `/counties` and `/map` from real SQL for FL; Lee County loads in under 5 s.
- [ ] "Check on Google Maps" returns a ShelterVerification with sources rendered under the text.
- [ ] A brief completes in under 2 minutes with all four segments, and **at least one Maps result changes the outcome** (a dropped/renamed shelter or changed verdict) visibly.
- [ ] Unknowns are counted ("63 of 74 shelters: nobody recorded wheelchair access").
- [ ] Chat answers the two rehearsed demo questions; the live-emergency question is refused.
- [ ] Audit panel shows recommended vs county vs state, including audit-only demographic rows.
- [ ] Both services on Cloud Run with min instances 1; the video recorded from the deployed UI; screenshots as backup.

---

## 17. Question coverage map

| The manager asks | Answered by | The answer must say |
|---|---|---|
| Which neighbourhoods would struggle most to self-evacuate? | `rank_vulnerable_tracts` | Which transport measure (ownership, not rides); `pct_lacktrpt` empty for FL |
| How many people depend on electricity for medical equipment? | `get_power_dependence` | County total only: how many, not which block; censored value meaning |
| Where would I send them, and how far is it? | `find_candidate_shelters` + `verify_shelter` routing | Straight-line vs drive time, labelled |
| Which of those shelters can take a wheelchair? | three-state flags + `verify_shelter` per building | "N I can speak to; M nobody has assessed" |
| Is that shelter still there? Open? What is it now? | `verify_shelter` | Live, with time; "open" = business hours |
| Which shelters are in the surge zone? | `in_surge_slosh_area`, `in_100_yr_floodplain`, `hazard_tracts` via nearest centroid | The method (nearest tract centroid by ST_DISTANCE) |
| What should I fix before the season? | `find_readiness_gaps` + writer | Each fix tied to affected ids |
| How long does it take to drive there? | Maps routing | Only when routing returned a value |
| Which assisted living facilities are in my county? | none (gap) | No national public list; state-regulated; listed in unknowns |
| Where are the mobile home parks? | ACS `mobile_homes` | Density per tract, not locations |
| Which specific people need help? | refusal | Aggregate only, by design |
| Is this shelter open right now, during the storm? | refusal | Not operational data; Maps barred for emergency response; call EOC/911 |
| How many people in this tract use a wheelchair? | CDC PLACES `pct_mobility` | Modeled prevalence, not a count |
| Can a wheelchair user actually get into this building? | assessment agent (tier 2) / unknowns | Somebody has to ask; this agent asks |

---

## 18. Build order, gates and cut list

Times are from kickoff (4.5 h total); shift if your start differs.

| Time | Gate / milestone | Lanes |
|---|---|---|
| 0:00–0:20 | Agree scenario, track, county | Everyone |
| 0:00–0:30 | Load FL tables; read WARN rows | Data |
| **0:40** | **Gate: contract v0 frozen** (`contract/schemas.py` + mocks on main) | Agent + Front end |
| 0:20–1:00 | Contract and **mock API deployed** (MOCK_MODE=1) | Agent (API owner) |
| 0:30–1:30 | `verify_shelter` + routing test (minute 10 of this block, not 200) | Agent |
| 0:30–1:30 | Ranking and candidate-shelter SQL handed over | Data |
| 0:20–2:30 | Map and brief UI on mocks | Front end |
| 1:30–2:30 | Gap rules, reachable distance; `brief_pipeline` end to end | Data, Agent |
| **2:30** | **Gate: live brief end to end** | All |
| 2:30–3:30 | Chat + attribution; switch UI routes to live; equity audit | Agent, Front end, Data |
| 0:00–3:30 | Deck, scenario, limitations | Story |
| **3:45** | **Gate: feature freeze** | All |
| 3:45–4:15 | Rehearse and record the video | Story |
| **4:30** | **Gate: submit to the portal** | Lead |

Implementation order for a single builder: contract (schemas + mocks) → API in mock mode → data
routes live → `verify_shelter` → other tools → brief_pipeline + assemble_brief → UI on mocks → UI
live → chat → audit → deploy → evals → tier 2.

**Cut list if behind:** cut chat before verification (`verify_shelter` is the Maps proof the judges
score). Then cut routing (fall back to straight-line km, labelled). Never cut the unknowns segment
or the three-state flags.

---

## 19. Phase 2 extensions (additive contract)

Build only if the core brief works end to end by about 3 hours in. All routes are additive.

### 19.1 Assessment agent (closes the 88% gap)
- `assessment_agent` interviews a shelter operator or volunteer: step-free entrance? unlocked outside school hours? lift working? accessible restroom? generator confirmed? **Can a wheelchair user get in, unaccompanied, today?** It asks follow-ups (a form gets "yes, accessible"; a conversation hears "the ramp is chained after hours").
- Tool `append_assessment(shelter_id, answers…)` appends to `shelter_assessments` (first-party data); before accepting, confirm the building via the stored `place_id` / one `verify_shelter` call.
- Routes: `POST /v1/assessments/chat` (same shape as chat) and `GET /v1/shelters/{id}` gains optional `latest_assessment`. UI: a phone-width operator chat; the grey pin turns green/red live on the manager's map.

### 19.2 Resource Allocator (OR-Tools CP-SAT)
- `allocate_limited_resource(total_units, sites[{site_id, need, priority, max_units?}], min_coverage_pct=0)`: split a scarce pool by priority with an optional equity floor (dropped and reported if infeasible).
- `assign_supply_to_demand(supplies[{supply_id, capacity}], demands[{demand_id, quantity, priority}], routes[{supply_id, demand_id, cost}], unmet_penalty=1000)`: transportation problem; minimises route cost (drive minutes when known, else straight-line km, labelled) while serving high-priority demand first; reports shipments, coverage, unused supply, unreachable demand.
- `place_new_sites(candidates, demands, budget, costs)`: max-coverage choice of k sites within budget (designate/retrofit public buildings found near gap tracts, add a generator, run an assessment). Output: coverage gained per dollar.
- Routes: `POST /v1/plans/allocate` → `{plan_id, assignments[], coverage{}, unmet[], assumptions{}}`; `POST /v1/plans/{plan_id}/approve` (human approval; `FunctionTool(..., require_confirmation=True)` on any publish tool).
- Reference implementations exist (tested): `tools/optimize.py` with the two functions above, pydantic inputs, CP-SAT, 10 s limit.

### 19.3 Rehearsal mode (tabletop, planning only)
- `POST /v1/rehearsals {county_fips, scenario: "ian_2022", shelters_down: [ids], hours_to_landfall}` → re-solve the assignment without the downed shelters (for example those `in_surge_slosh_area = yes` for the scenario) and show reassigned tracts and newly unmet demand. **No Maps grounding in this path.** UI: Ian's forecast track/cone from archived NHC GIS data, before/after toggle.

### 19.4 Storm Watch
- Ingests official products only: NHC Tropical Weather Outlook (7-day formation), advisories/forecast cone (GIS), NWS alerts (`api.weather.gov`), optionally NOAA CPC Global Tropics Hazards (weeks 2–3, low confidence). Public domain. Never produces its own forecast.
- `GET /v1/storm-watch` → `{phase: inventory|plan|brief|watch|activate|learn, active_systems[], days_to_possible_impact, sources[], checked_at}`. For the demo, replay archived Ian (AL092022) advisories.

### 19.5 Equity auditor as a standing check
Run the audit automatically on every brief and plan; show it in the UI; store in `decision_log`.

---

## 20. Path to production

- **Scheduling:** Cloud Scheduler → Cloud Run jobs: monthly re-verification sweep (weekly Jun–Nov) re-grounding stored `place_id`s; NHC polling every 6 h in season via Pub/Sub; annual data refresh when ACS/SVI/PLACES release.
- **Operational re-solve (Activate phase):** inputs are operator-reported shelter status/occupancy (`status_reports` table), county announcements and our data; **no Maps grounding** and no Google Maps Platform routing (high-risk-use restrictions); routing from a source licensed for emergency use; every change approved by a person in the EOC.
- **State and scale:** move jobs and sessions to Firestore or a database session service; lift `--max-instances 1`; IaC (Terraform) for services, service accounts, datasets.
- **Access:** internal only, via Gemini Enterprise (agent on Agent Runtime) or IAP in front of Cloud Run.
- **Observability and quality:** OpenTelemetry to Cloud Trace (`--otel_to_cloud`), decision log, `adk eval` in CI with the section-17 set, alerting on verification failure rates.
- **Learning loop:** after-action reports (which shelters opened, actual capacities, who arrived) feed `shelter_assessments` and capacity priors for next season.
- **Other hazards:** swap NRI prefixes (`HRCN_`/`CFLD_` → wildfire, ice storm); three of five tables are hazard-agnostic; emPOWER is a power-outage layer.

---

## 21. Known limitations to state in the demo

- About 88% of FL shelters have no recorded wheelchair access; Maps answers about 5 in 8 single-building questions; the rest need a site visit.
- FEMA's file is historical and explicitly not operational status.
- Straight-line distance unless routing returned a drive time.
- Car ownership is not the same as lacking a ride; `pct_lacktrpt` unpublished for FL.
- emPOWER is county-level; mobile homes are density, not park locations; PLACES is modeled.
- No national assisted-living roster.
- Recorded counts measure paperwork (California fills in the form), not readiness.
- Planning tool only; Maps grounding is barred for emergency response.

---

## 22. Open decisions and defaults

| Decision | Options | Default | Owner |
|---|---|---|---|
| Scenario sentence | fill the blanks | Section 1 sentence | Lead + story |
| Track | shelter readiness / getting people out / medically dependent | Shelter readiness with no-car tracts as the "who" | Lead |
| Focus county | from `01_county_ranking.sql` | Lee (12071), the Ian comparison | Data |
| Reachable without a car | 1.5 / 3 / 10 km | 3 km, stated with one sentence of why | Data |
| Shelters verified per brief | 6 / 12 / 20 | 12, in parallel (check Vertex AI quota) | Agent |
| Drive times | Maps grounding routing / Grounding Lite `compute_routes` (needs the Maps Demo Key) | Routing; fall back to straight-line km | Agent |
| Assessment agent | build / skip | Only if the core brief works end to end ~3 h in | Lead |

*Sources: the challenge README (haggman/A4I2026-challenge-4-evacuation), the team's "Challenge 4
Architecture & API Contract" (20 Oct 2026), BigQuery remote MCP server docs
(docs.cloud.google.com/bigquery/docs/use-bigquery-mcp), and the installed ADK 2.12.0 / google-genai 2.31.0 packages.*
