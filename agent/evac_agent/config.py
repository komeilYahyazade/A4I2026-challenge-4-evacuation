"""Settings read from the environment (see agent/.env.example)."""

import os

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "evacuation_readiness")
STATE = os.environ.get("STATE", "FL")

# 3.x is required for one agent to hold Maps grounding alongside our own
# function tools. On 2.5 the request is rejected with a 400.
MODEL = os.environ.get("AGENT_MODEL", "gemini-3.6-flash")

# BigQuery's Google-managed MCP server.
BIGQUERY_MCP_URL = os.environ.get("BIGQUERY_MCP_URL", "https://bigquery.googleapis.com/mcp")

STATE_FIPS = {"FL": "12", "TX": "48", "GA": "13", "NC": "37", "LA": "22",
              "SC": "45", "NY": "36", "CA": "06"}


def table(name: str) -> str:
    return f"`{PROJECT}.{DATASET}.{name}`"
