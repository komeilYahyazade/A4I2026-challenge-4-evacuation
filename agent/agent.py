"""Root agent: Gemini 3.x with Google Maps grounding, our function tools, and managed MCP servers.

On Gemini 3.x a built-in tool and function tools can share one agent (organizer-verified on
gemini-3.6-flash, 2026-08-09). On 2.5 and older the request is rejected, so Maps moves into a
sub-agent wrapped in AgentTool.
"""

import logging
import time
from typing import Any, Callable

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams, StreamableHTTPConnectionParams

from .brief import build_readiness_brief
from .config import AgentConfig
from .grounding import maps_grounding
from .prompts import SYSTEM_INSTRUCTION
from .allocate import allocate_county
from .rehearse import rehearse
from .tools import (
    audit_equity,
    care_facility_gaps,
    county_overview,
    find_county,
    lookup_shelter,
    rank_tracts,
    shelters_for_tract,
)
from .verify import verify_shelters

logger = logging.getLogger(__name__)

FUNCTION_TOOLS = [
    find_county,
    county_overview,
    rank_tracts,
    shelters_for_tract,
    lookup_shelter,
    verify_shelters,
    care_facility_gaps,
    build_readiness_brief,
    audit_equity,
    allocate_county,
    rehearse,
]


def _id_token_provider(audience: str) -> Callable[[Any], dict[str, str]]:
    """Authorization header for a private Cloud Run service, refreshed before the token expires."""
    cache: dict[str, Any] = {"token": None, "at": 0.0}

    def provider(_ctx: Any) -> dict[str, str]:
        if cache["token"] is None or time.time() - cache["at"] > 45 * 60:
            from google.auth.transport.requests import Request
            from google.oauth2 import id_token

            cache["token"] = id_token.fetch_id_token(Request(), audience)
            cache["at"] = time.time()
        return {"Authorization": f"Bearer {cache['token']}"}

    return provider


def toolbox_toolset(cfg: AgentConfig) -> list[Any]:
    """MCP Toolbox for Databases, consumed over MCP at /mcp/<toolset> (streamable HTTP)."""
    if not cfg.mcp_toolbox_url:
        return []
    url = cfg.mcp_toolbox_url
    if url.endswith("/sse"):
        params: Any = SseConnectionParams(url=url)
    else:
        if "/mcp" not in url:
            url = f"{url}/mcp/{cfg.mcp_toolbox_toolset}"
        params = StreamableHTTPConnectionParams(url=url)
    header_provider = None
    if cfg.mcp_toolbox_auth == "id_token":
        audience = cfg.mcp_toolbox_url.split("/mcp")[0]
        header_provider = _id_token_provider(audience)
    return [McpToolset(connection_params=params, header_provider=header_provider, tool_name_prefix="toolbox")]


def maps_lite_toolset(cfg: AgentConfig) -> list[Any]:
    """Maps Grounding Lite (Google-hosted MCP). Only compute_routes: the drive-time fallback."""
    if not cfg.maps_lite_api_key:
        return []
    params = StreamableHTTPConnectionParams(url=cfg.maps_lite_url, headers={"X-Goog-Api-Key": cfg.maps_lite_api_key})
    return [McpToolset(connection_params=params, tool_filter=["compute_routes"])]


def _access_token_provider(project: str) -> Callable[[Any], dict[str, str]]:
    """OAuth access token from Application Default Credentials, refreshed when it expires."""
    state: dict[str, Any] = {"creds": None}

    def provider(_ctx: Any) -> dict[str, str]:
        import google.auth
        from google.auth.transport.requests import Request

        if state["creds"] is None:
            state["creds"], _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
        creds = state["creds"]
        if not creds.valid:
            creds.refresh(Request())
        headers = {"Authorization": f"Bearer {creds.token}"}
        if project:
            headers["x-goog-user-project"] = project
        return headers

    return provider


def bigquery_mcp_toolset(cfg: AgentConfig) -> list[Any]:
    """Google's managed remote BigQuery MCP server. No deployment; authenticates with ADC."""
    if not cfg.bigquery_mcp_enabled:
        return []
    params = StreamableHTTPConnectionParams(url=cfg.bigquery_mcp_url)
    return [McpToolset(connection_params=params, header_provider=_access_token_provider(cfg.project_id),
                       tool_name_prefix="bigquery")]


def mcp_toolsets(cfg: AgentConfig) -> list[Any]:
    toolsets = toolbox_toolset(cfg) + maps_lite_toolset(cfg) + bigquery_mcp_toolset(cfg)
    if not toolsets:
        message = (
            "No managed MCP server configured: set MCP_TOOLBOX_URL, MAPS_GROUNDING_LITE_API_KEY "
            "or BIGQUERY_MCP_ENABLED=true. "
            "The challenge requires consuming at least one."
        )
        if cfg.require_mcp:
            raise RuntimeError(message)
        logger.warning(message)
    return toolsets


def build_root_agent(cfg: AgentConfig | None = None) -> Agent:
    cfg = cfg or AgentConfig.from_env()
    tools: list[Any] = [*FUNCTION_TOOLS, *mcp_toolsets(cfg)]
    description = (
        "Pre-season evacuation readiness analyst: federal records in BigQuery, checked live against "
        "Google Maps, honest about what nobody recorded."
    )
    if cfg.model_name.startswith("gemini-3"):
        return Agent(
            name="readiness_agent",
            model=cfg.model_name,
            description=description,
            instruction=SYSTEM_INSTRUCTION,
            tools=[maps_grounding, *tools],
        )
    maps_agent = Agent(
        name="maps_checker",
        model=cfg.model_name,
        description="Answers questions about one named place at one address using Google Maps.",
        instruction=(
            "Answer about one named building at one address using Google Maps only. Say 'unknown' when "
            "Google Maps does not say. Planning use only, never emergency response."
        ),
        tools=[maps_grounding],
    )
    return Agent(
        name="readiness_agent",
        model=cfg.model_name,
        description=description,
        instruction=SYSTEM_INSTRUCTION,
        tools=[AgentTool(agent=maps_agent), *tools],
    )


root_agent = build_root_agent()
