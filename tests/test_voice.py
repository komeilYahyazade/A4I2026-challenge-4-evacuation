"""Voice app: builds offline, runs Gemini Live in us-central1, shares the text agent's tools and rules."""

from google.adk.models import Gemini
from google.adk.tools.mcp_tool import McpToolset

from agent.agent import FUNCTION_TOOLS
from agent.config import AgentConfig
from agent.prompts import SYSTEM_INSTRUCTION, VOICE_INSTRUCTION


def test_voice_agent_uses_gemini_live_in_us_central1():
    from voice.agent import build_voice_agent

    agent = build_voice_agent(AgentConfig(project_id="demo-project"))
    assert agent.name == "readiness_voice"
    assert isinstance(agent.model, Gemini) and agent.model.model == "gemini-3.8-live"
    assert agent.model.client_kwargs["location"] == "us-central1"
    assert agent.model.client_kwargs["project"] == "demo-project"


def test_voice_tools_are_exactly_the_function_tools():
    from voice import root_agent

    assert [t.__name__ for t in root_agent.tools] == [f.__name__ for f in FUNCTION_TOOLS]
    assert not any(isinstance(t, McpToolset) or getattr(t, "name", "") == "google_maps" for t in root_agent.tools)


def test_voice_prompt_shares_the_planning_rules_and_adds_spoken_ones():
    assert "{" not in VOICE_INSTRUCTION and "}" not in VOICE_INSTRUCTION
    core = SYSTEM_INSTRUCTION[: SYSTEM_INSTRUCTION.index("\nOUTPUT\n")]
    assert VOICE_INSTRUCTION.startswith(core)
    for rule in ("911", "UNRECORDED", "do not recommend a shelter to them", "Decline unrelated requests"):
        assert rule in VOICE_INSTRUCTION
    for rule in ("three short spoken sentences", "the Google Maps sources are on screen", "Never read out IDs",
                 "county-level", "nobody recorded it", "no google_maps, toolbox or compute_routes"):
        assert rule in VOICE_INSTRUCTION
    assert "Never paste URLs" not in VOICE_INSTRUCTION  # text-only output rules stay out of voice


def test_text_agent_is_unchanged_by_voice():
    import agent

    assert agent.root_agent.name == "readiness_agent"
    assert agent.root_agent.model == "gemini-3.6-flash"
    assert agent.root_agent.instruction.startswith(SYSTEM_INSTRUCTION)
    assert getattr(agent.root_agent.tools[0], "name", "") == "google_maps"


def test_voice_model_and_location_are_configurable():
    cfg = AgentConfig.from_env({"GOOGLE_CLOUD_PROJECT": "demo-project", "VOICE_MODEL": "gemini-3.8-live",
                                "VOICE_LOCATION": "us-east5"})
    assert cfg.voice_location == "us-east5"
