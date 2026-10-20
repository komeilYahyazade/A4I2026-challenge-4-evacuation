#!/usr/bin/env bash
# Deploy readiness-agent (agent/server.py) to Cloud Run: private, one warm instance, its own service account.
#   bash deploy/deploy_agent.sh
# Env: GOOGLE_CLOUD_PROJECT (default: gcloud config), REGION (us-central1), SERVICE (readiness-agent),
#      MCP_TOOLBOX_URL (optional), REQUIRE_MCP (true), FRONTEND_SA (optional; granted run.invoker).
# One instance: ADK sessions are in memory (they can hold Google Maps content, which may not be stored).
# Uses gcloud, not `adk deploy cloud_run`, so GOOGLE_CLOUD_LOCATION stays "global" (where Maps grounding works).
# Maps Grounding Lite key: created once, restricted to mapstools.googleapis.com, kept in Secret Manager
# (secret maps-grounding-lite-key), never in git.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-readiness-agent}"
SA_NAME="${SA_NAME:-readiness-agent}"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
SECRET="maps-grounding-lite-key"

gcloud services enable run.googleapis.com aiplatform.googleapis.com bigquery.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com apikeys.googleapis.com mapstools.googleapis.com \
  --project "$PROJECT"

if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name "Readiness agent (Cloud Run)"
fi
# Concurrent IAM edits (other lanes) make add-iam-policy-binding fail on etag; retry, and fail loudly.
grant() {
  local role="$1"
  for try in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" \
         --condition None --quiet >/dev/null 2>/tmp/grant.err; then return 0; fi
    sleep $((try * 2))
  done
  echo "FAILED to grant $role: $(tail -1 /tmp/grant.err)" >&2
  return 1
}
for role in roles/aiplatform.user roles/bigquery.jobUser roles/bigquery.dataViewer; do grant "$role"; done
grant roles/mcp.toolUser || echo "note: roles/mcp.toolUser not granted (only needed for the managed BigQuery MCP)"
# Writes to readiness_ops only (plans, shelter_place_ids, decision_log).
bq --project_id "$PROJECT" show --format=prettyjson "$PROJECT:readiness_ops" > /tmp/ro_acl.json
python3 - "$SA" <<'PY'
import json, sys
d = json.load(open("/tmp/ro_acl.json")); sa = sys.argv[1]
if not any(a.get("userByEmail") == sa for a in d.get("access", [])):
    d.setdefault("access", []).append({"role": "WRITER", "userByEmail": sa})
json.dump(d, open("/tmp/ro_acl.json", "w"))
PY
bq --project_id "$PROJECT" update --source /tmp/ro_acl.json "$PROJECT:readiness_ops" >/dev/null

if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  KEY_JSON="$(gcloud services api-keys create --project "$PROJECT" --display-name "readiness maps-lite (server)" \
    --api-target=service=mapstools.googleapis.com --format=json)"
  KEY="$(printf '%s' "$KEY_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("response",d).get("keyString",""))')"
  [[ -n "$KEY" ]] || { echo "API key creation returned no keyString" >&2; exit 1; }
  printf '%s' "$KEY" | gcloud secrets create "$SECRET" --project "$PROJECT" --data-file=- >/dev/null
fi
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" --member "serviceAccount:$SA" \
  --role roles/secretmanager.secretAccessor >/dev/null

ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=True"
ENV_VARS+=",GOOGLE_GENAI_USE_ENTERPRISE=True,ADK_MODEL=${ADK_MODEL:-gemini-3.6-flash}"
ENV_VARS+=",BQ_DATASET=${BQ_DATASET:-evacuation_readiness},BQ_LOCATION=US,EVAC_STATE=${EVAC_STATE:-FL},EVAC_COUNTY_FIPS=${EVAC_COUNTY_FIPS:-12071}"
ENV_VARS+=",OPS_DATASET=${OPS_DATASET:-readiness_ops},REQUIRE_MCP=${REQUIRE_MCP:-true},MCP_TOOLBOX_URL=${MCP_TOOLBOX_URL:-}"

gcloud run deploy "$SERVICE" \
  --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$SA" \
  --min-instances 1 --max-instances 1 --cpu 2 --memory 2Gi --timeout 300 --concurrency 20 \
  --no-allow-unauthenticated \
  --set-env-vars "$ENV_VARS" \
  --set-secrets "MAPS_GROUNDING_LITE_API_KEY=${SECRET}:latest"

if [[ -n "${FRONTEND_SA:-}" ]]; then
  gcloud run services add-iam-policy-binding "$SERVICE" --project "$PROJECT" --region "$REGION" \
    --member "serviceAccount:${FRONTEND_SA}" --role roles/run.invoker >/dev/null
fi
URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
echo "Agent URL: ${URL}   (APP=agent; runs as ${SA})"
echo "Local authenticated proxy: gcloud run services proxy ${SERVICE} --region ${REGION} --port 8081"
