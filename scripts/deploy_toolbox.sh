#!/usr/bin/env bash
# Deploy MCP Toolbox for Databases to Cloud Run: private, BigQuery read-only, one warm instance.
# Usage (repo root, Cloud Shell):
#   GOOGLE_CLOUD_PROJECT=<project> bash scripts/deploy_toolbox.sh
# Optional: REGION (default us-central1), AGENT_SA (service account allowed to call it),
#           TOOLBOX_IMAGE (default: the official image, latest).
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${REGION:-us-central1}"
SA_NAME="toolbox-identity"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${TOOLBOX_IMAGE:-us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest}"

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable run.googleapis.com secretmanager.googleapis.com bigquery.googleapis.com iam.googleapis.com

if ! gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name "MCP Toolbox (read-only BigQuery)"
fi
for role in roles/secretmanager.secretAccessor roles/bigquery.dataViewer roles/bigquery.jobUser; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$role" \
    --condition=None --quiet >/dev/null
done

if gcloud secrets describe tools >/dev/null 2>&1; then
  gcloud secrets versions add tools --data-file=toolbox/tools.yaml
else
  gcloud secrets create tools --data-file=toolbox/tools.yaml
fi

# Cloud Run scales to zero by default; the first request after idle is the one made on stage.
gcloud run deploy toolbox \
  --image "$IMAGE" \
  --region "$REGION" \
  --service-account "$SA" \
  --min-instances 1 \
  --no-allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT" \
  --set-secrets "/app/tools.yaml=tools:latest" \
  --args="--config=/app/tools.yaml","--address=0.0.0.0","--port=8080"

URL="$(gcloud run services describe toolbox --region "$REGION" --format 'value(status.url)')"

if [[ -n "${AGENT_SA:-}" ]]; then
  gcloud run services add-iam-policy-binding toolbox --region "$REGION" \
    --member "serviceAccount:${AGENT_SA}" --role roles/run.invoker >/dev/null
  echo "Granted run.invoker on toolbox to ${AGENT_SA}"
fi

echo
echo "MCP_TOOLBOX_URL=${URL}"
echo "The agent connects to ${URL}/mcp/evacuation_readiness"
