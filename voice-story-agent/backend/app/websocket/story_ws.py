"""
WebSocket handler skeleton for /ws/story/{session_id}.

Protocol (T-012):
  1. On connect   — validate Bearer token from ?token= query param.
                    Reject with close code 4001 if token is missing/empty.
                    Emit `connected` with current session_status.
  2. Dispatch     — route incoming text frames by "type":
                      ping          → pong
                      session_start → voice_session_ready  (stub; ADK wired in T-013)
                      <unknown>     → session_error code=unknown_message_type
  3. On close     — log session_id + close reason.

All outbound frames go through `emit()` to guarantee the {"type": "..."} envelope.

Token validation (stub):
    Any non-empty, non-whitespace token string is accepted.
    Real JWT verification is wired in a later task.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.dependencies import get_store
from app.exceptions import SessionNotFoundError
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Emit helper
# ---------------------------------------------------------------------------


async def emit(ws: WebSocket, event_type: str, **fields: object) -> None:
    """Send a JSON frame of the form {"type": event_type, ...fields}."""
    await ws.send_json({"type": event_type, **fields})


# ---------------------------------------------------------------------------
# Token validation (stub)
# ---------------------------------------------------------------------------


def _is_valid_token(token: Optional[str]) -> bool:
    """Return True for any non-empty, non-whitespace token string."""
    return bool(token and token.strip())


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/story/{session_id}")
async def story_websocket(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(default=None),
    store: SessionStore = Depends(get_store),
) -> None:
    """
    Bidi-streaming WebSocket for a single story session.

    Connect:  wss://{host}/ws/story/{session_id}?token={bearer_token}
    """
    await websocket.accept()

    # ── Token validation ──────────────────────────────────────────────────
    if not _is_valid_token(token):
        logger.warning(
            "WS rejected — missing or empty token (session=%s)", session_id
        )
        await websocket.close(code=4001)
        return

    # ── Session lookup ────────────────────────────────────────────────────
    try:
        session = await store.get_session(session_id)
    except SessionNotFoundError:
        logger.warning("WS rejected — session not found (session=%s)", session_id)
        await emit(websocket, "session_error", code="session_not_found")
        await websocket.close(code=4001)
        return

    # ── Emit connected ────────────────────────────────────────────────────
    await emit(websocket, "connected", session_status=session.status)

    # ── Message dispatch loop ─────────────────────────────────────────────
    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect as exc:
            logger.info(
                "WS closed (session=%s, code=%s)", session_id, exc.code
            )
            break
        except Exception as exc:
            logger.error("WS receive error (session=%s): %s", session_id, exc)
            break

        msg_type = data.get("type") if isinstance(data, dict) else None

        if msg_type == "ping":
            await emit(websocket, "pong")
        elif msg_type == "session_start":
            await emit(websocket, "voice_session_ready")
        else:
            await emit(
                websocket, "session_error", code="unknown_message_type"
            )
