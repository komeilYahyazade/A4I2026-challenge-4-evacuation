# The readiness agent

**Our agent helps a county emergency manager in Florida decide which shelters to verify, and which
neighborhoods need a pickup plan, before the season starts.** Demo county: Lee County (FIPS 12071),
where Hurricane Ian made landfall in 2022. The code works for any county in the loaded state.

## How it fits together

| Piece | What it does |
|---|---|
| `agent/agent.py` | One ADK agent on `gemini-3.6-flash`: Google Maps grounding, nine function tools, managed MCP toolsets |
| `agent/brief.py` | `build_readiness_brief`: the four-segment brief, with live verification of up to 8 shelters |
| `agent/verify.py` | `verify_shelters`: one grounded Google Maps call per named building, then a verdict |
| `agent/tools.py` | Conversational tools: county lookup, overview, tract ranking, destinations, nursing homes, equity audit |
| `agent/grounding.py` | Maps tool with **places and routing** enabled, county location bias, prompt, parsing, attribution |
| `agent/queries.py`, `data.py`, `bq.py` | Parameterized BigQuery SQL and the fetchers that run it |
| `agent/reachability.py` | What "reachable" means without a car, with the reasoning |
| `agent/evidence.py` | The six evidence labels, fix severity, and the YES / NO / UNRECORDED classifier |
| `toolbox/tools.yaml` | MCP Toolbox for Databases config: read-only registry lookups served over MCP |

Every tool response carries `payload_type` (for example `readiness_brief.v1`,
`shelter_verification.v1`) so a front end can dispatch on it. Tracts, shelters and the county carry
`lat` and `lng`. Every fix carries a `severity` of HIGH, MEDIUM or LOW and a `ref` naming the
tract, shelter, facility or county it is about, with coordinates. A sample brief is in
`docs/samples/readiness_brief.v1.json`; its shape is real and its contents are synthetic.
`contract/README.md` is the front end's contract, written from that sample.

