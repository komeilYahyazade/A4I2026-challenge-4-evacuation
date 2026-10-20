# System contract, v1.6 (FROZEN at v1.0 12:00 EDT; additive changes only)

**Owner:** agent lane. To change anything, send `CONTRACT-CHANGE:`; the owner bumps the version and
broadcasts `CONTRACT vX.Y published`. v1.0 froze at 12:00 EDT. From now on changes are additive only: new optional
fields or new payload types.

**Scenario:** our agent helps Lee County's (FIPS `12071`) emergency manager decide where verified,
reachable shelters and para-transit runs are needed for residents who can't drive, before the
September peak. Planning and tabletop rehearsal only. Grounding with Google Maps is used for planning
and never in rehearsal or response mode.

## 1. Components and how they talk

```
Browser ──HTTPS──▶ readiness-web (Cloud Run, PUBLIC)
                     static SPA + same-origin proxy  /agent/*  ──ID token──▶  readiness-agent
                     Google Maps JS API (browser key, referrer-restricted)        (Cloud Run, PRIVATE,
                                                                                  --no-allow-unauthenticated)
readiness-agent = agent/server.py: ADK get_fast_api_app (APP "agent") + /api/* + /healthz
   root_agent (gemini-3.6-flash): google_maps grounding (Places+Routing) + function tools + MCP toolsets
       │ parameterized SQL (agent/bq.py)               │ MCP, Streamable HTTP
       ▼                                               ▼
   BigQuery <project>.evacuation_readiness (5 tables, READ-ONLY)     MCP Toolbox (Cloud Run, private, ID token)
   BigQuery <project>.readiness_ops  (views + ops tables, contract/DATA.md)   Maps Grounding Lite compute_routes (API key)
```

- **Sessions** are in memory (`use_local_storage=False`), because session events can hold Google Maps content. The service runs with
  `--max-instances 1` so a session never moves between instances.
