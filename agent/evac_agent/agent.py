import google.auth
import google.auth.transport.requests
from google.adk.agents import LlmAgent
from google.adk.tools import McpToolset
from google.adk.tools.google_maps_grounding_tool import GoogleMapsGroundingTool
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai import types

from . import config, tools


class MapsGroundingWithRouting(GoogleMapsGroundingTool):
    """Maps grounding with routing switched on, so grounded calls can return drive times."""

    async def process_llm_request(self, *, tool_context, llm_request) -> None:
        await super().process_llm_request(tool_context=tool_context, llm_request=llm_request)
        for tool in llm_request.config.tools:
            if getattr(tool, "google_maps", None) is not None:
                tool.google_maps.grounding_types = types.GoogleMapsGroundingTypes(
                    places=types.GoogleMapsPlaces(), routing=types.GoogleMapsRouting())


_credentials = None


def _bigquery_auth_headers(_ctx) -> dict[str, str]:
    """Fresh OAuth token per MCP request; Application Default Credentials expire hourly."""
    global _credentials
    if _credentials is None:
        _credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/bigquery"])
    if not _credentials.valid:
        _credentials.refresh(google.auth.transport.requests.Request())
    headers = {"Authorization": f"Bearer {_credentials.token}"}
    if config.PROJECT:
        headers["x-goog-user-project"] = config.PROJECT
    return headers


bigquery_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=config.BIGQUERY_MCP_URL, timeout=30),
    header_provider=_bigquery_auth_headers,
)

INSTRUCTION = f"""
You help a county emergency manager in {config.STATE} prepare a hurricane-season
readiness brief in August, weeks before landfall. This is planning, not live response.

Data (BigQuery dataset `{config.PROJECT}.{config.DATASET}`):
- vulnerability_tracts: people per census tract (no-vehicle households, age 65+, disability, mobile homes)
- power_dependent_counties: residents relying on powered medical equipment, per COUNTY only
- care_facilities: nursing homes with certified beds
- shelters: FEMA's historical shelter registry with capacity and YES/NO/blank flags
- hazard_tracts: hurricane and coastal-flood exposure per tract

How to work:
1. Use resolve_county, then rank_tracts_by_evacuation_need, then find_shelters_near_tract
   for the questions those tools fit. Use the BigQuery MCP tools for anything else.
2. Unrecorded is not "no". Never report "no accessible shelters" when the record is blank.
   Say, for example, "3 recorded accessible, 0 recorded not, 11 nobody has assessed".
3. Verify candidate shelters with Google Maps ONE building at a time, by name and full
   address. Ask whether it still exists, what it is now, whether it was renamed, and what
   it says about wheelchair access. Asking about a whole area returns nothing useful.
4. Act on what Maps returns: drop or flag shelters that closed or changed, note renames,
   and report drive time from the tract when routing returns it.
5. Label every claim's source: "FEMA record (historical)" versus "Google Maps (live now)".
   When they disagree, say so and recommend a site check.
6. Power dependence is county-level. Say how many, never which neighborhood.
7. Never identify or speculate about individual people.
8. When you use Google Maps, cite it right after the claim it supports, written exactly as
   "Google Maps".

Finish with what to fix before the season, and what you could not determine.
"""

root_agent = LlmAgent(
    name="evacuation_readiness",
    model=config.MODEL,
    description="Hurricane evacuation readiness assistant for a county emergency manager.",
    instruction=INSTRUCTION,
    tools=[
        tools.resolve_county,
        tools.rank_tracts_by_evacuation_need,
        tools.find_shelters_near_tract,
        MapsGroundingWithRouting(),
        bigquery_mcp,
    ],
)
