import pytest
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.mcp_tool import McpToolset

from agent.agent import (
    FUNCTION_TOOLS, bigquery_mcp_toolset, build_root_agent, maps_lite_toolset, mcp_toolsets, toolbox_toolset,
)
from agent.config import AgentConfig
from agent.prompts import SYSTEM_INSTRUCTION


def _names(agent):
    return [getattr(t, "name", None) or getattr(t, "__name__", type(t).__name__) for t in agent.tools]


def test_single_agent_on_gemini_3_with_maps_and_all_tools():
    agent = build_root_agent(AgentConfig(project_id="demo-project"))
    names = _names(agent)
    assert names[0] == "google_maps"
    for fn in FUNCTION_TOOLS:
        assert fn.__name__ in names


def test_instruction_names_the_bigquery_project_and_datasets():
    # Without this the agent guesses project ids for the BigQuery MCP tools.
    for model in ("gemini-3.6-flash", "gemini-2.5-flash"):
        agent = build_root_agent(AgentConfig(project_id="demo-project", model_name=model))
        assert "demo-project" in agent.instruction
        assert "evacuation_readiness" in agent.instruction and "readiness_ops" in agent.instruction
        assert "{" not in agent.instruction and "}" not in agent.instruction


def test_instruction_names_the_default_county_for_unscoped_questions():
    # The console's suggestions ("Which of my shelters...") name no county.
    agent = build_root_agent(AgentConfig(project_id="demo-project", default_county_fips="12071"))
    assert "12071" in agent.instruction


def test_instruction_without_a_project_is_the_base_instruction():
    assert build_root_agent(AgentConfig()).instruction == SYSTEM_INSTRUCTION


def test_tool_errors_go_back_to_the_model_instead_of_ending_the_turn():
    agent = build_root_agent(AgentConfig(project_id="demo-project"))
    result = agent.on_tool_error_callback(
        tool=None, args={"county_fips": "00000"}, tool_context=None,
        error=ValueError("County 00000 is not in FL"))
    assert result["status"] == "error"
    assert "County 00000 is not in FL" in result["error"]


def test_sub_agent_fallback_on_gemini_2_5():
    agent = build_root_agent(AgentConfig(project_id="demo-project", model_name="gemini-2.5-flash"))
    assert isinstance(agent.tools[0], AgentTool)
    assert "google_maps" not in _names(agent)


def test_instruction_has_no_state_placeholders_and_key_rules():
    assert "{" not in SYSTEM_INSTRUCTION and "}" not in SYSTEM_INSTRUCTION
    for phrase in ("911", "UNRECORDED", "never which tract", "ONE named building", "Google Maps",
                   "only in a turn where you called google_maps or verify_shelters", "not zero spaces",
                   "recorded in the surge zone", "[APPROXIMATE]", "INACTIVE or CLOSED", "Never paste URLs",
                   "ranking may_need_help", "Never use ranking composite_v0 to plan"):
        assert phrase in SYSTEM_INSTRUCTION


@pytest.mark.parametrize("fn", FUNCTION_TOOLS, ids=lambda f: f.__name__)
def test_declarations_hide_tool_context_and_have_docstrings(fn):
    decl = FunctionTool(fn)._get_declaration()
    schema = decl.parameters_json_schema or {}
    assert "tool_context" not in (schema.get("properties") or {})
    assert decl.description and len(decl.description) > 80


def test_toolbox_is_consumed_over_mcp_with_id_token():
    ts = toolbox_toolset(AgentConfig(project_id="demo-project", mcp_toolbox_url="https://toolbox-x.a.run.app"))
    assert len(ts) == 1 and isinstance(ts[0], McpToolset)
    assert ts[0]._connection_params.url == "https://toolbox-x.a.run.app/mcp/evacuation_readiness"
    assert ts[0]._header_provider is not None
    local = toolbox_toolset(AgentConfig(project_id="demo-project", mcp_toolbox_url="http://127.0.0.1:5000",
                                        mcp_toolbox_auth="none"))
    assert local[0]._header_provider is None


def test_maps_grounding_lite_exposes_only_compute_routes():
    ts = maps_lite_toolset(AgentConfig(project_id="demo-project", maps_lite_api_key="k"))
    assert ts[0].tool_filter == ["compute_routes"]
    assert ts[0]._connection_params.headers == {"X-Goog-Api-Key": "k"}


def test_missing_mcp_fails_loudly_when_required():
    with pytest.raises(RuntimeError, match="MCP"):
        mcp_toolsets(AgentConfig(project_id="demo-project", require_mcp=True))
    assert mcp_toolsets(AgentConfig(project_id="demo-project")) == []


def test_config_rejects_bad_identifiers_and_out_of_state_counties():
    from agent.config import county_fips_or_error

    with pytest.raises(ValueError):
        AgentConfig(project_id="demo-project", dataset="x`; DROP")
    with pytest.raises(ValueError):
        county_fips_or_error("45019", AgentConfig(project_id="demo-project"))  # SC county, FL dataset
    assert county_fips_or_error("12-071", AgentConfig(project_id="demo-project")) == "12071"


def test_managed_bigquery_mcp_is_opt_in():
    assert bigquery_mcp_toolset(AgentConfig(project_id="demo-project")) == []
    ts = bigquery_mcp_toolset(AgentConfig(project_id="demo-project", bigquery_mcp_enabled=True))
    assert ts[0]._connection_params.url == "https://bigquery.googleapis.com/mcp"
    assert ts[0]._header_provider is not None


def test_managed_bigquery_mcp_exposes_only_small_read_tools():
    # The full tool list is ~630 KB of schema and trips the per-minute token quota.
    ts = bigquery_mcp_toolset(AgentConfig(project_id="demo-project", bigquery_mcp_enabled=True))
    assert ts[0].tool_filter == ["list_table_ids", "execute_sql_readonly"]


def test_audit_reports_coverage_of_the_countys_need():
    from agent.tools import coverage

    ranked = [{"households_no_vehicle": 639, "mobile_homes": 10, "pop_65_plus": 100},
              {"households_no_vehicle": 456, "mobile_homes": None, "pop_65_plus": 50}]
    totals = {"households_no_vehicle": 14553, "mobile_homes": 39299, "pop_65_plus": 215942}
    c = coverage(ranked, totals)
    assert c["households_no_vehicle"] == {"covered": 1095, "county_total": 14553, "share": round(1095 / 14553, 4)}
    assert c["mobile_homes"]["covered"] == 10
    assert coverage([], {})["pop_65_plus"]["share"] is None