## Run it

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt
cp .env.example agent/.env          # set GOOGLE_CLOUD_PROJECT, and one MCP option
gcloud auth application-default login
adk web .                           # pick "agent"
```

Tests run without cloud access: `uv pip install pytest sqlglot && python -m pytest`.

## Managed MCP

The challenge requires consuming at least one managed MCP server. Three are supported; set
`REQUIRE_MCP=true` for the demo so a missing one fails at startup instead of silently.

| Option | Setting | Notes |
|---|---|---|
| MCP Toolbox for Databases | `MCP_TOOLBOX_URL` | `bash scripts/deploy_toolbox.sh`. Private Cloud Run, one warm instance, BigQuery read-only |
| Maps Grounding Lite | `MAPS_GROUNDING_LITE_API_KEY` | Only `compute_routes`, as the drive-time fallback. A Maps Demo Key works |
| Managed BigQuery MCP | `BIGQUERY_MCP_ENABLED=true` | No deployment; uses ADC. Not yet tested against a live project |

## Deploy

```bash
GOOGLE_CLOUD_PROJECT=<p> bash scripts/deploy_toolbox.sh
GOOGLE_CLOUD_PROJECT=<p> MCP_TOOLBOX_URL=<url> bash scripts/deploy_agent.sh
adk deploy agent_engine --project=<p> --region=us-central1 --display_name="Evacuation Readiness" agent
```

`deploy_agent.sh` uses `gcloud run deploy --source .` so `GOOGLE_CLOUD_LOCATION` stays `global`,
where Maps grounding is verified.

## Decisions, and why

**Unrecorded is its own category.** Every flag is YES, NO or UNRECORDED from the SQL onward. Answers
read "11 I can speak to; 64 nobody has assessed", never "no accessible shelters".

**Reachable means a mobility class.** On foot within 1.5 km. A para-transit run within 30 minutes
of routed drive time, or 15 km straight-line when no route exists. By car within 40 km. A tract
needs a pickup plan at 50 households without a vehicle or a 10% share. Reasons are in
`agent/reachability.py`.

**Only EVAC shelters are destinations.** POST shelters are a different job. Shelters with no usage
code are counted as unrecorded, not as destinations.

**Two rankings, both by how many people, not what share.**
- *Who cannot leave*: tracts with the most households without a vehicle. This is the challenge's
  scenario: people who cannot drive themselves out.
- *Who must leave and may need help*: state z-scores of the counts of mobile homes, residents 65+
  and residents with a disability. Mobile homes must evacuate in any hurricane.

Inputs are physical and demographic only. No accessibility field, so the plan does not follow good
paperwork. No NRI composite, which already multiplies in social vulnerability. No race.

**The equity audit changed the ranking.** The first version summed four state z-scores of *shares*.
Run on real Lee County data, its top 15 tracts held only 14% of the county's 14,553 households
without a vehicle, because small tracts with high mobile-home and 65+ shares dominated it; three of
its top four had 17 to 19 car-less households. Its tracts were also 90% white non-Hispanic, against
66% countywide. Reporting bias was not the cause: wheelchair access is recorded at 9% to 11% of the
shelters near every list, against 14% statewide. Ranking by count raises coverage of car-less
households to 34%, and the count-based "may need help" list reaches more of the county's mobile
homes and older residents than the old blend did. The old ranking stays reproducible as
`rank_tracts(ranking="composite_v0")` for the before-and-after.

| Top 15 tracts in Lee | Car-less households reached | Mobile homes reached | Residents 65+ reached |
|---|---:|---:|---:|
| Original composite of shares | 14% | 42% | 10% |
| Who cannot leave: by car-less count | 34% | 9% | 7% |
| Must leave, may need help: by counts | 10% | 46% | 12% |

Who the top 15 tracts hold, population-weighted from ACS 2020, race used only for this audit:

| Top 15 tracts in Lee | Black | Hispanic | White non-Hispanic | In poverty |
|---|---:|---:|---:|---:|
| Original composite of shares | 2.5% | 5.0% | 90.3% | 15.3% |
| Who cannot leave | 14.9% | 31.9% | 50.1% | 20.3% |
| Must leave, may need help | 3.1% | 16.2% | 78.7% | 12.0% |
| Lee County | 7.9% | 21.9% | 66.3% | 11.8% |

The "may need help" list still leans white non-Hispanic, because Lee's mobile-home parks and
retirement communities do. We report that rather than tune it away, because the need is physical.
The car-less list reaches tracts where Black and Hispanic residents are a larger share than
countywide, which the original composite had missed.

**Race is an audit, not an input.** `audit_equity` compares the recommended tracts with the county
and state, population-weighted from the public ACS. It reports coverage of the county's need, and
checks whether shelters near the recommended tracts have their wheelchair field filled in less often
than shelters statewide.

**What Google Maps changes.**

| Maps says | We do |
|---|---|
| Nothing at the address, or permanently closed | Drop it from destinations and put it on the fix list |
| Another name | Keep it and flag the record. Abbreviations like MS and HS are expanded first |
| Another organisation, where FEMA's own name says inactive | Drop it. Ask the new occupant whether it agreed to be a shelter. Never "rename the record", which would hand the designation over silently |
| The same organisation, where FEMA's own name says inactive | Not counted as a destination. Confirm the agreement is still in force |
| Wheelchair entrance disagrees with FEMA | Keep it and send someone to look |
| Wheelchair entrance where FEMA is blank | Report it as live evidence about an entrance, not an ADA certification |
| Nothing | It stays UNRECORDED. Unknown never becomes yes or no |
| No Maps source at all | Not evidence, whatever the text says |

Drive time from a Maps route chunk is labelled ROUTED. A minute count with no route chunk is
labelled MODEL-REPORTED. Neither reflects evacuation traffic.

**Blank capacity is unrecorded, not zero.** A blank or zero capacity on a pre-landfall shelter is
UNRECORDED; capacity totals count recorded values only. A zero on a post-impact shelter is real.
In Lee County, 72 of 84 pre-landfall shelters have a recorded capacity.

**The surge flag is mostly blank.** FEMA's surge-zone flag is unrecorded for 81 of 84 pre-landfall
shelters in Lee County. We say "recorded in the surge zone" for a yes and never treat a missing
flag as safe.

**Flood exposure of the site is an approximation, and labelled so.** Because the surge flag is
mostly blank, the brief also reads the data lane's `readiness_ops.v_shelter_site_hazard` view: FEMA
NRI coastal-flood exposure of each shelter's nearest 2020 tract centroid. A pre-landfall shelter is
flagged when at least half of that tract's residents are exposed, which is the same threshold as
the Hurricane Ian rehearsal. "Any exposure" would flag 71 of Lee's 84. These claims carry a sixth
label, APPROXIMATE: the exposure is federal, the shelter-to-tract match is ours, and it describes
the neighbourhood, not the building. If the view is missing, the brief says so.

**A record that says it is inactive is believed.** 148 Florida shelters have INACTIVE, CLOSED,
DEMOLISHED or DO NOT USE in their FEMA name; 24 are still designated pre-landfall. In Lee County,
"INACTIVE Grace Church" is listed EVAC with 100 spaces. These are never chosen as a destination,
are excluded from capacity totals, go on the fix list as HIGH, and are the first shelters sent to
Google Maps for a live check.

**Implausible capacities are flagged, not fixed.** Records above 100,000 are excluded from totals and
listed; the federal number is left as filed.

**We store only Google Maps place IDs.** Everything else is re-grounded at runtime.

## What it cannot tell you

Wheelchair access for most shelters, because nobody recorded it. Which block power-dependent
residents live on, because emPOWER is county-level. Drive times under evacuation traffic. Assisted
living facilities, which have no national roster. Mobile home park locations. Lack of reliable
transportation in Florida, which CDC PLACES does not publish there; we quote vehicle ownership,
and owning no car is not the same as having no ride.

## Not yet verified against live services

Every query, including the site-hazard view, passes a BigQuery dry run against the loaded Florida
tables, and every column it uses exists with the expected type. No query has returned live rows through the agent yet, because
local Application Default Credentials were not working. Grounded calls, routing, the Toolbox server and the managed
BigQuery MCP endpoint have not been exercised end to end. Run the seven planner questions from the
challenge README once the data is loaded and credentials work.