- **Access tiers (v1.5).** The caller's verified identity decides the tier. Cloud Run verifies the ID token, and the agent reads its
  `email` claim (confirmed: Cloud Run forwards Authorization to the container).
  - **PUBLIC** when: the email is `PUBLIC_CALLER_SA` (readiness-web's proxy, i.e. anyone on the public demo); or there is no token,
    an undecodable one, or one without an email claim (fails closed); or the request carries `x-readiness-public: 1`. That header
    can only add restriction.
  - **SIGNED-IN** is any other email, e.g. a person through the localhost dev proxy.
  - Public callers: no BigQuery writes (`place_ids_stored: 0`; `/api/plans` writes get 403), at most 2 Maps checks per
    `/api/verify` (the rest go to `skipped_over_limit`), and `/api/brief` runs with Maps off when the cap is below VERIFY_MAX.
  - Everyone: VERIFY_MAX=4 per check, and MAPS_BUDGET_PER_HOUR=120 grounded verify calls per rolling hour across the process.
  - `GET /api/health` adds `caller: "public"|"signed_in"`, and `caller_email` for signed-in callers only. `/api/brief` adds `verify_limit`.
  - `shelter_place_ids` writes are a MERGE on (shelter_id, place_id), so repeats refresh the row instead of adding one.
  - The deploy refuses `BIGQUERY_MCP_ENABLED=true`: its execute_sql plus the SA's WRITER on readiness_ops would put DML within reach of
    anonymous chat. The Toolbox is fine, because its tools are fixed read-only SQL.
- **Agent auth.** The agent stays private. The frontend server mints a Google ID token with
  audience = agent URL and proxies `/agent/<path>` → `<AGENT_URL>/<path>`. The frontend's runtime
  service account needs `roles/run.invoker` on `readiness-agent`; the agent lane grants it once you send
  the account. Local dev uses a Vite proxy to `http://localhost:8000` (`uv run --python .venv/bin/python -m agent.server`)
  or to `gcloud run services proxy readiness-agent --region us-central1 --port 8081`.
  Production access path: IAP or Gemini Enterprise for internal users.
- **CORS** is `*` on the agent, for local dev only. `OPTIONS` preflight works on `/api/*` and `/run_sse`.
- **REST errors** are JSON `{"error": "<code>", "detail": "<text>"}` with a 4xx/5xx status.

## 2. Protocol A: Frontend ↔ Agent

### A1. ADK chat (LLM), confirmed against google-adk 2.12.0 source

- `APP = "agent"`.
- `POST /apps/agent/users/{userId}/sessions` with body `{"state": {"county_fips": "12071"}}` (all fields
  optional; `sessionId` may be supplied). Returns a Session `{id, appName, userId, state, events, lastUpdateTime}`.
- `POST /run_sse` with body
  `{"appName":"agent","userId":"em-lee","sessionId":"<id>","newMessage":{"role":"user","parts":[{"text":"..."}]},"streaming":false}`.
  The request accepts camelCase or snake_case keys.
- **Response:** `text/event-stream`. Each event is `data: <json>\n\n`, where the json is an ADK Event dumped with
  `by_alias=True, exclude_none=True`, so it is **camelCase**. Fields used:
  `author`, `invocationId`, `content.parts[]`, and in each part `text` | `functionCall{id,name,args}` | `functionResponse{id,name,response}`;
  `groundingMetadata{groundingChunks[].maps{uri,title,placeId}, groundingSupports[]{segment{startIndex?,endIndex,text}, groundingChunkIndices[]}}`.
  A missing `startIndex` means 0.
- `streaming:false` gives whole events, no partials.
- **Errors** arrive in the stream as `data: {"error": "<Type>: <msg>", "error_details": {...}}`, after which the stream ends.
- **Compact tool payloads (v1.4):** in chat, `allocate_county` and `rehearse` return their payload_type with `compact: true`
  and `full: {method, path, body}`. In that view tracts, shelters and assignments are empty (in rehearsal, before and after are compact
  and the cone is dropped); gaps are the top 12 by severity, with `gaps_total`, `gap_counts` and `excluded_counts`. The map
  calls `full` (the /api route) for its layers. Why: a grounded request has a 32,768-token input cap, and a full Lee allocation is
  about 220 KB (rehearsal about 465 KB). Compact is 14 KB and 31 KB. The /api routes always return the full payload.
- **Tool results:** every function tool returns a dict, so `functionResponse.response` IS the payload, with
  `response.payload_type` at the top. Unknown payload types are ignored.
- **Deployed capture:** `contract/fixtures/run_sse.sample.txt` comes from readiness-agent revision 2 on Cloud Run:
  find_county, then county_lookup.v1, then rehearse, then rehearsal.v1, then text (50 s).
- **Real captures** (local server, real BigQuery, live Google Maps, gemini-3.6-flash):
  `contract/fixtures/run_sse.allocate.sample.txt` (allocate_county, then allocation.v1, then text) and
  `contract/fixtures/run_sse.verify.sample.txt` (verify_shelters on 345200 "INACTIVE Grace Church", which DROPs as NOT_FOUND,
  and 133777, which is KEEP). **Maps content is redacted**: titles, URIs, current_name and the model's text, keeping only
  place_id, per §4. Event keys seen: `actions, author, content, finishReason, id, invocationId, longRunningToolIds,
  modelVersion, nodeInfo, timestamp, usageMetadata`.
- **Maps title quirk:** an address-only result arrives with chunk title `" - Google Maps"`. The UI strips a trailing
  `" - Google Maps"` from link text, falls back to the shelter name, and keeps the separate `Google Maps` label unchanged.

### A2. Deterministic REST (no LLM in the loop; the demo runs on these)

| Route | Body / query | Returns | Maps calls |
|---|---|---|---|
| `GET /api/health` (`/healthz` works locally only; Cloud Run reserves it) | – | `{"ok":true,"contract_version":"1.1","app":"agent"}` | no |
| `GET /api/map?county_fips=12071` | – | `county_map.v1`: every tract and every county shelter, for the base layers | no |
| `POST /api/brief` | `{"county_fips":"12071","top_tracts":5,"verify_top_n":0}` | `readiness_brief.v1` (komeil-d2's builder). `verify_top_n:0` makes no Maps calls and returns fast | 0 to 8 |
| `POST /api/verify` | `{"shelter_ids":["..."],"origin_geo_id":"12071..."}` | `shelter_verification.v1` (live, about 5–10 s) | yes, one per building |
| `POST /api/allocate` | `{"county_fips":"12071","shelters_down":[],"max_km":15,"shelter_seeking_share":null}` | `allocation.v1` | **never** |
| `POST /api/rehearse` | `{"county_fips":"12071","scenario":"ian_2022","advisory":null,"shelters_down":null}` | `rehearsal.v1` | **never** |
| `GET /api/storm/ian_2022` | – | `storm_track.v1` | no |
| `POST /api/plans` | `{"kind":"allocation"\|"rehearsal","county_fips":"12071","created_by":"<person>","shelters_down":null,"advisory":null}` | `{plan_id, county_fips, version, mode, status:"DRAFT"}`. The server computes the plan; clients never send plan JSON | never |
| `POST /api/plans/{plan_id}/approve` | `{"approved_by":"<person>"}` (required) | `{plan_id, status:"APPROVED", approved_by, approved_at}`; the previous APPROVED plan becomes SUPERSEDED | – |
| `GET /api/plans?county_fips=12071` | – | `{county_fips, plans[{plan_id, version, mode, status, created_by, approved_by, approved_at, payload_type, totals}]}` | – |

A person approves every plan. Approval is REST-only and is not an agent tool, so no model output is published unreviewed.
Every draft and approval is appended to `readiness_ops.decision_log`, and `plan_json` is passed through `plans.strip_maps()`.

The UI loads `/api/map`, `/api/allocate` and `/api/brief` (with `verify_top_n:0`) in parallel, then
calls `/api/verify` for the shelters it checks live on camera (demo beat 1).


### A3. Live voice (Gemini Live), v1.6, confirmed on readiness-agent-00011-5wz

The voice app is ADK app `voice` (`voice/agent.py`, komeil-d2). It runs `gemini-3.8-live` in us-central1 (global gives close 1008) with the
same FUNCTION_TOOLS as the text agent, no google_maps built-in, and no MCP. `verify_shelters` still grounds at `global` itself.

- **Availability:** `GET /api/health` returns `voice` (true when the voice app is in the image and `VOICE_ENABLED=1`) and
  `voice_public` (`VOICE_PUBLIC=1`; the default is 0). The UI shows the mic only when voice is true, and on the public site only when voice_public is also true.
- **Session:** `POST /apps/voice/users/{userId}/sessions` with `{"state": {"county_fips": "12071"}}`. These are separate from chat (app `agent`) sessions.
- **Socket:** `wss://<agent>/run_live?app_name=voice&user_id=<u>&session_id=<id>&modalities=AUDIO`
  - `modalities` is a repeated query parameter, with default `["AUDIO"]`.
  - Also accepted: `proactive_audio`, `enable_affective_dialog`, `enable_session_resumption`, `explicit_vad_signal`. Never send `save_live_blob=true`.
- **Client to server:** one JSON text frame per `LiveRequest`.
  - **Top-level keys are snake_case only:** `blob`, `content`, `close`, `activity_start`, `activity_end`,
    `audio_stream_end`, `state_delta`. A camelCase or unknown top-level key is silently dropped (tested: `{"audioStreamEnd":true}` became an empty request).
    Fields nested inside accept either casing; use `mime_type`.
  - Audio: `{"blob": {"mime_type": "audio/pcm;rate=16000", "data": "<base64 PCM16 LE mono 16 kHz>"}}`, sent about every 100 ms. ADK runs VAD by default.
  - Typed text: `{"content": {"role": "user", "parts": [{"text": "..."}]}}`
  - End: `{"close": true}`
- **Server to client:** text frames, each an ADK Event in camelCase (exclude_none).
  - `content.parts[].inlineData {mimeType: "audio/pcm;rate=24000", data: <base64 PCM16 LE mono 24 kHz>}`
  - `outputTranscription {text}`: incremental on `partial: true` events, then final on `partial: false`. `inputTranscription {text}` arrives when the user speaks.
  - `content.parts[].functionCall {name, args}` and `functionResponse {name, response}`. The response is the usual payload with `payload_type`,
    and allocate and rehearse are compact (`compact: true`, fetch `full`), so use the same dispatch as chat.
  - `turnComplete: true`. One arrives after the tool round and another after the spoken answer; wait for audio before treating the turn as done.
    `interrupted: true` means barge-in: stop playback and flush.
  - Other keys: `author: "readiness_voice"`, `liveSessionId`, `usageMetadata`, and `groundingMetadata` (render it only if it has chunks).
- **Close codes:**
  - `1008 "Voice is turned off on the public demo"`: the caller is public by the same identity rule as HTTP, and VOICE_PUBLIC is not 1.
  - `1008 "Voice is turned off"`: the VOICE_ENABLED=0 kill switch.
  - `1002 "Session not found"`
  - `1011 <reason up to 123 bytes>` for internal errors.
  - The public proxy adds its own 4008 at 3 minutes.
- **Measured:** "how many residents without a car can we not place?" took 20 s end to end: allocate_county (compact), then 96 audio frames,
  then the spoken answer "5,589 ..." with both caveats.
- **Capture:** `contract/fixtures/run_live.sample.jsonl`. Line 1 is a header with the client messages; 3 of 96 audio frames are kept, audio data is truncated, and Maps content is redacted.

## 3. Payloads

Common fields: `payload_type` (`"<kind>.v1"`), `schema_version` (int `1`). Many payloads also carry `county_fips` and `generated_at`.

**Wire conventions**
- Three-state flags are `"YES" | "NO" | "UNRECORDED"`. UNRECORDED is never NO.
- Labels are from `agent/evidence.py`: `RECORDED | UNRECORDED | VERIFIED LIVE | MODELED | COUNTY-LEVEL | APPROXIMATE`.
  APPROXIMATE means taken from a federal record but placed by our own method (e.g. a shelter given its nearest tract centroid's NRI flood exposure).
- `distance_basis` is the long string from `agent/reachability.py`. Match it on the prefix `ROUTED` / `MODEL-REPORTED` / `STRAIGHT-LINE`.
- Thresholds come only from `agent/reachability.py`: walk 1.5 km, para-transit 30 min, 15 km straight-line proxy, 40 km search.
- **Coordinates.** Payloads owned by the agent lane always use `lat`/`lng`. Raw rows owned by komeil-d2 use
  `lat`/`lon` for tracts (tract_ranking) and `latitude`/`longitude` for shelters (tract_destinations, shelter_lookup).
  Normalize these on the client. Anything without coordinates is joined by `geo_id` / `shelter_id` against `county_map.v1`.

### Emitted by komeil-d2 (`agent/brief.py`, `agent/tools.py`, `agent/verify.py`)
`county_lookup.v1`, `county_overview.v1`, `tract_ranking.v1`, `tract_destinations.v1`, `shelter_lookup.v1`,
`shelter_verification.v1`, `care_facility_gaps.v1`, `readiness_brief.v1`, `equity_audit.v1`.

**`readiness_brief.v1`**: the reference example is `docs/samples/readiness_brief.v1.json`, and `contract/fixtures/readiness_brief.lee.json` is a Lee version of it.
Keys: `county{fips,name}`, `generated_at`, `assumptions`, `county_shelters`, `power_dependence`,
`who_cannot_leave[] {rank, geo_id, priority_score, people{}, reach{}, hazard{}, reasons[{text,label,source}], mobility{...reachability.assess}}`,
`where_they_would_go[] {for_tract, shelter_id, name, address, straight_line_km, record{label,capacity,capacity_implausible,usage,wheelchair,generator,surge,is_medical}, live{label,verified_at,sources[{title,uri,place_id}],exists,current_name,place_type,closed,wheelchair_entrance,drive_minutes,road_km,drive_basis,notes,status}|null, accessibility{label,summary}|null, verdict KEEP|DROP|KEEP_UNVERIFIED|NOT_VERIFIED_THIS_RUN, why}`,
`fix_before_season[] {type, subject, evidence, action, label}`,
`could_not_tell_you[] {item, spoken_to?, unassessed?, note?, label}`, `grounding_ledger`,
`maps_sources[] {title, uri, place_id, supports_shelter_ids[]}`, `evidence_labels`.

**v1.0 additions, all landed in komeil-d2's code:**
- `county.lat`, `county.lng`
- `who_cannot_leave[].lat`, `.lng`
- `where_they_would_go[].lat`, `.lng`, `.flags[]` (empty when not verified)
- `where_they_would_go[].record` gains `capacity` (null when unrecorded), `capacity_status` (`RECORDED|UNRECORDED`) and `capacity_as_filed`
- `fix_before_season[].severity` (`HIGH|MEDIUM|LOW`, the list is sorted by it) and `.ref {kind:"tract"|"shelter"|"facility"|"county", id, lat, lng}`
- `county_shelters.pre_landfall_capacity_recorded` and `.pre_landfall_capacity_unrecorded`
- `is_pre_landfall` is null-safe: NULL usage gives false, and those shelters are counted in `could_not_tell_you` under "Shelter usage code"
- `shelter_verification.v1` results gain `name`, `lat` and `lng`, and their fixes carry `severity` and `ref` with kind shelter
- POST `/api/brief` `verify_top_n:0` maps to `verify_with_maps=False`

**v1.1 additions (komeil-d2):**
- fix types `PRE_LANDFALL_FLOOD_EXPOSED_SITE` (APPROXIMATE, MEDIUM, ref shelter; coastal_flood_exposed_share ≥ 0.5) and `NAME_SAYS_INACTIVE` (RECORDED, HIGH)
- `where_they_would_go[].record.site_hazard {label, coastal_flood_exposed_share, coastal_flood_annual_frequency, hurricane_annual_frequency, nearest_geo_id, km_to_centroid, method, note} | null`
- `where_they_would_go[].record.name_says_inactive`
- `county_shelters.pre_landfall_flood_exposed_approx`, `.pre_landfall_name_says_inactive`
- a "Site flood exposure" item in `could_not_tell_you`
- Shelters whose FEMA name says INACTIVE/CLOSED/DEMOLISHED/DO NOT USE are never destinations, in the brief or in the allocation.

**`shelter_verification.v1`**: `{verified_at, results[{shelter_id, shelter_name, address, record, live, accessibility, verdict, why, flags[], fixes[], stored_pointer}], not_in_registry[], skipped_over_limit[], grounding_ledger, session_grounding_ledger, display_rule}`.
Flags: `CLOSED | NOT_FOUND | TEMPORARILY_CLOSED | NAME_DIFFERS | SUCCESSOR_AT_ADDRESS | NAME_SAYS_INACTIVE | ACCESSIBILITY_CONFLICT | CAPACITY_IMPLAUSIBLE | PRE_LANDFALL_IN_SURGE_ZONE`.
A shelter whose FEMA name says inactive never gets KEEP. If Google Maps shows a different organisation at the address, the verdict is DROP with flags NAME_DIFFERS, SUCCESSOR_AT_ADDRESS and NAME_SAYS_INACTIVE, plus a HIGH `SUCCESSOR_AT_ADDRESS` fix (VERIFIED LIVE, ref shelter). If it shows the same organisation, the verdict is KEEP_UNVERIFIED with a HIGH NAME_SAYS_INACTIVE fix.
`POST /api/verify` also returns `place_ids_stored` (int): the place_ids written to readiness_ops.shelter_place_ids, which is the only Maps field kept.

### Emitted by the agent lane (`agent/server.py`, `agent/allocate.py`, `agent/rehearse.py`)

**`county_map.v1`** (base layers for the "88% of pins grey" view)
```
county {fips, name, lat, lng}
tracts[]   {geo_id, lat, lng, total_pop, households, households_no_vehicle, share_no_vehicle, est_persons_no_vehicle}
shelters[] {shelter_id, name, address, lat, lng, usage, is_pre_landfall, capacity (null if unrecorded),
            capacity_status RECORDED|UNRECORDED, capacity_implausible, wheelchair, generator, surge, is_medical}
            // every county shelter, three-state strings
```

**`allocation.v1`** (OR-Tools; never calls Maps)
```
county_fips, generated_at, solver:"ortools.SimpleMinCostFlow", status:"OPTIMAL"|"NO_DEMAND"|"SOLVER_ERROR"
params {max_km, shelter_seeking_share, shelters_down[], excluded_shelters[{shelter_id,name,reason}], exclude_surge:false,
        capacity_rule, demand_rule, cost_rule, distance_basis}
   excluded reason ∈ DOWN | NOT_PRE_LANDFALL | NAME_SAYS_INACTIVE | CAPACITY_UNRECORDED | CAPACITY_IMPLAUSIBLE | IN_SURGE_ZONE
totals {demand_persons, assigned_persons, unassigned_persons, usable_capacity, shelters_used}
tracts[]   {geo_id, lat, lng, households_no_vehicle, persons_per_household, demand_persons, assigned_persons, unassigned_persons}
shelters[] {shelter_id, name, lat, lng, county_fips, capacity (null if unrecorded), capacity_status, capacity_implausible,
            is_pre_landfall, name_says_inactive, assigned_persons, utilization (0..1 | null), wheelchair, generator, surge, is_medical,
            excluded_reason (null | one of the reasons above)}
assignments[] {geo_id, shelter_id, persons, straight_line_km, drive_minutes:null, distance_basis,
               mode:"WALK"|"PARATRANSIT", from{lat,lng}, to{lat,lng}}
unassigned[] {geo_id, persons, reason:"NO_SHELTER_WITHIN_MAX_KM"|"CAPACITY_EXHAUSTED", lat, lng}
gaps[] {type, subject, evidence, action, label, severity, ref{kind,id,lat,lng}, persons_affected?, households_affected?}
   (the same shape as the brief's fix_before_season[], so one renderer draws both)
   type ∈ UNASSIGNED_DEMAND (HIGH) | ASSIGNED_SHELTER_IN_SURGE_ZONE (HIGH) |
          ASSESS_ASSIGNED_SHELTER (MEDIUM; wheelchair access not recorded YES, ranked by persons the plan sends there) |
          CAPACITY_UNRECORDED_EXCLUDED (LOW) | CAPACITY_IMPLAUSIBLE_EXCLUDED (LOW)
could_not_tell_you[] (same shape as the brief's)
site_options[] {type:"DESIGNATE_SITE_NEAR", subject, evidence, action, label, severity:"HIGH", ref{kind:"tract",id,lat,lng},
               persons_covered, seats_needed, tracts_covered[]}   (v1.2, Allocator part 2: greedy; each option counts only
               people not covered by the ones before it. Real Lee: one site near 12071001502 seats 5,195 of 5,589 unplaced)
maps_used: false
gaps[0] is a county headline when anything is unplaced: UNASSIGNED_DEMAND, HIGH, ref{kind:"county", id, lat:null, lng:null}
```
**Design notes**
- Allocation cost is straight-line km only. Recorded accessibility is deliberately *not* rewarded, because that would steer
  the plan toward the shelters with good paperwork. Unassessed access becomes a gap ("assess these shelters") instead.
- The `gaps[]` shape is a superset of `fix_before_season[]`.

**`rehearsal.v1`** (tabletop; never calls Maps)
```
scenario {id:"ian_2022", name, storm_id:"AL092022", advisory|null (default "19", 22 h before landfall; at advisory 17
          or earlier Fort Myers was outside the cone), issued_at|null, landfall_at:"2022-09-28T19:05:00Z", landfall{lat,lng},
          cone: GeoJSON Geometry|null, source}
  Selection rule: v_ian_shelters_down = in the cone at the advisory AND (FEMA surge YES OR NRI coastal-flood exposed share ≥ 0.5
  at the nearest tract centroid; APPROXIMATE, describes the neighbourhood, not the building). Fallback: FEMA surge YES.
  The 2 shelters taken offline by default are the highest-ranked with in_plan:true.
shelters_down[] {shelter_id, name, lat, lng, reason:"IN_SURGE_ZONE"|"IN_CONE_AND_SURGE"|"COASTAL_FLOOD_TRACT"|"REPORTED_CLOSED"|"MANUAL",
                 rule (which rule picked it), in_plan (bool: had recorded capacity, so losing it moves people)}
at_risk_not_in_plan[] (shelters_down rows with in_plan:false, plus a note). These are at-risk shelters the plan never counted on.
   Real Lee example: FEMA's only two surge-YES shelters both have unrecorded capacity.
before: allocation.v1 (full)    after: allocation.v1 (full)
diff {reassigned[{geo_id, from_shelter_id, to_shelter_id, persons, from{lat,lng}, to_old{lat,lng}, to_new{lat,lng}}],
      newly_unassigned[{geo_id, persons, lat, lng}], capacity_lost, unassigned_before, unassigned_after}
maps_used: false
note: "Tabletop rehearsal: no Google Maps grounding is used in response-mode planning."
```

**`storm_track.v1`**
```
storm_id:"AL092022", name:"Ian", landfall_at:"2022-09-28T19:05:00Z", landfall{lat,lng}, source:"NOAA NHC GIS archive (public domain)"
advisories[] {advisory, issued_at, hours_before_landfall, cone: GeoJSON Geometry (Polygon|MultiPolygon, RFC 7946 [lng,lat]) | null,
              track[{lat, lng, valid_at, wind_kt, tau_h?, category?}]}
best_track[] {lat, lng, valid_at, wind_kt}   (what actually happened; NHC best track)
```

## 4. Protocol B: Agent ↔ BigQuery
- Custom tools use parameterized SQL via `agent/bq.py`. New agent-lane code reads `readiness_ops` views
  (`v_tract_need`, `v_tract_shelter_candidates`, `v_ian_shelters_down`, `storm_advisories`) and
  komeil-d2's `agent/data.py` fetchers. Nothing new is written against `evacuation_readiness`.
- MCP:
  - MCP Toolbox at `<TOOLBOX_URL>/mcp/evacuation_readiness`, using an ID token. Owned by komeil-d2 and pending approval.
  - Maps Grounding Lite `compute_routes`, with `MAPS_GROUNDING_LITE_API_KEY` from a server-side key restricted to mapstools.googleapis.com.
  - The managed BigQuery MCP (`BIGQUERY_MCP_ENABLED`) is wired in but untested.
  - The demo runs with `REQUIRE_MCP=true`.
- **Maps compliance:**
  - Only `place_id` (+ verified_at, verdict) may be persisted, in `readiness_ops.shelter_place_ids`.
  - Plans written to `readiness_ops.plans` have `live.sources`, `maps_sources`, `current_name`, `notes` and supports stripped.
  - Nothing Maps-derived is cached across requests.

## 5. Protocol C: Frontend ↔ Google Maps
- Maps JavaScript API, with a browser key restricted to the `readiness-web` URL and localhost. The key is never committed.
- `<MapsAttribution sources>` renders **immediately after** the claim it supports. "Google Maps" sits in
  `<span translate="no">` and is never restyled or wrapped (`white-space: nowrap`).
- Brief rows: `live.sources` render right after that row's `why` and `accessibility.summary`.
- Chat:
  - An event's `groundingMetadata` renders under that message.
  - Text that follows a verify or brief `functionResponse` in the same invocation gets the union of that invocation's `live.sources`.

## 6. Machine-checkable contract
- `agent/contract.py`: pydantic models for every payload (`PAYLOAD_MODELS`, `validate()`).
- `contract/schemas/*.schema.json`: JSON Schema exported by `.venv/bin/python -m agent.contract`.
- `contract/fixtures/*.sample.json`, `*.lee.json`: synthetic, generated through the real solver by
  `contract/fixtures/make_fixtures.py`, and marked `"_fixture": true`.
- `contract/fixtures/*.live.json`: REAL Lee County output (allocation, rehearsal at advisory 19, county_map, storm_track
  with NHC cones and best track). These contain no Google Maps content.
- Every JSON fixture must validate (`tests/test_contract.py`).

## Changelog
- **v1.6 (14:45):** A3 live voice (/run_live, app voice), the voice gate, and voice and voice_public in /api/health. Revision readiness-agent-00011-5wz.
- **v1.5 (12:45):** identity-based access tiers, public limits, MERGE for place_ids, health caller fields. Revision readiness-agent-00006-p92.
- **v1.4 (12:20):** compact chat payloads for allocate_county and rehearse (compact, full, gaps_total, gap_counts, excluded_counts, reassigned_total).
- **v1.3 (12:08):** plans routes (draft, approve, list) and decision_log; place_ids_stored; SUCCESSOR_AT_ADDRESS and NAME_SAYS_INACTIVE flags; in-memory sessions with max 1 instance. Serving on revision readiness-agent-00004-cmz.
- **v1.2 (12:00):** allocation.v1 site_options[] (Allocator part 2) and a county headline gap; /api/health; the deployed SSE capture; the verify capture redacted.
- **v1.1 (12:05):** APPROXIMATE label; NAME_SAYS_INACTIVE exclusion in brief and allocation; site_hazard and the flood-exposed-site fix.
- **v1.0 (12:00):** frozen. komeil-d2's brief and verification coordinates, flags, severity, ref and capacity_status landed;
  allocation gaps adopted the brief fix shape; rehearsal gained rule, in_plan and at_risk_not_in_plan, with default advisory 19.
- **v0.1 (11:40):** adopted komeil-d2's readiness_brief.v1 key names; added county_map.v1; the agent stays private behind a same-origin proxy.
- **v0 (11:28):** seed from main.
