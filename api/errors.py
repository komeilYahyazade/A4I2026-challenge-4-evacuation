"""One error shape for every /v1 route: {"error": {"code", "message", "retryable"}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def _body(code: str, message: str, retryable: bool) -> dict:
    return {"error": {"code": code, "message": message, "retryable": retryable}}


def from_exception(e: Exception) -> ApiError:
    """Map upstream failures onto the contract: model quota -> 503 retryable, timeouts -> 504."""
    if isinstance(e, ApiError):
        return e
    if isinstance(e, TimeoutError):
        return ApiError(504, "upstream_timeout", "An upstream call timed out.", retryable=True)
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (429, 503) or "RESOURCE_EXHAUSTED" in str(e):
        return ApiError(503, "model_quota", "The model is over quota. Try again shortly.", retryable=True)
    return ApiError(503, "upstream_error", f"{type(e).__name__}: {str(e)[:200]}", retryable=True)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, e: ApiError):
        return JSONResponse(_body(e.code, e.message, e.retryable), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(req: Request, e: RequestValidationError):
        # ADK's own routes keep FastAPI's default shape; only /v1 speaks the contract.
        if not req.url.path.startswith("/v1"):
            return await request_validation_exception_handler(req, e)
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", []))
        return JSONResponse(_body("invalid_request", f"{where}: {first.get('msg', 'invalid')}", False),
                            status_code=400)
