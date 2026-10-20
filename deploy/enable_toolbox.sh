#!/usr/bin/env bash
# Point the running agent at the MCP Toolbox once komeil-d2's deploy_toolbox.sh has granted
# readiness-agent@ run.invoker on it. No rebuild: a new revision with two env vars changed.
#   bash deploy/enable_toolbox.sh https://<toolbox-service>.run.app
set -euo pipefail
URL="${1:?usage: enable_toolbox.sh <TOOLBOX_URL>}"
PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
gcloud run services update "${SERVICE:-readiness-agent}" --project "$PROJECT" --region "${REGION:-us-central1}" \
  --update-env-vars "MCP_TOOLBOX_URL=${URL%/},MCP_TOOLBOX_AUTH=id_token,REQUIRE_MCP=true,BIGQUERY_MCP_ENABLED=false"
