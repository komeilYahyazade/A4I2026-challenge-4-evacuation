"""readiness-web: serves the React build and forwards /v1 to the private readiness-api.

The browser never holds Google credentials: this service calls the API with its own service
account's ID token. The Maps JavaScript API key (browser key, referrer-restricted) reaches the page
through /config.js from an env var, never from the bundle or git.

Run locally: ../.venv/bin/python -m uvicorn server:app --port 8090   (API_BASE_URL defaults to :8080)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")  # local dev only

API = os.environ.get("API_BASE_URL", "http://localhost:8080").rstrip("/")
DIST = Path(os.environ.get("DIST_DIR", HERE / "dist"))
CONFIG = {"MAPS_API_KEY": os.environ.get("MAPS_API_KEY", ""), "MAP_ID": os.environ.get("MAP_ID", "DEMO_MAP_ID")}
TIMEOUT_S = 310
FORWARD_HEADERS = ("content-type", "accept")

app = FastAPI(title="readiness-web", docs_url=None, redoc_url=None)
_client = httpx.AsyncClient(timeout=TIMEOUT_S)
_token: tuple[float, str] | None = None
_token_lock = asyncio.Lock()


def _fetch_id_token() -> str | None:
    import google.auth.transport.requests
    from google.oauth2 import id_token
    return id_token.fetch_id_token(google.auth.transport.requests.Request(), API)


async def _auth_header() -> dict[str, str]:
    """ID token for a private Cloud Run API; none for a local http:// API."""
    global _token
    if os.environ.get("API_ID_TOKEN"):
        return {"Authorization": f"Bearer {os.environ['API_ID_TOKEN']}"}
    if not API.startswith("https://"):
        return {}
    async with _token_lock:
        if not _token or time.time() - _token[0] > 45 * 60:
            _token = (time.time(), await asyncio.to_thread(_fetch_id_token))
    return {"Authorization": f"Bearer {_token[1]}"}


@app.get("/config.js", include_in_schema=False)
def config_js() -> Response:
    return Response(f"window.__CONFIG__ = {json.dumps(CONFIG)};", media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})


@app.get("/_health", include_in_schema=False)  # Cloud Run reserves /healthz
def healthz() -> dict:
    return {"ok": True, "api": API, "maps_key": bool(CONFIG["MAPS_API_KEY"])}


@app.api_route("/v1/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def proxy(path: str, request: Request) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARD_HEADERS}
    try:
        headers.update(await _auth_header())
        r = await _client.request(request.method, f"{API}/v1/{path}", params=request.query_params,
                                  content=await request.body(), headers=headers)
    except httpx.TimeoutException:
        return JSONResponse({"error": {"code": "upstream_timeout", "message": "The API took too long.", "retryable": True}}, 504)
    except Exception as e:  # unreachable API, token failure
        return JSONResponse({"error": {"code": "api_unreachable", "message": type(e).__name__, "retryable": True}}, 503)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> Response:
    f = DIST / path
    if path and f.is_file() and DIST in f.resolve().parents:
        return FileResponse(f)
    index = DIST / "index.html"
    if not index.is_file():
        return JSONResponse({"error": "web build missing: run npm run build"}, 503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
