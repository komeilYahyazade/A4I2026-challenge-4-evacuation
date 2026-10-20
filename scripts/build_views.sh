#!/usr/bin/env bash
#
# Builds the readiness_ops dataset: views over the read-only evacuation_readiness tables, plus
# the empty operational tables the agent writes to. Safe to re-run: views are CREATE OR REPLACE,
# tables are CREATE IF NOT EXISTS (never truncated).
#
#     bash scripts/build_views.sh                 # every sql/*.sql in order
#     bash scripts/build_views.sh sql/10_*.sql    # just these
#
# Column-by-column documentation lives in contract/DATA.md.

set -euo pipefail

SRC_DATASET="evacuation_readiness"
OPS_DATASET="readiness_ops"
LOCATION="US"

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
[[ -n "${PROJECT}" && "${PROJECT}" != "(unset)" ]] \
  || { echo "No project. Set GOOGLE_CLOUD_PROJECT or run: gcloud config set project ID" >&2; exit 1; }

cd "$(dirname "$0")/.."

if ! bq --project_id="${PROJECT}" show --dataset --format=none "${PROJECT}:${OPS_DATASET}" >/dev/null 2>&1; then
  bq --project_id="${PROJECT}" --location="${LOCATION}" mk --dataset \
     --description="Views and operational tables for the readiness agent. Source tables stay in ${SRC_DATASET}." \
     "${PROJECT}:${OPS_DATASET}" >/dev/null
  echo "created ${OPS_DATASET}"
fi

# What "reachable" means is decided once, in agent/reachability.py. Read it from there so the
# views and the agent can never disagree. The last assignment wins (the docstring repeats them).
threshold() {
  local v
  v="$(grep -E "^$1( *:[^=]*)? *=" agent/reachability.py | tail -1 | sed -E 's/^[^=]*= *//; s/_//g; s/ *#.*$//')"
  [[ -n "${v}" ]] || { echo "Cannot read $1 from agent/reachability.py" >&2; exit 1; }
  echo "${v}"
}
WALK_KM="$(threshold WALK_KM)"
PROXY_KM="$(threshold PARATRANSIT_PROXY_KM)"
CAR_KM="$(threshold CAR_KM)"
CAPACITY_MAX="$(threshold CAPACITY_PLAUSIBLE_MAX)"
# ("EVAC", "BOTH") -> 'EVAC', 'BOTH'
PRE_LANDFALL="$(threshold PRE_LANDFALL_USAGE | tr -d '()' | tr '"' "'" | sed -E "s/, *\$//")"
echo "  thresholds: walk ${WALK_KM} km, run ${PROXY_KM} km, search ${CAR_KM} km, pre-landfall ${PRE_LANDFALL}"

files=("$@")
[[ ${#files[@]} -gt 0 ]] || files=(sql/*.sql)

for f in "${files[@]}"; do
  printf '  %-44s' "${f}"
  sql="$(sed -e "s/\${PROJECT}/${PROJECT}/g" \
             -e "s/\${SRC}/${SRC_DATASET}/g" \
             -e "s/\${OPS}/${OPS_DATASET}/g" \
             -e "s/\${WALK_KM}/${WALK_KM}/g" \
             -e "s/\${PROXY_KM}/${PROXY_KM}/g" \
             -e "s/\${CAR_KM}/${CAR_KM}/g" \
             -e "s/\${CAPACITY_MAX}/${CAPACITY_MAX}/g" \
             -e "s/\${PRE_LANDFALL}/${PRE_LANDFALL}/g" "${f}")"
  if out="$(bq --project_id="${PROJECT}" --location="${LOCATION}" query \
              --use_legacy_sql=false --format=none <<<"${sql}" 2>&1)"; then
    echo "ok"
  else
    echo "FAILED"
    echo "${out}" | tail -5 >&2
    exit 1
  fi
done
