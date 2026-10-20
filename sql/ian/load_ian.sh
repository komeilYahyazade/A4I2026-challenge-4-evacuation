#!/usr/bin/env bash
#
# Loads the Hurricane Ian (AL092022) replay into readiness_ops: five NHC 5-day forecast cones and
# tracks (advisories 9, 11, 13, 17, 19, i.e. 82 to 22 hours before landfall) plus the best track,
# then builds v_ian_shelters_down. NOAA NHC GIS archive, public domain.
#
#     bash sql/ian/load_ian.sh
#
# Downloads go to a fresh temp directory and are parsed with pyshp under `python -I`.

set -euo pipefail
cd "$(dirname "$0")/../.."

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
OPS="readiness_ops"
ADVISORIES=(009 011 013 017 019)
DL="$(mktemp -d)"
OUT="$(mktemp -d)"
trap 'rm -rf "${DL}" "${OUT}"' EXIT

for a in "${ADVISORIES[@]}"; do
  curl -sSf -m 60 -o "${DL}/al092022_5day_${a}.zip" "https://www.nhc.noaa.gov/gis/forecast/archive/al092022_5day_${a}.zip"
  mkdir -p "${DL}/x_al092022_5day_${a}" && unzip -oq "${DL}/al092022_5day_${a}.zip" -d "${DL}/x_al092022_5day_${a}"
done
curl -sSf -m 60 -o "${DL}/al092022_best_track.zip" "https://www.nhc.noaa.gov/gis/best_track/al092022_best_track.zip"
mkdir -p "${DL}/x_al092022_best_track" && unzip -oq "${DL}/al092022_best_track.zip" -d "${DL}/x_al092022_best_track"

uv run -q --no-project --python 3.12 --with pyshp python -I "$(pwd)/sql/ian/nhc_to_ndjson.py" "${DL}" "${OUT}"

bq --project_id="${PROJECT}" --location=US load --replace --source_format=NEWLINE_DELIMITED_JSON \
  "${PROJECT}:${OPS}._stage_storm_advisories" "${OUT}/storm_advisories.ndjson" \
  storm_id:STRING,storm_name:STRING,advisory:STRING,issued_at:TIMESTAMP,storm_type:STRING,cone_geojson:STRING,track_json:STRING,track_geojson:STRING,source:STRING
bq --project_id="${PROJECT}" --location=US load --replace --source_format=NEWLINE_DELIMITED_JSON \
  "${PROJECT}:${OPS}.storm_best_track" "${OUT}/storm_best_track.ndjson" \
  storm_id:STRING,valid_at:TIMESTAMP,lat:FLOAT64,lng:FLOAT64,wind_kt:INT64,mslp_mb:INT64,storm_type:STRING,source:STRING

# Landfall: Cayo Costa, Lee County, 2022-09-28 19:05 UTC (NHC Tropical Cyclone Report AL092022).
bq --project_id="${PROJECT}" --location=US query --use_legacy_sql=false --format=none "
CREATE OR REPLACE TABLE \`${PROJECT}.${OPS}.storm_advisories\`
OPTIONS (description='NHC forecast cones and tracks for Hurricane Ian (AL092022). NOAA NHC GIS archive, public domain. Replay data for tabletop rehearsal only.')
AS SELECT storm_id, storm_name, advisory, issued_at, storm_type,
  ST_GEOGFROMGEOJSON(cone_geojson, make_valid => TRUE) AS cone,
  cone_geojson, track_json, track_geojson,
  ROUND(TIMESTAMP_DIFF(TIMESTAMP '2022-09-28 19:05:00+00', issued_at, MINUTE) / 60, 1) AS hours_before_landfall,
  source
FROM \`${PROJECT}.${OPS}._stage_storm_advisories\`"

bash scripts/build_views.sh sql/ian/70_v_ian_shelters_down.sql
