"""Cloud Run entry point: the ADK API (/apps/..., /run_sse, dev UI) plus deterministic /api routes.

The /api routes run the same tool functions the agent calls, without an LLM in the loop, so the
demo map and brief load the same way every time. Contract: contract/README.md.

    uv run --python .venv/bin/python -m agent.server          # http://localhost:8000

AGENTS_DIR must contain only the agent/ folder (the Docker image copies it to /app/agents/agent);
locally it defaults to the repo root, which works because the app is loaded by name.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
from pydantic import BaseModel

from . import data
from .allocate import TRACT_NEED_SQL, allocate_county, ops_table
from .brief import build_readiness_brief
from .config import AgentConfig, county_fips_or_error
from .contract import CONTRACT_VERSION
from .evidence import payload, three_state
from . import plans
from .rehearse import SCENARIOS, rehearse, storm_track
from .verify import verify_shelters

APP_NAME = "agent"

# Who is calling decides how much they may do. readiness-agent is private, so Cloud Run has already
# verified the Google ID token in Authorization before this code runs; we only read its claims.
#   email == PUBLIC_CALLER_SA (readiness-web's proxy, i.e. anyone on the public demo): PUBLIC
#   any other email (a signed-in person, e.g. through the localhost dev proxy):       SIGNED-IN
#   no token, an undecodable one, or one without an email claim:                     PUBLIC
# The x-readiness-public: 1 header the public proxy adds can only add restriction; its absence never
# grants trust. Public requests never write to BigQuery and get tighter Maps limits.
PUBLIC_HEADER = "x-readiness-public"
PUBLIC_VERIFY_MAX = 2
PUBLIC_CALLER_SA = os.environ.get(
    "PUBLIC_CALLER_SA", "readiness-web@qwiklabs-gcp-03-7a0c015d2e8a.iam.gserviceaccount.com").strip().lower()


def token_email(authorization: str) -> Optional[str]:
    """The email claim of a bearer JWT, unverified (Cloud Run verified it). None if absent or garbled."""
    import base64
    import json as _json

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or token.count(".") < 1:
        return None
    try:
        seg = token.split(".")[1]
        claims = _json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    except Exception:
        return None
    email = claims.get("email") if isinstance(claims, dict) else None
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def classify(headers: Any) -> dict[str, Any]:
    """Access tier from request headers (HTTP or WebSocket). Fails closed: anything unclear is public."""
    email = token_email(headers.get("authorization", ""))
    if email is None:
        return {"public": True, "email": None, "reason": "no verifiable caller identity"}
    if email == PUBLIC_CALLER_SA:
        return {"public": True, "email": email, "reason": "public demo proxy"}
    if headers.get(PUBLIC_HEADER, "").strip() == "1":
        return {"public": True, "email": email, "reason": "public header"}
    return {"public": False, "email": email, "reason": "signed-in caller"}


def caller(request: Request) -> dict[str, Any]:
    return classify(request.headers)


def is_public(request: Request) -> bool:
    return bool(caller(request)["public"])


def verify_limit(requested: int, public: bool, cfg: Optional[AgentConfig] = None) -> int:
    """Grounded checks allowed for one request: never above VERIFY_MAX, never above 2 for the public."""
    cap = (cfg or AgentConfig.from_env()).verify_max
    if public:
        cap = min(cap, PUBLIC_VERIFY_MAX)
    return max(0, min(int(requested), cap))


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = os.environ.get("AGENTS_DIR", str(REPO_ROOT))

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    web=os.environ.get("ADK_WEB", "true").lower() in ("1", "true", "yes"),
    allow_origins=["*"],
    # In-memory sessions: session events can carry Google Maps content, which may not be stored.
    use_local_storage=False,
    trace_to_cloud=os.environ.get("ADK_TRACE_TO_CLOUD", "false").lower() in ("1", "true", "yes"),
)


# Voice (Gemini Live) over ADK's /run_live WebSocket. VOICE_ENABLED=0 turns it off for everyone (the
# fallback if it misbehaves before the freeze); VOICE_PUBLIC=0 (the default) keeps it off for public
# callers, decided exactly as for HTTP. The gate runs before ADK accepts the socket, and fails closed.
VOICE_APP = "voice"
VOICE_CLOSE_CODE = 1008
VOICE_OFF_PUBLIC = "Voice is turned off on the public demo"
VOICE_OFF = "Voice is turned off"


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def voice_status() -> dict[str, bool]:
    present = (Path(AGENTS_DIR) / VOICE_APP / "__init__.py").exists()
    return {"voice": present and _flag("VOICE_ENABLED", "1"), "voice_public": _flag("VOICE_PUBLIC", "0")}


def voice_refusal(headers: Any) -> Optional[str]:
    """Why this caller may not open /run_live, or None if it may."""
    status = voice_status()
    if not status["voice"]:
        return VOICE_OFF
    if classify(headers)["public"] and not status["voice_public"]:
        return VOICE_OFF_PUBLIC
    return None


class VoiceGate:
    """ASGI middleware: accept-then-close /run_live with 1008 and a reason the client can show."""

    def __init__(self, inner: Any):
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "websocket" and scope.get("path", "").rstrip("/") == "/run_live":
            from starlette.datastructures import Headers

            reason = voice_refusal(Headers(scope=scope))
            if reason is not None:
                await receive()  # websocket.connect
                await send({"type": "websocket.accept"})
                await send({"type": "websocket.close", "code": VOICE_CLOSE_CODE, "reason": reason})
                return
        await self.inner(scope, receive, send)


app.add_middleware(VoiceGate)


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


@app.exception_handler(ApiError)
async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": exc.code, "detail": exc.detail}, status_code=exc.status)


async def _call(fn: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        return await fn()
    except ValueError as exc:
        raise ApiError(400, "bad_request", str(exc)) from exc
    except asyncio.TimeoutError as exc:
        raise ApiError(504, "timeout", "Upstream call timed out") from exc
    except Exception as exc:  # surfaced to the UI as JSON, logged by Cloud Run
        raise ApiError(500, "internal", f"{type(exc).__name__}: {str(exc)[:300]}") from exc


def _default_fips(value: Optional[str]) -> str:
    cfg = AgentConfig.from_env()
    return county_fips_or_error(value or cfg.default_county_fips, cfg)


class BriefRequest(BaseModel):
    county_fips: Optional[str] = None
    top_tracts: int = 5
    verify_top_n: int = 0


class VerifyRequest(BaseModel):
    shelter_ids: list[str]
    origin_geo_id: Optional[str] = None


class AllocateRequest(BaseModel):
    county_fips: Optional[str] = None
    shelters_down: Optional[list[str]] = None
    max_km: Optional[float] = None
    shelter_seeking_share: Optional[float] = None


class RehearseRequest(BaseModel):
    county_fips: Optional[str] = None
    scenario: str = "ian_2022"
    advisory: Optional[str] = None
    shelters_down: Optional[list[str]] = None


class PlanRequest(BaseModel):
    county_fips: Optional[str] = None
    kind: str = "allocation"  # allocation | rehearsal; the server computes it, the client never sends plan JSON
    created_by: str
    shelters_down: Optional[list[str]] = None
    advisory: Optional[str] = None


class ApproveRequest(BaseModel):
    approved_by: str


# ---------------------------------------------------------------------------
# Existing Contract Routes (/api/health, /api/map, /api/brief, /api/verify, ...)
# ---------------------------------------------------------------------------

@app.get("/api/health")
@app.get("/healthz")  # local only: Cloud Run's frontend reserves /healthz
async def healthz(request: Request) -> dict[str, Any]:
    who = caller(request)
    out: dict[str, Any] = {
        "ok": True,
        "status": "healthy",
        "contract_version": CONTRACT_VERSION,
        "app": APP_NAME,
        "caller": "public" if who["public"] else "signed_in",
        **voice_status(),
    }
    if not who["public"]:  # identity details only for signed-in callers
        out["caller_email"] = who["email"]
    return out


def county_map(cfg: AgentConfig, fips: str) -> dict[str, Any]:
    from .bq import render, run

    county = data.county(cfg, fips)
    shelters = data.county_shelters(cfg, fips)
    tracts = run(render(TRACT_NEED_SQL, tract_need=ops_table(cfg, "v_tract_need")), {"county_fips": fips}, cfg)
    try:
        care = data.care_facilities(cfg, fips)
    except Exception:
        care = []
    return payload("county_map", {
        "county_fips": fips,
        "county": {"fips": fips, "name": county.get("county_name"), "lat": county.get("lat"), "lng": county.get("lng")},
        "tracts": [
            {"geo_id": t["geo_id"], "lat": t.get("lat"), "lng": t.get("lon"), "total_pop": t.get("total_pop"),
             "households": t.get("households"), "households_no_vehicle": t.get("households_no_vehicle"),
             "share_no_vehicle": t.get("share_no_vehicle"), "est_persons_no_vehicle": t.get("est_persons_no_vehicle")}
            for t in tracts
        ],
        "shelters": [
            {"shelter_id": s["shelter_id"], "name": s.get("shelter_name"),
             "address": ", ".join(str(p) for p in (s.get("address_1"), s.get("city"), s.get("zip")) if p),
             "lat": s.get("latitude"), "lng": s.get("longitude"), "usage": s.get("usage"),
             "is_pre_landfall": bool(s.get("is_pre_landfall")),
             "capacity": None if s.get("capacity_status") == "UNRECORDED" else s.get("evacuation_capacity"),
             "capacity_status": s.get("capacity_status"), "capacity_implausible": bool(s.get("capacity_implausible")),
             "wheelchair": three_state(s.get("wheelchair_status")), "generator": three_state(s.get("generator_status")),
             "surge": three_state(s.get("surge_status")), "is_medical": bool(s.get("is_medical"))}
            for s in shelters
        ],
        "care_facilities": [
            {"facility_id": str(c.get("facility_id") or ""), "facility_name": c.get("facility_name"),
             "city": c.get("city"), "certified_beds": c.get("certified_beds"),
             "latitude": c.get("latitude"), "longitude": c.get("longitude"),
             "km_to_generator_recorded_shelter": c.get("km_to_generator_recorded_shelter"),
             "km_to_medical_generator_recorded_shelter": c.get("km_to_medical_generator_recorded_shelter")}
            for c in care
        ],
    })


@app.get("/api/map")
async def api_map(county_fips: Optional[str] = None) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        fips = _default_fips(county_fips)
        return await asyncio.to_thread(county_map, AgentConfig.from_env(), fips)

    return await _call(go)


@app.post("/api/brief")
async def api_brief(req: BriefRequest, request: Request) -> dict[str, Any]:
    """The brief never stores place_ids. Its Maps checks are capped by verify_limit(). Until
    build_readiness_brief takes a per-call limit, a public request whose cap is below VERIFY_MAX runs
    with Maps off; the UI does its live checks through /api/verify, which honours the cap exactly."""
    public = is_public(request)
    cfg = AgentConfig.from_env()
    limit = verify_limit(req.verify_top_n, public, cfg)

    async def go() -> dict[str, Any]:
        with_maps = limit > 0 and (limit >= cfg.verify_max or not public)
        out = await build_readiness_brief(_default_fips(req.county_fips), top_tracts=req.top_tracts,
                                          verify_with_maps=with_maps)
        out["verify_limit"] = limit if with_maps else 0
        return out

    return await _call(go)


@app.post("/api/verify")
async def api_verify(req: VerifyRequest, request: Request) -> dict[str, Any]:
    if not req.shelter_ids:
        raise ApiError(400, "bad_request", "shelter_ids is empty")
    public = is_public(request)
    limit = verify_limit(len(req.shelter_ids), public)
    ids, over = req.shelter_ids[:limit], req.shelter_ids[limit:]

    async def go() -> dict[str, Any]:
        out = await verify_shelters(ids, req.origin_geo_id or "")
        out["skipped_over_limit"] = list(out.get("skipped_over_limit") or []) + over
        # Strangers' verdicts are not written to BigQuery; signed-in checks keep only place_id.
        out["place_ids_stored"] = 0 if public else await asyncio.to_thread(plans.maybe_save_place_ids, out)
        return out

    return await _call(go)


@app.post("/api/allocate")
async def api_allocate(req: AllocateRequest) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        return await allocate_county(_default_fips(req.county_fips), shelters_down=req.shelters_down,
                                     max_km=req.max_km, shelter_seeking_share=req.shelter_seeking_share)

    return await _call(go)


@app.post("/api/rehearse")
async def api_rehearse(req: RehearseRequest) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        return await rehearse(_default_fips(req.county_fips), scenario=req.scenario, advisory=req.advisory,
                              shelters_down=req.shelters_down)

    return await _call(go)


def _no_public_writes(request: Request) -> None:
    if is_public(request):
        raise ApiError(403, "forbidden", "Plans are drafted and approved from the signed-in console only.")


@app.post("/api/plans")
async def api_plan_create(req: PlanRequest, request: Request) -> dict[str, Any]:
    """Draft a plan from a fresh solve. Drafts are never published until a person approves them."""
    _no_public_writes(request)

    async def go() -> dict[str, Any]:
        if not req.created_by.strip():
            raise ValueError("created_by is required")
        fips = _default_fips(req.county_fips)
        if req.kind == "rehearsal":
            body, mode = await rehearse(fips, advisory=req.advisory, shelters_down=req.shelters_down), "REHEARSAL"
        elif req.kind == "allocation":
            body, mode = await allocate_county(fips, shelters_down=req.shelters_down), "PLANNING"
        else:
            raise ValueError("kind must be allocation or rehearsal")
        return await asyncio.to_thread(plans.save_plan, AgentConfig.from_env(), body, req.created_by, mode)

    return await _call(go)


@app.post("/api/plans/{plan_id}/approve")
async def api_plan_approve(plan_id: str, req: ApproveRequest, request: Request) -> dict[str, Any]:
    _no_public_writes(request)
    return await _call(lambda: asyncio.to_thread(plans.approve_plan, AgentConfig.from_env(), plan_id, req.approved_by))


@app.get("/api/plans")
async def api_plan_list(county_fips: Optional[str] = None) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        fips = _default_fips(county_fips)
        return {"county_fips": fips, "plans": await asyncio.to_thread(plans.list_plans, AgentConfig.from_env(), fips)}

    return await _call(go)


@app.get("/api/storm/{scenario}")
async def api_storm(scenario: str) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ApiError(404, "not_found", f"Unknown scenario {scenario!r}")
    return await _call(lambda: storm_track(scenario))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
