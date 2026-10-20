"""POST /v1/shelters/{id}/assess: one turn of the field-assessment conversation.

The manager talks; the assessment agent asks the follow-ups and, once the manager confirms, saves
the operator's answer. Sessions live in memory (one Cloud Run instance, like chat). The contact
(live from Google Maps) and the saved record come back as structured fields beside the reply, read
from this turn's tool responses, so the console can show attribution and refresh the brief.
"""

from __future__ import annotations

import asyncio
import uuid

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from api import config
from api.errors import ApiError
from contract import schemas
from readiness_agent.agent import assessment_agent
from readiness_agent.tools import verify as verify_tool

APP_NAME = "readiness_assess"
TURN_TIMEOUT_S = 120

_sessions = InMemorySessionService()
_runner = Runner(app_name=APP_NAME, agent=assessment_agent, session_service=_sessions)


async def assess(shelter_id: str, req: schemas.AssessRequest) -> dict:
    session = None
    if req.session_id:
        session = await _sessions.get_session(app_name=APP_NAME, user_id="api", session_id=req.session_id)
        if session is not None and session.state.get("shelter_id") != shelter_id:
            raise ApiError(400, "session_shelter_mismatch", "This session is assessing a different shelter")
    if session is None:
        if await asyncio.to_thread(verify_tool.load_shelter, shelter_id) is None:
            raise ApiError(404, "shelter_not_found", f"No shelter {shelter_id} in {config.STATE}")
        session = await _sessions.create_session(app_name=APP_NAME, user_id="api",
                                                 session_id=f"asm_{uuid.uuid4().hex[:10]}",
                                                 state={"shelter_id": shelter_id})

    message = types.Content(role="user", parts=[types.Part(text=req.message)])
    texts: list[str] = []
    contact, saved = None, None

    async def drive():
        nonlocal contact, saved
        async for ev in _runner.run_async(user_id="api", session_id=session.id, new_message=message):
            for resp in ev.get_function_responses() or []:
                body = resp.response or {}
                if resp.name == "get_shelter_contact" and body.get("status") == "ok":
                    contact = body.get("contact")
                elif resp.name == "save_field_assessment" and body.get("status") == "ok":
                    saved = body.get("saved")
            if ev.author == assessment_agent.name and ev.content and ev.is_final_response():
                texts.extend(p.text for p in ev.content.parts or [] if p.text and not getattr(p, "thought", False))

    try:
        await asyncio.wait_for(drive(), timeout=TURN_TIMEOUT_S)
    except TimeoutError:
        raise ApiError(504, "assess_timeout", "The assessment agent took too long; try again.", retryable=True)
    reply = "\n\n".join(t.strip() for t in texts if t.strip()) or "I could not produce a reply; please try again."
    return {"session_id": session.id, "shelter_id": shelter_id, "reply": reply, "contact": contact, "saved": saved}
