"""Agent-side configuration from the environment. Never hard-code a project, key or account."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "evacuation_readiness")
# The data lane's ops dataset: Hurricane Ian NHC advisories (cones, track). Read only here.
OPS_DATASET = os.environ.get("OPS_DATASET", "readiness_ops")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")
STATE = os.environ.get("STATE", "FL")
MODEL = os.environ.get("MODEL", "gemini-3.6-flash")
# Gemini Live is regional; text and Maps grounding stay at GOOGLE_CLOUD_LOCATION (global).
VOICE_MODEL = os.environ.get("VOICE_MODEL", "gemini-3.8-live")
VOICE_LOCATION = os.environ.get("VOICE_LOCATION", "us-central1")
MAX_PARALLEL_VERIFY = int(os.environ.get("MAX_PARALLEL_VERIFY", "6"))
SQL_DIR = Path(os.environ.get("SQL_DIR", REPO_ROOT / "sql"))

# Defaults the data lane owns; every one of them travels in the brief's `assumptions`.
REACHABLE_KM_NO_VEHICLE = 3.0  # ~40-minute walk for an older adult
GENERATOR_GAP_KM = 10.0
NEED_SCORE_FORMULA = (
    "Min-max normalised within the state: 0.35 households_no_vehicle + 0.20 pop_65_plus "
    "+ 0.20 pct_disability + 0.15 mobile_homes + 0.10 group_quarters_pop"
)
