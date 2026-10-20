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
AGENTS_DIR = os.environ.get("AGENTS_DIR", str(Path(__file__).resolve().parents[1]))

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    web=os.environ.get("ADK_WEB", "true").lower() in ("1", "true", "yes"),
    allow_origins=["*"],
    # In-memory sessions: session events can carry Google Maps content, which may not be stored.
    use_local_storage=False,
    trace_to_cloud=os.environ.get("ADK_TRACE_TO_CLOUD", "false").lower() in ("1", "true", "yes"),
)


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


@app.get("/api/health")
@app.get("/healthz")  # local only: Cloud Run's frontend reserves /healthz
async def healthz() -> dict[str, Any]:
    return {"ok": True, "contract_version": CONTRACT_VERSION, "app": APP_NAME}


def county_map(cfg: AgentConfig, fips: str) -> dict[str, Any]:
    from .bq import render, run

    county = data.county(cfg, fips)
    shelters = data.county_shelters(cfg, fips)
    tracts = run(render(TRACT_NEED_SQL, tract_need=ops_table(cfg, "v_tract_need")), {"county_fips": fips}, cfg)
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
    })


@app.get("/api/map")
async def api_map(county_fips: Optional[str] = None) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        fips = _default_fips(county_fips)
        return await asyncio.to_thread(county_map, AgentConfig.from_env(), fips)

    return await _call(go)


@app.post("/api/brief")
async def api_brief(req: BriefRequest) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        return await build_readiness_brief(_default_fips(req.county_fips), top_tracts=req.top_tracts,
                                           verify_with_maps=req.verify_top_n > 0)

    return await _call(go)


@app.post("/api/verify")
async def api_verify(req: VerifyRequest) -> dict[str, Any]:
    if not req.shelter_ids:
        raise ApiError(400, "bad_request", "shelter_ids is empty")
    async def go() -> dict[str, Any]:
        out = await verify_shelters(req.shelter_ids, req.origin_geo_id or "")
        out["place_ids_stored"] = await asyncio.to_thread(plans.maybe_save_place_ids, out)
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


@app.post("/api/plans")
async def api_plan_create(req: PlanRequest) -> dict[str, Any]:
    """Draft a plan from a fresh solve. Drafts are never published until a person approves them."""
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
async def api_plan_approve(plan_id: str, req: ApproveRequest) -> dict[str, Any]:
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
