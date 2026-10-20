#!/usr/bin/env bash
# Deploy readiness-web: the SPA plus a same-origin proxy to the PRIVATE readiness-agent.
# Usage (from the repo root): bash frontend/deploy.sh               # private (IAM sign-in required)
#                             PUBLIC_DEMO=1 bash frontend/deploy.sh # public, plan writes refused
# Public is a deliberate decision: anyone with the URL can then run briefs and chat (Gemini and
# Google Maps calls billed to this project). server.mjs refuses plan drafting/approval in that mode,
# rate-limits the expensive routes per IP (in memory, hence --max-instances 1) and caps bodies at 64 KB.
set -euo pipefail
PUBLIC_DEMO="${PUBLIC_DEMO:-0}"
if [[ "$PUBLIC_DEMO" == "1" ]]; then AUTH_FLAG="--allow-unauthenticated"; else AUTH_FLAG="--no-allow-unauthenticated"; fi
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
  --service-account "$SA" "$AUTH_FLAG" --min-instances 1 --max-instances 1 --timeout 300 \
  --set-env-vars "AGENT_UPSTREAM=${AGENT_UPSTREAM},APP=agent,MAPS_API_KEY=${MAPS_API_KEY},PUBLIC_DEMO=${PUBLIC_DEMO},VOICE_ENABLED=${VOICE_ENABLED:-1},VOICE_PUBLIC=${VOICE_PUBLIC:-0}"

URL="$(gcloud run services describe readiness-web --project "$PROJECT" --region "$REGION" --format='value(status.url)')"
NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
URL2="https://readiness-web-${NUM}.${REGION}.run.app"
# Browser key: Maps JavaScript API only, these referrers only (both Cloud Run URL forms).
gcloud services api-keys update "$KEY_ID" --project "$PROJECT" \
  --allowed-referrers="${URL}/*,${URL2}/*,http://localhost:5173/*,http://127.0.0.1:5173/*,http://localhost:8080/*,http://localhost:8090/*,http://localhost:4173/*" >/dev/null
if [[ "$PUBLIC_DEMO" == "1" ]]; then echo "readiness-web: $URL (PUBLIC, plan writes refused)"; exit 0; fi
echo "readiness-web: $URL (private)"
echo "View it locally through IAM: gcloud run services proxy readiness-web --region $REGION --port 8090  ->  http://localhost:8090"
