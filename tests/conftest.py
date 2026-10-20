import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "demo-project")
for key in ("MCP_TOOLBOX_URL", "MAPS_GROUNDING_LITE_API_KEY", "REQUIRE_MCP", "ADK_MODEL", "EVAC_STATE", "BQ_DATASET"):
    os.environ.pop(key, None)
