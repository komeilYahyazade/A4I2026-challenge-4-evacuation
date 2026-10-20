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
from urllib.parse import urlencode

import httpx
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")  # local dev only

API = os.environ.get("API_BASE_URL", "http://localhost:8080").rstrip("/")
DIST = Path(os.environ.get("DIST_DIR", HERE / "dist"))
VOICE_ENABLED = os.environ.get("VOICE_ENABLED", "1") == "1"
CONFIG = {"MAPS_API_KEY": os.environ.get("MAPS_API_KEY", ""), "MAP_ID": os.environ.get("MAP_ID", "DEMO_MAP_ID"),
          "VOICE_ENABLED": VOICE_ENABLED}
# Voice (Gemini Live over ADK /run_live), ported from main's server.mjs limits: one app only, a
# 3-minute cap per session and at most 2 live at once (in memory: run with --max-instances 1).
VOICE_APP = "readiness_voice"
VOICE_MAX_S = 180
VOICE_MAX_LIVE = 2
_voice_live = 0
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


@app.post("/apps/readiness_voice/users/{user_id}/sessions", include_in_schema=False)
async def voice_session(user_id: str, request: Request) -> Response:
    """Create an ADK session for the voice app only; nothing else under /apps is forwarded."""
    if not VOICE_ENABLED:
        return JSONResponse({"error": {"code": "voice_off", "message": "Voice is turned off.", "retryable": False}}, 403)
    try:
        r = await _client.post(f"{API}/apps/{VOICE_APP}/users/{user_id}/sessions", content=await request.body(),
                               headers={"content-type": "application/json", **(await _auth_header())})
    except Exception as e:
        return JSONResponse({"error": {"code": "api_unreachable", "message": type(e).__name__, "retryable": True}}, 503)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


@app.websocket("/run_live")
async def run_live(ws: WebSocket) -> None:
    """Bridge the browser's Gemini Live socket to the private API's /run_live, with our ID token."""
    global _voice_live
    await ws.accept()
    if not VOICE_ENABLED:
        await ws.close(code=1008, reason="Voice is turned off")
        return
    if _voice_live >= VOICE_MAX_LIVE:
        await ws.close(code=4008, reason="Voice limit reached: two live sessions at once. Try again shortly.")
        return
    params = {**dict(ws.query_params), "app_name": VOICE_APP}
    upstream = API.replace("https://", "wss://").replace("http://", "ws://") + "/run_live?" + urlencode(params)
    _voice_live += 1
    try:
        async with websockets.connect(upstream, additional_headers=await _auth_header(), max_size=None,
                                      open_timeout=20) as up:
            async def browser_to_api():
                while True:
                    await up.send(await ws.receive_text())

            async def api_to_browser():
                async for msg in up:
                    await ws.send_text(msg if isinstance(msg, str) else msg.decode())

            tasks = [asyncio.create_task(browser_to_api()), asyncio.create_task(api_to_browser())]
            done, pending = await asyncio.wait(tasks, timeout=VOICE_MAX_S, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            reason = "Voice session limit reached (3 minutes)." if not done else "Voice session ended."
            await up.close()
            await ws.close(code=1000, reason=reason)
    except WebSocketDisconnect:
        pass
    except Exception as e:  # upstream refused or dropped: tell the browser why
        try:
            await ws.close(code=1011, reason=f"Voice service error: {type(e).__name__}"[:120])
        except Exception:
            pass
    finally:
        _voice_live -= 1


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
