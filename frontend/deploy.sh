#!/usr/bin/env bash
# Deploy readiness-web: the SPA plus a same-origin proxy to the PRIVATE readiness-agent.
# Usage (from the repo root): bash frontend/deploy.sh
# Deployed PRIVATE (--no-allow-unauthenticated). Making it public is a separate, deliberate decision:
#   gcloud run services add-iam-policy-binding readiness-web --region us-central1 --member=allUsers --role=roles/run.invoker
set -euo pipefail
PROJECT="${GOOGLE_CLOUD_PROJECT:-qwiklabs-gcp-03-7a0c015d2e8a}"
REGION="${REGION:-us-central1}"
SA="readiness-web@${PROJECT}.iam.gserviceaccount.com"
AGENT_UPSTREAM="${AGENT_UPSTREAM:-$(gcloud run services describe readiness-agent --project "$PROJECT" --region "$REGION" --format='value(status.url)')}"
KEY_ID="${MAPS_KEY_ID:-cec21074-f41f-40d1-b592-73796be183db}"
MAPS_API_KEY="${MAPS_API_KEY:-$(gcloud services api-keys get-key-string "$KEY_ID" --project "$PROJECT" --format='value(keyString)')}"
HERE="$(cd "$(dirname "$0")" && pwd)"

gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1 ||
  gcloud iam service-accounts create readiness-web --project "$PROJECT" --display-name="readiness-web (frontend proxy)"

# The only permission the frontend needs: invoke the private agent.
gcloud run services add-iam-policy-binding readiness-agent --project "$PROJECT" --region "$REGION" \
  --member="serviceAccount:${SA}" --role=roles/run.invoker >/dev/null

(cd "$HERE" && node scripts/sync-fixtures.mjs)
gcloud run deploy readiness-web --project "$PROJECT" --region "$REGION" --source "$HERE" \
  --service-account "$SA" --no-allow-unauthenticated --min-instances 1 --timeout 300 \
  --set-env-vars "AGENT_UPSTREAM=${AGENT_UPSTREAM},APP=agent,MAPS_API_KEY=${MAPS_API_KEY}"

URL="$(gcloud run services describe readiness-web --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
URL2="https://readiness-web-${NUM}.${REGION}.run.app"
# Browser key: Maps JavaScript API only, these referrers only (both Cloud Run URL forms).
gcloud services api-keys update "$KEY_ID" --project "$PROJECT" \
  --allowed-referrers="${URL}/*,${URL2}/*,http://localhost:5173/*,http://127.0.0.1:5173/*,http://localhost:8080/*,http://localhost:8090/*,http://localhost:4173/*" >/dev/null
echo "readiness-web: $URL (private)"
echo "View it locally through IAM: gcloud run services proxy readiness-web --region $REGION --port 8090  ->  http://localhost:8090"
