"""The agents (BUILD_SPEC.md section 8).

root_agent (= readiness_agent) answers chat with every tool, Google Maps grounding and the BigQuery
MCP server. brief_pipeline = brief_analyst (custom tools only) -> brief_writer (no tools,
output_schema=BriefJudgments). Numbers come from tools; judgments come from the model.
"""

import json
import sys
from pathlib import Path

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import FunctionTool, google_maps_grounding
from google.genai import types

_ROOT = str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:  # `contract` lives at the repo root
    sys.path.append(_ROOT)

from contract.schemas import BriefJudgments  # noqa: E402

from readiness_agent import brief_plan, config, prompts  # noqa: E402
from readiness_agent.tools import facts  # noqa: E402
from readiness_agent.tools.care import list_care_facilities  # noqa: E402
from readiness_agent.tools.gaps import find_readiness_gaps  # noqa: E402
from readiness_agent.tools.mcp import bigquery_mcp  # noqa: E402
from readiness_agent.tools.power import get_power_dependence  # noqa: E402
from readiness_agent.tools.seats import allocate_seats, rehearse_ian  # noqa: E402
from readiness_agent.tools.shelters import find_candidate_shelters  # noqa: E402
from readiness_agent.tools.tracts import rank_vulnerable_tracts  # noqa: E402
from readiness_agent.tools.verify import verify_shelter  # noqa: E402

CUSTOM_TOOLS = [FunctionTool(fn) for fn in (
    rank_vulnerable_tracts, list_care_facilities, get_power_dependence,
    find_candidate_shelters, verify_shelter, find_readiness_gaps, allocate_seats)]
# Chat and voice also get the Ian tabletop; the brief pipeline does not run it.
CHAT_TOOLS = [*CUSTOM_TOOLS, FunctionTool(rehearse_ian)]


def _prepare_digest(callback_context):
    """Before the writer runs: build the compact facts JSON it reads via {facts_digest}."""
    f = facts.collect(callback_context.state)
    track = callback_context.state.get("track") or "shelter_readiness"
    callback_context.state["facts_digest"] = json.dumps(
        {"facts": json.loads(facts.digest(f)), "tasks": brief_plan.writer_tasks(f, track)},
        separators=(",", ":"), default=str)
    return None


brief_analyst = LlmAgent(
    name="brief_analyst",
    model=config.MODEL,
    description="Gathers the facts for one county's readiness brief and acts on Google Maps results.",
    instruction=prompts.analyst_instruction,  # steps differ by track
    tools=CUSTOM_TOOLS,
)

brief_writer = LlmAgent(
    name="brief_writer",
    model=config.MODEL,
    description="Turns the gathered facts into judgments, referring to facts by id only.",
    instruction=prompts.WRITER,
    output_schema=BriefJudgments,
    output_key="judgments",
    include_contents="none",
    before_agent_callback=_prepare_digest,
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)

brief_pipeline = SequentialAgent(name="brief_pipeline", sub_agents=[brief_analyst, brief_writer])

readiness_agent = LlmAgent(
    name="readiness_agent",
    model=config.MODEL,
    description="Answers an emergency manager's hurricane-season planning questions.",
    instruction=prompts.CHAT,
    tools=[*CHAT_TOOLS, google_maps_grounding, bigquery_mcp],
)

root_agent = readiness_agent
