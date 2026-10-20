"""readiness-api: ADK's own routes (/dev-ui, /run, /run_sse) plus the /v1 contract, in one app.

Run: uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

import logging
import sys
import threading

from api.config import ROOT

# ADK's loader imports agents from agents_dir as top-level packages. Putting agent/ on sys.path
# lets the API import `readiness_agent` as the same module, so both share one set of agents.
AGENTS_DIR = ROOT / "agent"
sys.path.insert(0, str(AGENTS_DIR))

from google.adk.cli.fast_api import get_fast_api_app  # noqa: E402

from api import errors  # noqa: E402
from api.routes import router  # noqa: E402

logging.basicConfig(level=logging.INFO)

# In-memory sessions only: session events can carry Google Maps content, which may not be stored.
app = get_fast_api_app(agents_dir=str(AGENTS_DIR), web=True, allow_origins=["*"], use_local_storage=False)
app.include_router(router)
errors.install(app)

WARM_COUNTY = "12071"  # the demo's focus county: its first map load must not pay for a cold cache


def _warm() -> None:
    from api import config, data
    if config.is_live("counties") and config.is_live("map"):
        try:
            data.counties()
            data.county_map(WARM_COUNTY)
        except Exception:
            logging.getLogger(__name__).exception("cache warm-up failed")


threading.Thread(target=_warm, daemon=True).start()
