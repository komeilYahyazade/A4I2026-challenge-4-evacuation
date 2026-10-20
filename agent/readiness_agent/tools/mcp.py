"""BigQuery remote MCP server (Google-managed), chat only. Requires the `mcp` package.

Identity needs roles/mcp.toolUser, roles/bigquery.jobUser, roles/bigquery.dataViewer. Limits:
queries time out after 3 minutes, results cap at 3,000 rows. Only read-only tools are exposed.
"""

import google.auth
import google.auth.transport.requests
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

from readiness_agent import config

# Read-only, and small: get_job (~235k chars) and get_table_info (~78k chars) carry output schemas
# so large that every chat turn blew the Gemini per-minute token quota. Column details come from
# INFORMATION_SCHEMA through execute_sql_readonly instead.
READ_ONLY_TOOLS = ["execute_sql_readonly", "list_dataset_ids", "list_table_ids"]

_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])


def _bq_headers(_ctx) -> dict[str, str]:
    if not _creds.valid:
        _creds.refresh(google.auth.transport.requests.Request())
    headers = {"Authorization": f"Bearer {_creds.token}"}
    if config.PROJECT:
        headers["x-goog-user-project"] = config.PROJECT
    return headers


bigquery_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url="https://bigquery.googleapis.com/mcp"),
    tool_filter=READ_ONLY_TOOLS,
    header_provider=_bq_headers,
)
