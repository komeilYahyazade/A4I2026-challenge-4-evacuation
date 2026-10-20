"""Voice agent on Gemini Live. Same function tools and planning rules as the text agent.

Gemini Live is served in us-central1 only, so this agent's model gets its own location through
client_kwargs. The text agent and verify_shelters' grounded calls stay at GOOGLE_CLOUD_LOCATION
(global), where Grounding with Google Maps is verified. No google_maps built-in and no MCP toolsets:
verify_shelters does its own grounded call, and keeping the live config small keeps latency down.
"""

from typing import Any, Optional

from google.adk.agents import Agent
from google.adk.models import Gemini

from agent.agent import FUNCTION_TOOLS
from agent.config import AgentConfig
from agent.prompts import VOICE_INSTRUCTION


def build_voice_agent(cfg: Optional[AgentConfig] = None) -> Agent:
    cfg = cfg or AgentConfig.from_env()
    client_kwargs: dict[str, Any] = {"vertexai": True, "location": cfg.voice_location}
    if cfg.project_id:
        client_kwargs["project"] = cfg.project_id
    return Agent(
        name="readiness_voice",
        model=Gemini(model=cfg.voice_model, client_kwargs=client_kwargs),
        description="Spoken pre-season evacuation readiness analyst. Same evidence rules as the text agent.",
        instruction=VOICE_INSTRUCTION,
        tools=list(FUNCTION_TOOLS),
    )


root_agent = build_voice_agent()
