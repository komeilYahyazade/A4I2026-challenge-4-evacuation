"""Voice agent on Gemini Live, ported from main's voice/agent.py. Same function tools and rules as
chat, spoken. Gemini Live is served regionally (VOICE_LOCATION, us-central1), so this model gets its
own client location; the text agents and verify_shelter's grounded calls stay global, where Grounding
with Google Maps is verified. No google_maps built-in and no MCP toolset: verify_shelter does its own
grounded call, and a small live config keeps latency down.
"""

from google.adk.agents import Agent
from google.adk.models import Gemini

from readiness_agent import config, prompts
from readiness_agent.agent import CHAT_TOOLS

_client = {"vertexai": True, "location": config.VOICE_LOCATION}
if config.PROJECT:
    _client["project"] = config.PROJECT

root_agent = Agent(
    name="readiness_voice",
    model=Gemini(model=config.VOICE_MODEL, client_kwargs=_client),
    description="Spoken pre-season evacuation readiness analyst. Same evidence rules as chat.",
    instruction=prompts.VOICE,
    tools=list(CHAT_TOOLS),
)
