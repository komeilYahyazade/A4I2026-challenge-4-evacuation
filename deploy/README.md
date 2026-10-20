# deploy/

`readiness-agent` is `agent/server.py` on Cloud Run: the ADK API (`/apps/...`, `/run_sse`, `/dev-ui`) plus the
deterministic `/api/*` routes in `contract/README.md`. It is private, and the frontend reaches it through its
same-origin proxy.

| Command | What it does |
|---|---|
| `bash deploy/deploy_agent.sh` | Idempotent. Enables the APIs, creates the `readiness-agent@` service account and its roles (with retries; it fails loudly), gives WRITER on `readiness_ops`, puts the Grounding Lite key in Secret Manager (`maps-grounding-lite-key`, created once and restricted to mapstools.googleapis.com), and runs `gcloud run deploy --source .` with one instance |
| `FRONTEND_SA=<sa> bash deploy/deploy_agent.sh` | The same, and also grants that service account `roles/run.invoker` |
| `bash deploy/enable_toolbox.sh <TOOLBOX_URL>` | Points the running agent at the MCP Toolbox. This is a new revision with no rebuild |

Public-demo limits, all set by the deploy script:
- `MAPS_BUDGET_PER_HOUR=120` and `VERIFY_MAX=4`.
- `BIGQUERY_MCP_ENABLED=false`; the script refuses to deploy with it set to true.
- Access tiers come from the caller's ID token email (see contract/README.md "Access tiers"). `PUBLIC_CALLER_SA` overrides the public proxy's service account.

Why it is built this way:
- We use `gcloud`, not `adk deploy cloud_run`, so `GOOGLE_CLOUD_LOCATION` stays `global`, which is where Maps grounding works.
- There is one instance with in-memory sessions, because ADK session events can carry Google Maps content and only `place_id` may be stored.
- Cloud Run reserves `/healthz`, so health is `/api/health`.

## Smoke test

```bash
U=$(gcloud run services describe readiness-agent --region us-central1 --format 'value(status.url)')
H="Authorization: Bearer $(gcloud auth print-identity-token)"
curl -s -H "$H" $U/api/health
curl -s -H "$H" -X POST -H 'content-type: application/json' -d '{}' $U/api/allocate | head -c 300
curl -s -H "$H" -X POST -H 'content-type: application/json' -d '{"shelter_ids":["345200"]}' $U/api/verify | head -c 300
# Dev UI, through an authenticated local proxy:
gcloud run services proxy readiness-agent --region us-central1 --port 8081   # then http://localhost:8081/dev-ui
```

Expected Lee County numbers (revision 4):
- **allocate:** 36,830 demand, 31,241 placed, 5,589 unplaced.
- **rehearse** (advisory 19): unplaced goes from 5,589 to 7,284.
- **verify 345200:** DROP, with flags `NAME_DIFFERS, SUCCESSOR_AT_ADDRESS, NAME_SAYS_INACTIVE`.

## Evals

`evals/readiness.evalset.json` has 11 cases: the README's seven questions, capacity, the Ian rehearsal, the blank-field
refusal and the 911 refusal. `evals/make_evalset.py` regenerates it.

```bash
adk eval agent evals/readiness.evalset.json --config_file_path evals/test_config.json --print_detailed_results
```
