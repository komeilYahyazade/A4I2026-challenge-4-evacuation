"""Build the derived evidence tables (sql/00_shelter_evidence.sql). Safe to re-run.

    .venv/bin/python scripts/setup_bq.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from readiness_agent import config  # noqa: E402
from readiness_agent.tools import bq  # noqa: E402

t0 = time.time()
bq.run_script("00_shelter_evidence.sql")
print(f"built evidence tables in {config.PROJECT}.{config.DATASET} in {time.time() - t0:.1f}s")
for t in ("shelter_places", "shelter_evidence", "tract_scores", "care_evidence"):
    print(f"  {t:<18} {bq.client().get_table(f'{config.PROJECT}.{config.DATASET}.{t}').num_rows:>6,} rows")
