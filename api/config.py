"""Runtime configuration from the environment (BUILD_SPEC.md section 13). Never hard-code a project."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")  # local dev only; Cloud Run sets real env vars, which take precedence

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "evacuation_readiness")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")
STATE = os.environ.get("STATE", "FL")
MODEL = os.environ.get("MODEL", "gemini-3.6-flash")
MAX_PARALLEL_VERIFY = int(os.environ.get("MAX_PARALLEL_VERIFY", "6"))
VERSION = "0.3.0"

MOCK_MODE = os.environ.get("MOCK_MODE", "1") == "1"
LIVE_ROUTES = {r.strip() for r in os.environ.get("LIVE_ROUTES", "").split(",") if r.strip()}

ROUTES = ("counties", "map", "insights", "plan", "shelter", "verify", "briefs", "audit", "chat")


def is_live(route: str) -> bool:
    """A route runs real code when MOCK_MODE is off, or when it is listed in LIVE_ROUTES."""
    return not MOCK_MODE or route in LIVE_ROUTES


def mode() -> str:
    return "live" if all(is_live(r) for r in ROUTES) else "mock"
