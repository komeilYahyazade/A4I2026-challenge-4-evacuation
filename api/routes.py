"""The /v1 routes (BUILD_SPEC.md section 10). Each route serves its mock until it is live.

Live implementations are imported inside the handler so a mock-only deploy never loads BigQuery
or the agents.
"""

from fastapi import APIRouter

from api import config, mocks
from api.errors import ApiError
from contract import schemas

router = APIRouter(prefix="/v1")


def _check_fips(county_fips: str) -> None:
    if not (len(county_fips) == 5 and county_fips.isdigit()):
        raise ApiError(400, "invalid_county_fips", f"{county_fips!r} is not a 5-digit county FIPS")


@router.get("/health", response_model=schemas.Health)
def health():
    return {"status": "ok", "mode": config.mode(), "model": config.MODEL, "state": config.STATE,
            "dataset": config.DATASET, "version": config.VERSION}


@router.get("/counties", response_model=schemas.CountiesResponse)
def counties():
    if not config.is_live("counties"):
        return mocks.mock("counties.json")
    from api import data
    return data.counties()


@router.get("/counties/{county_fips}/map", response_model=schemas.CountyMap)
def county_map(county_fips: str):
    _check_fips(county_fips)
    if not config.is_live("map"):
        return mocks.mock("county_map.json")
    from api import data
    return data.county_map(county_fips)


@router.get("/counties/{county_fips}/insights", response_model=schemas.CountyInsights)
def county_insights(county_fips: str, reachable_km: float = 3.0):
    _check_fips(county_fips)
    if not 0.5 <= reachable_km <= 25:
        raise ApiError(400, "invalid_reachable_km", "reachable_km must be between 0.5 and 25")
    if not config.is_live("insights"):
        return mocks.mock("insights.json")
    from api import data
    return data.insights(county_fips, round(reachable_km, 1))


@router.get("/shelters/{shelter_id}", response_model=schemas.ShelterDetail)
def shelter(shelter_id: str):
    if not config.is_live("shelter"):
        return mocks.mock("shelter.json")
    from api import data
    return data.shelter_detail(shelter_id)


@router.post("/shelters/{shelter_id}/verify", response_model=schemas.ShelterVerification)
async def verify(shelter_id: str, req: schemas.VerifyRequest | None = None):
    req = req or schemas.VerifyRequest()
    if not config.is_live("verify"):
        return mocks.mock("verify.json")
    from api import data
    return await data.verify(shelter_id, req)


@router.post("/shelters/{shelter_id}/assess", response_model=schemas.AssessResponse)
async def assess(shelter_id: str, req: schemas.AssessRequest):
    if not config.is_live("assess"):
        body = mocks.mock("assess.json")
        body["shelter_id"] = shelter_id
        return body
    from api import assess as assess_impl
    return await assess_impl.assess(shelter_id, req)


@router.post("/briefs", response_model=schemas.BriefAccepted, status_code=202)
async def create_brief(req: schemas.BriefRequest):
    _check_fips(req.county_fips)
    if not config.is_live("briefs"):
        return mocks.start_brief()
    from api import briefs
    return await briefs.start(req)


@router.get("/briefs/{brief_id}", response_model=schemas.BriefStatus)
def get_brief(brief_id: str):
    if not config.is_live("briefs"):
        body = mocks.brief_status(brief_id)
    else:
        from api import briefs
        body = briefs.status(brief_id)
    if body is None:
        raise ApiError(404, "brief_not_found", f"No brief {brief_id} (jobs live in memory and expire)")
    return body


@router.post("/briefs/{brief_id}/refresh", response_model=schemas.BriefStatus)
async def refresh_brief(brief_id: str):
    """Re-assemble a finished brief with the latest field assessments. No model call."""
    if not config.is_live("briefs"):
        body = mocks.brief_status(brief_id)
        if body is None:
            raise ApiError(404, "brief_not_found", f"No brief {brief_id} (jobs live in memory and expire)")
        return body
    from api import briefs
    return await briefs.refresh(brief_id)


@router.get("/briefs/{brief_id}/audit", response_model=schemas.AuditResponse)
def audit(brief_id: str):
    if not config.is_live("audit"):
        body = mocks.mock("audit.json")
        body["brief_id"] = brief_id
        return body
    from api import briefs
    return briefs.audit(brief_id)


@router.post("/chat", response_model=schemas.ChatResponse)
async def chat(req: schemas.ChatRequest):
    if not config.is_live("chat"):
        return mocks.mock("chat.json")
    from api import chat as chat_impl
    return await chat_impl.chat(req)
