"""
WebSocket handler for /ws/story/{session_id}.

Protocol
--------
T-012  Connect, token validation, ping/pong, session_start stub, unknown-type
       error.

T-015  Wire VoiceSessionService into the handler:
         1. session_start → VoiceSessionService.start + _turn_loop background
            task + voice_session_ready event.
         2. Binary frame  → VoiceSessionService.send_audio (silently ignored
            if the voice session has not yet started).
         3. _turn_loop    → iterates VoiceSessionService.stream_turns; emits
            `transcript` JSON event for every VoiceTurn and a binary WebSocket
            frame for agent audio; routes final user turns to the pipeline stub
            via _route_user_turn.
         4. transcript_input text message → synthetic VoiceTurn injected into
            _route_user_turn, producing a `turn_detected` event.

Token validation (stub):
    Any non-empty, non-whitespace token string is accepted.
    Real JWT verification is wired in a later task.

All outbound JSON frames go through emit() to guarantee the
{"type": "..."} envelope.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.dependencies import get_store, get_voice_service
from app.exceptions import SessionNotFoundError, VoiceSessionError, VoiceSessionNotFoundError
from app.services.adk_voice_service import VoiceTurn, VoiceSessionService
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)

router = APIRouter()

# System prompt used when the voice session is opened during the setup phase.
_SETUP_SYSTEM_PROMPT = (
    "You are a warm, imaginative storytelling assistant for children aged 4–10. "
    "Help the child choose a protagonist, a setting, and a tone for their "
    "personalised bedtime story. Ask gentle, open-ended questions one at a time. "
    "Keep each response short (≤ 2 sentences) and encouraging."
)


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
# Pipeline routing (T-015 stub)
# ---------------------------------------------------------------------------


async def _route_user_turn(
    ws: WebSocket, turn: VoiceTurn, session_id: str
) -> None:
    """
    Route a final user turn to the appropriate generation pipeline.

    T-015 stub: logs the turn and emits a `turn_detected` event.
    Full pipeline routing (setup vs. steering vs. narration) is wired in T-016+.
    """
    turn_id = str(uuid.uuid4())
    logger.info(
        "Routing user turn (session=%s, turn_id=%s, text=%.80r)",
        session_id,
        turn_id,
        turn.transcript,
    )
    await emit(
        ws,
        "turn_detected",
        turn_id=turn_id,
        text=turn.transcript,
        phase="setup",
    )


# ---------------------------------------------------------------------------
# Turn streaming background task
# ---------------------------------------------------------------------------


async def _turn_loop(
    session_id: str,
    ws: WebSocket,
    voice_svc: VoiceSessionService,
) -> None:
    """
    Background task: relay VoiceTurn events to the WebSocket client.

    For each turn received from VoiceSessionService.stream_turns:
      - Emit a `transcript` JSON event (role, text, is_final, phase, turn_id).
      - If the turn is from the agent and carries audio bytes, send a binary
        WebSocket frame so the browser can play audio directly.
      - If the turn is a final user turn, forward it to _route_user_turn.

    The task is cancelled when the WebSocket handler exits its finally block.
    """
    try:
        async for turn in voice_svc.stream_turns(session_id):
            turn_id = str(uuid.uuid4())

            await emit(
                ws,
                "transcript",
                role=turn.role,
                text=turn.transcript,
                is_final=turn.is_final,
                phase="setup",
                turn_id=turn_id,
            )

            # Agent audio → binary WebSocket frame for client playback.
            if turn.role == "agent" and turn.audio_bytes:
                await ws.send_bytes(turn.audio_bytes)

            # Final user turn → pipeline routing stub.
            if turn.is_final and turn.role == "user":
                await _route_user_turn(ws, turn, session_id)

    except asyncio.CancelledError:
        logger.debug("_turn_loop cancelled (session=%s)", session_id)
        raise
    except VoiceSessionNotFoundError:
        logger.warning(
            "_turn_loop: voice session not found (session=%s)", session_id
        )
    except Exception as exc:
        logger.error("_turn_loop error (session=%s): %s", session_id, exc)
        try:
            await emit(ws, "session_error", code="stream_error")
        except Exception:
            pass  # WebSocket may already be closed


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws/story/{session_id}")
async def story_websocket(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(default=None),
    store: SessionStore = Depends(get_store),
    voice_svc: VoiceSessionService = Depends(get_voice_service),
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
    turn_loop_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    try:
        while True:
            try:
                msg = await websocket.receive()
            except WebSocketDisconnect as exc:
                logger.info(
                    "WS closed (session=%s, code=%s)", session_id, exc.code
                )
                break
            except Exception as exc:
                logger.error(
                    "WS receive error (session=%s): %s", session_id, exc
                )
                break

            # Starlette may surface disconnect as a message instead of an exception.
            if msg.get("type") == "websocket.disconnect":
                logger.info(
                    "WS disconnect message (session=%s, code=%s)",
                    session_id,
                    msg.get("code"),
                )
                break

            # ── Binary frame: raw PCM audio from the child's microphone ──
            raw_bytes = msg.get("bytes")
            if raw_bytes is not None:
                try:
                    await voice_svc.send_audio(session_id, raw_bytes)
                except VoiceSessionNotFoundError:
                    # Audio arriving before session_start is silently ignored.
                    pass
                except VoiceSessionError as exc:
                    logger.error(
                        "send_audio error (session=%s): %s", session_id, exc
                    )
                continue

            # ── Text frame: JSON control message ─────────────────────────
            raw_text = msg.get("text")
            if not raw_text:
                continue

            try:
                data = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError):
                data = {}

            msg_type = data.get("type") if isinstance(data, dict) else None

            if msg_type == "ping":
                await emit(websocket, "pong")

            elif msg_type == "session_start":
                try:
                    await voice_svc.start(session_id, _SETUP_SYSTEM_PROMPT)
                except VoiceSessionError as exc:
                    logger.error(
                        "Voice session start failed (session=%s): %s",
                        session_id,
                        exc,
                    )
                    await emit(websocket, "session_error", code="voice_start_failed")
                else:
                    # Launch the turn-streaming background task once per session.
                    if turn_loop_task is None or turn_loop_task.done():
                        turn_loop_task = asyncio.create_task(
                            _turn_loop(session_id, websocket, voice_svc)
                        )
                    await emit(websocket, "voice_session_ready")

            elif msg_type == "transcript_input":
                # Text typed/pasted by the user — treated as a complete utterance.
                text = (
                    str(data.get("text", "")) if isinstance(data, dict) else ""
                )
                synthetic_turn = VoiceTurn(
                    role="user",
                    transcript=text,
                    audio_bytes=None,
                    is_final=True,
                )
                await _route_user_turn(websocket, synthetic_turn, session_id)

            else:
                await emit(websocket, "session_error", code="unknown_message_type")

    finally:
        # Cancel the background turn-streaming task if still running.
        if turn_loop_task is not None and not turn_loop_task.done():
            turn_loop_task.cancel()
            try:
                await turn_loop_task
            except (asyncio.CancelledError, Exception):
                pass

        # Always close the ADK voice session to release SDK resources.
        await voice_svc.end(session_id)
        logger.info("WS handler cleaned up (session=%s)", session_id)
