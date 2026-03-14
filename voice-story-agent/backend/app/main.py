"""
Voice Story Agent — FastAPI application entry point.

Local development:
    cd backend
    cp .env.example .env          # fill in GCP_PROJECT_ID when ready
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

The server starts and GET /health works with no credentials set.
GCP-dependent endpoints will return 500 with a descriptive message until
GCP_PROJECT_ID is set and credentials are configured.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import pages, sessions
from app.websocket import story_ws

logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Voice Story Agent for Children",
    description=(
        "Real-time voice storytelling API. "
        "REST for session lifecycle; WebSocket /ws/story/{session_id} for bidi-streaming."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception handlers ────────────────────────────────────────────────────────


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Normalise all HTTPException responses to {"error": "..."} envelope."""
    if isinstance(exc.detail, dict):
        content = exc.detail
    else:
        content = {"error": exc.detail}
    return JSONResponse(status_code=exc.status_code, content=content)


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(sessions.router)
app.include_router(pages.router)
app.include_router(story_ws.router)

# ── Lifecycle ─────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def _startup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s  %(name)s  %(message)s",
    )
    for warning in settings.startup_warnings():
        logger.warning("⚠️  %s", warning)
    logger.info(
        "Voice Story Agent started. "
        "Docs: http://localhost:8000/docs  |  Health: http://localhost:8000/health"
    )


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """
    Liveness probe.  Always returns 200 OK regardless of credential state.
    Cloud Run and local dev both use this endpoint.
    """
    return {"status": "ok"}
