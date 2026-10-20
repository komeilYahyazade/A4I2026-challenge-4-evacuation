"""Runtime configuration for the readiness agent.

Modeling thresholds (what "reachable" means, what counts as pre-landfall) are decisions, not
settings, so they live in reachability.py with the reasoning beside them.
"""

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

# FIPS codes are fixed by federal standard; hardcoding them is safe.
STATE_FIPS: dict[str, str] = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "PR": "72",
}

TABLES: tuple[str, ...] = (
    "shelters",
    "vulnerability_tracts",
    "hazard_tracts",
    "care_facilities",
    "power_dependent_counties",
)

OPS_VIEWS: tuple[str, ...] = ("v_shelter_site_hazard",)

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9\-]{4,61}[a-z0-9]$|^[a-z0-9\-.:]+$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")


def _truthy(value: Optional[str], default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AgentConfig:
    """Everything the agent reads from the environment, validated once."""

    project_id: str = ""
    dataset: str = "evacuation_readiness"
    ops_dataset: str = "readiness_ops"
    state: str = "FL"
    bq_location: str = "US"
    model_name: str = "gemini-3.6-flash"
    default_county_fips: str = "12071"  # Lee County, FL: Hurricane Ian landfall, 2022
    mcp_toolbox_url: str = ""
    mcp_toolbox_toolset: str = "evacuation_readiness"
    mcp_toolbox_auth: str = "id_token"  # "id_token" for a private Cloud Run service, or "none"
    maps_lite_url: str = "https://mapstools.googleapis.com/mcp"
    maps_lite_api_key: str = ""
    bigquery_mcp_enabled: bool = False
    bigquery_mcp_url: str = "https://bigquery.googleapis.com/mcp"
    require_mcp: bool = False
    verify_max: int = 8
    verify_concurrency: int = 5
    verify_timeout_s: float = 45.0

    def __post_init__(self) -> None:
        if self.project_id and not _PROJECT_RE.match(self.project_id):
            raise ValueError(f"GOOGLE_CLOUD_PROJECT {self.project_id!r} is not a valid project id")
        if not _DATASET_RE.match(self.dataset):
            raise ValueError(f"BQ_DATASET {self.dataset!r} is not a valid dataset id")
        if not _DATASET_RE.match(self.ops_dataset):
            raise ValueError(f"OPS_DATASET {self.ops_dataset!r} is not a valid dataset id")
        if self.state not in STATE_FIPS:
            raise ValueError(f"EVAC_STATE {self.state!r} is not a two-letter state code")

    @property
    def state_fips(self) -> str:
        return STATE_FIPS[self.state]

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AgentConfig":
        src = os.environ if env is None else env
        return cls(
            project_id=src.get("GOOGLE_CLOUD_PROJECT", "").strip(),
            dataset=src.get("BQ_DATASET", "evacuation_readiness").strip(),
            ops_dataset=src.get("OPS_DATASET", "readiness_ops").strip(),
            state=src.get("EVAC_STATE", "FL").strip().upper(),
            bq_location=src.get("BQ_LOCATION", "US").strip(),
            model_name=src.get("ADK_MODEL", "gemini-3.6-flash").strip(),
            default_county_fips=src.get("EVAC_COUNTY_FIPS", "12071").strip(),
            mcp_toolbox_url=src.get("MCP_TOOLBOX_URL", "").strip().rstrip("/"),
            mcp_toolbox_toolset=src.get("MCP_TOOLBOX_TOOLSET", "evacuation_readiness").strip(),
            mcp_toolbox_auth=src.get("MCP_TOOLBOX_AUTH", "id_token").strip().lower(),
            maps_lite_url=src.get("MAPS_GROUNDING_LITE_URL", "https://mapstools.googleapis.com/mcp").strip(),
            maps_lite_api_key=src.get("MAPS_GROUNDING_LITE_API_KEY", "").strip(),
            bigquery_mcp_enabled=_truthy(src.get("BIGQUERY_MCP_ENABLED"), False),
            bigquery_mcp_url=src.get("BIGQUERY_MCP_URL", "https://bigquery.googleapis.com/mcp").strip(),
            require_mcp=_truthy(src.get("REQUIRE_MCP"), False),
            verify_max=int(src.get("VERIFY_MAX", "8")),
            verify_concurrency=int(src.get("VERIFY_CONCURRENCY", "5")),
            verify_timeout_s=float(src.get("VERIFY_TIMEOUT_S", "45")),
        )

    def table(self, name: str) -> str:
        """Backtick-quoted `project.dataset.table`. Only the five known tables are allowed."""
        if name not in TABLES:
            raise ValueError(f"Unknown table {name!r}. Valid tables: {TABLES}")
        prefix = f"{self.project_id}.{self.dataset}" if self.project_id else self.dataset
        return f"`{prefix}.{name}`"

    def ops_table(self, name: str) -> str:
        """Derived views built by the data lane (scripts/build_views.sh). Only known views are allowed."""
        if name not in OPS_VIEWS:
            raise ValueError(f"Unknown ops view {name!r}. Valid views: {OPS_VIEWS}")
        prefix = f"{self.project_id}.{self.ops_dataset}" if self.project_id else self.ops_dataset
        return f"`{prefix}.{name}`"


def county_fips_or_error(value: str, cfg: AgentConfig) -> str:
    """Normalize a county FIPS and check it belongs to the configured state."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 5:
        raise ValueError(
            f"County FIPS must be 5 digits, got {value!r}. Use find_county to look it up."
        )
    if not digits.startswith(cfg.state_fips):
        raise ValueError(
            f"County {digits} is not in {cfg.state} (state FIPS {cfg.state_fips}). "
            f"The loaded dataset covers {cfg.state} only."
        )
    return digits


def tract_geo_id_or_error(value: str, cfg: AgentConfig) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) != 11 or not digits.startswith(cfg.state_fips):
        raise ValueError(f"Census tract id must be 11 digits in {cfg.state}, got {value!r}")
    return digits
