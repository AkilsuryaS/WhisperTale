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

T-017  Safety gate wired into every final user turn before pipeline routing:
         1. SafetyService.evaluate() called on every is_final=True user turn.
         2. If safe=False:
              a. Emit `safety_rewrite` (decision_id, turn_id, detected_category,
                 proposed_rewrite, phase).
              b. Call VoiceSessionService.speak(proposed_rewrite) so the child
                 hears the safe alternative.
              c. Set gate into awaiting-acknowledgement state.
              d. Next final user turn (from ADK stream or transcript_input) is
                 treated as acceptance → persist SafetyDecision(user_accepted=True),
                 update ContentPolicy, emit `safety_accepted`.
         3. If safe=True: proceed to normal routing (_route_user_turn).
         4. WebSocket disconnect while gate is pending →
              SafetyDecision(user_accepted=False) persisted; session status set
              to `error` when phase=setup.

T-020  Setup parameter extraction wired into _route_user_turn:
         _route_user_turn delegates to SetupHandler.handle() which:
           1. Calls Gemini Flash to extract protagonist, setting, tone.
           2. Emits `story_brief_updated` for each newly confirmed parameter.
           3. Asks a follow-up question if parameters are still missing
              (and fewer than MAX_SETUP_TURNS have been used).
           4. On all-confirmed (or turn limit reached):
              a. Persists StoryBrief.
              b. Calls StoryPlannerService.create_arc → persists arc beats.
              c. Emits `story_brief_confirmed`.
              d. Calls CharacterBibleService.initialise.
              e. Emits `character_bible_ready`.
              f. Updates Session.status → generating.

T-026  Page generation loop wired after character_bible_ready:
         After setup completes (character_bible_ready emitted), a background
         task runs the 5-page loop:
           for page_number in 1..5:
               await run_page(...)                   # generates text, image, TTS
               emit page_complete                     # fired by run_page
               open steering window (10 s timer)      # await asyncio.sleep(10)
           emit story_complete
           update Session.status = complete

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.dependencies import (
    get_character_bible_svc,
    get_image_svc,
    get_media_svc,
    get_safety_service,
    get_setup_handler,
    get_store,
    get_story_planner,
    get_tts_svc,
    get_voice_service,
)
from app.websocket.setup_handler import SetupHandler, SetupState
from app.exceptions import (
    SessionNotFoundError,
    VoiceSessionError,
    VoiceSessionNotFoundError,
)
from app.models.safety import (
    SAFE_FALLBACK_REWRITE,
    SafetyCategory,
    SafetyDecision,
    SafetyPhase,
)
from app.models.session import SessionStatus
from app.services.adk_voice_service import VoiceTurn, VoiceSessionService
from app.services.character_bible_service import CharacterBibleService
from app.services.image_generation import ImageGenerationService
from app.services.media_persistence import MediaPersistenceService
from app.services.safety_service import SafetyService
from app.services.session_store import SessionStore
from app.services.story_planner import StoryPlannerService
from app.services.tts_service import TTSService

logger = logging.getLogger(__name__)

router = APIRouter()

# System prompt used when the voice session is opened during the setup phase.
_SETUP_SYSTEM_PROMPT = (
    "You are a warm, imaginative storytelling assistant for children aged 4–10. "
    "Help the child choose a protagonist, a setting, and a tone for their "
    "personalised bedtime story. Ask gentle, open-ended questions one at a time. "
    "Keep each response short (≤ 2 sentences) and encouraging."
)

# Duration (seconds) for the steering window between pages.
_STEERING_WINDOW_SECONDS = 10


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
# Safety gate state (per-session, mutable)
# ---------------------------------------------------------------------------


@dataclass
class _SafetyGate:
    """
    Per-session safety gate state shared between _turn_loop and the main
    WebSocket handler.

    When `awaiting_ack` is True the next final user turn (from the ADK stream
    or a transcript_input message) is treated as acceptance of the safety
    rewrite rather than a new story premise.

    asyncio is single-threaded and cooperative, so reads/writes to this object
    inside awaited coroutines are safe without additional locking.
    """

    awaiting_ack: bool = False
    decision_id: uuid.UUID = field(default_factory=uuid.uuid4)
    turn_uuid: Optional[uuid.UUID] = None
    raw_input: str = ""
    category: Optional[SafetyCategory] = None
    proposed_rewrite: str = ""
    triggered_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Safety gate helpers
# ---------------------------------------------------------------------------


async def _begin_safety_rewrite(
    ws: WebSocket,
    turn: VoiceTurn,
    turn_id: str,
    session_id: str,
    voice_svc: VoiceSessionService,
    gate: _SafetyGate,
    proposed_rewrite: str,
    category: Optional[SafetyCategory],
) -> None:
    """
    Arm the safety gate, emit `safety_rewrite`, and have the agent speak the
    child-safe alternative premise.
    """
    gate.awaiting_ack = True
    gate.decision_id = uuid.uuid4()
    gate.turn_uuid = uuid.UUID(turn_id)
    gate.raw_input = turn.transcript
    gate.category = category
    gate.proposed_rewrite = proposed_rewrite
    gate.triggered_at = datetime.now(timezone.utc)

    await emit(
        ws,
        "safety_rewrite",
        decision_id=str(gate.decision_id),
        turn_id=turn_id,
        detected_category=category.value if category else None,
        proposed_rewrite=proposed_rewrite,
        phase="setup",
    )

    try:
        await voice_svc.speak(session_id, proposed_rewrite)
    except VoiceSessionError as exc:
        logger.error(
            "speak failed during safety rewrite (session=%s): %s", session_id, exc
        )


async def _complete_safety_ack(
    ws: WebSocket,
    session_id: str,
    store: SessionStore,
    gate: _SafetyGate,
) -> None:
    """
    Process a safety-gate acknowledgement: disarm the gate, persist
    SafetyDecision(user_accepted=True), append the exclusion to ContentPolicy,
    and emit `safety_accepted`.
    """
    gate.awaiting_ack = False

    exclusion = (
        f"no {gate.category.value}" if gate.category else "no unsafe content"
    )

    sd = SafetyDecision(
        decision_id=gate.decision_id,
        turn_id=gate.turn_uuid,
        phase=SafetyPhase.setup,
        raw_input=gate.raw_input,
        detected_category=gate.category,
        proposed_rewrite=gate.proposed_rewrite,
        user_accepted=True,
        final_premise=gate.proposed_rewrite,
        exclusion_added=exclusion,
        triggered_at=gate.triggered_at,
    )

    try:
        await store.save_safety_decision(session_id, sd)
        bible = await store.get_character_bible(session_id)
        if bible is not None:
            updated_exclusions = list(bible.content_policy.exclusions) + [exclusion]
            updated_decision_ids = list(
                bible.content_policy.derived_from_safety_decisions
            ) + [str(sd.decision_id)]
            await store.update_character_bible_field(
                session_id, "content_policy.exclusions", updated_exclusions
            )
            await store.update_character_bible_field(
                session_id,
                "content_policy.derived_from_safety_decisions",
                updated_decision_ids,
            )
    except Exception as exc:
        logger.error(
            "Failed to persist safety decision or update ContentPolicy "
            "(session=%s, error_type=%s)",
            session_id,
            type(exc).__name__,
        )

    await emit(
        ws,
        "safety_accepted",
        decision_id=str(sd.decision_id),
        final_premise=sd.proposed_rewrite,
    )


async def _persist_abandoned_safety_decision(
    session_id: str,
    store: SessionStore,
    gate: _SafetyGate,
) -> None:
    """
    Called when the WebSocket closes while the safety gate is still open.
    Persists SafetyDecision(user_accepted=False) and marks the session as
    `error` (setup-phase safety abandonment).
    """
    if not gate.awaiting_ack:
        return

    sd = SafetyDecision(
        decision_id=gate.decision_id,
        turn_id=gate.turn_uuid,
        phase=SafetyPhase.setup,
        raw_input=gate.raw_input,
        detected_category=gate.category,
        proposed_rewrite=gate.proposed_rewrite,
        user_accepted=False,
        final_premise=None,
        exclusion_added=None,
        triggered_at=gate.triggered_at,
    )

    try:
        await store.save_safety_decision(session_id, sd)
        await store.update_session_status(session_id, SessionStatus.error)
    except Exception as exc:
        logger.error(
            "Failed to persist abandoned safety decision "
            "(session=%s, error_type=%s)",
            session_id,
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Pipeline routing
# ---------------------------------------------------------------------------


async def _route_user_turn(
    ws: WebSocket,
    turn: VoiceTurn,
    session_id: str,
    voice_svc: VoiceSessionService,
    setup_handler: SetupHandler,
    setup_state: SetupState,
    store: SessionStore,
) -> None:
    """
    Route a final, safety-cleared user turn to the generation pipeline.

    T-020: delegates to SetupHandler.handle() which extracts story parameters,
    emits story_brief_updated / story_brief_confirmed / character_bible_ready,
    and orchestrates StoryPlannerService + CharacterBibleService.
    """
    await setup_handler.handle(ws, turn, session_id, voice_svc, setup_state, store)


# ---------------------------------------------------------------------------
# Page generation loop (T-026)
# ---------------------------------------------------------------------------


async def _page_generation_loop(
    ws: WebSocket,
    session_id: str,
    store: SessionStore,
    story_planner: StoryPlannerService,
    character_bible_svc: CharacterBibleService,
    image_svc: ImageGenerationService,
    tts_svc: TTSService,
    media_svc: MediaPersistenceService,
    steering_window_seconds: float = _STEERING_WINDOW_SECONDS,
) -> None:
    """
    Run the full 5-page story generation loop after setup is complete.

    Sequence for each page:
      1. Fetch the current session to get the story arc and page history.
      2. Call run_page (emits page events via the WebSocket).
      3. Open a steering window (sleep for steering_window_seconds) to allow
         voice commands before the next page begins.
    After all 5 pages: emit story_complete, update session status to complete.
    """
    from app.websocket.page_orchestrator import run_page

    async def ws_emit(event_type: str, **fields: object) -> None:
        try:
            await emit(ws, event_type, **fields)
        except Exception:
            pass  # WebSocket may have closed during generation

    try:
        for page_number in range(1, 6):
            # Re-fetch session to get latest story_arc and current_page
            try:
                session = await store.get_session(session_id)
            except Exception as exc:
                logger.error(
                    "_page_generation_loop: get_session failed (session=%s, page=%d): %s",
                    session_id,
                    page_number,
                    exc,
                )
                break

            if not session.story_arc or len(session.story_arc) < page_number:
                logger.error(
                    "_page_generation_loop: story arc too short "
                    "(session=%s, page=%d, arc_len=%d)",
                    session_id,
                    page_number,
                    len(session.story_arc),
                )
                break

            beat = session.story_arc[page_number - 1]

            # Build page_history from text of prior completed pages
            page_history: list[str] = []
            for pn in range(1, page_number):
                pg = await store.get_page(session_id, pn)
                if pg is not None and pg.text:
                    sentences = pg.text.split(".")
                    summary = sentences[0].strip() + "." if sentences else pg.text
                    page_history.append(summary)

            await run_page(
                session_id=session_id,
                page_number=page_number,
                beat=beat,
                page_history=page_history,
                emit=ws_emit,
                story_planner=story_planner,
                character_bible_svc=character_bible_svc,
                image_svc=image_svc,
                tts_svc=tts_svc,
                media_svc=media_svc,
                session_store=store,
            )

            # Steering window: allow voice commands before next page
            if page_number < 5:
                await ws_emit(
                    "steering_window_open",
                    page=page_number,
                    duration_seconds=steering_window_seconds,
                )
                await asyncio.sleep(steering_window_seconds)
                await ws_emit("steering_window_closed", page=page_number)

        # All 5 pages done — emit story_complete and update status
        await ws_emit("story_complete", session_id=session_id)
        try:
            await store.update_session_status(session_id, SessionStatus.complete)
        except Exception as exc:
            logger.error(
                "_page_generation_loop: update_session_status failed (session=%s): %s",
                session_id,
                exc,
            )

    except asyncio.CancelledError:
        logger.debug("_page_generation_loop cancelled (session=%s)", session_id)
        raise
    except Exception as exc:
        logger.error(
            "_page_generation_loop: unexpected error (session=%s): %s",
            session_id,
            exc,
        )
        try:
            await ws_emit("session_error", code="page_generation_error")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Turn streaming background task
# ---------------------------------------------------------------------------


async def _turn_loop(
    session_id: str,
    ws: WebSocket,
    voice_svc: VoiceSessionService,
    safety_svc: SafetyService,
    store: SessionStore,
    safety_gate: _SafetyGate,
    setup_handler: SetupHandler,
    setup_state: SetupState,
    story_planner: StoryPlannerService,
    character_bible_svc: CharacterBibleService,
    image_svc: ImageGenerationService,
    tts_svc: TTSService,
    media_svc: MediaPersistenceService,
) -> None:
    """
    Background task: relay VoiceTurn events from the ADK stream to the client.

    For each turn:
      - Emit a `transcript` JSON event (role, text, is_final, phase, turn_id).
      - Agent turns with audio_bytes → binary WebSocket frame for playback.
      - Final user turns:
          * If the safety gate is awaiting ack → complete the ack.
          * Otherwise: evaluate with SafetyService; if unsafe, begin a safety
            rewrite; if safe, route to SetupHandler via _route_user_turn.

    T-026: Monitors for `character_bible_ready` completion. When SetupHandler
    emits `character_bible_ready` (signalled via setup_state.all_confirmed and
    session status = generating), spawns the page generation loop as a task.
    """
    page_loop_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
    page_loop_started = False

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

            if turn.role == "agent" and turn.audio_bytes:
                await ws.send_bytes(turn.audio_bytes)

            if turn.is_final and turn.role == "user":
                if safety_gate.awaiting_ack:
                    await _complete_safety_ack(ws, session_id, store, safety_gate)
                else:
                    result = await safety_svc.evaluate(
                        turn.transcript, session_id=session_id
                    )
                    if not result.safe:
                        proposed = result.rewrite or SAFE_FALLBACK_REWRITE
                        await _begin_safety_rewrite(
                            ws,
                            turn,
                            turn_id,
                            session_id,
                            voice_svc,
                            safety_gate,
                            proposed,
                            result.category,
                        )
                    else:
                        await _route_user_turn(
                            ws,
                            turn,
                            session_id,
                            voice_svc,
                            setup_handler,
                            setup_state,
                            store,
                        )

                        # T-026: after setup completes, launch the page loop
                        if not page_loop_started:
                            try:
                                session = await store.get_session(session_id)
                                if session.status == SessionStatus.generating:
                                    page_loop_started = True
                                    page_loop_task = asyncio.create_task(
                                        _page_generation_loop(
                                            ws=ws,
                                            session_id=session_id,
                                            store=store,
                                            story_planner=story_planner,
                                            character_bible_svc=character_bible_svc,
                                            image_svc=image_svc,
                                            tts_svc=tts_svc,
                                            media_svc=media_svc,
                                        )
                                    )
                            except Exception as exc:
                                logger.error(
                                    "_turn_loop: failed to check session for page loop "
                                    "(session=%s): %s",
                                    session_id,
                                    exc,
                                )

    except asyncio.CancelledError:
        logger.debug("_turn_loop cancelled (session=%s)", session_id)
        if page_loop_task is not None and not page_loop_task.done():
            page_loop_task.cancel()
            try:
                await page_loop_task
            except (asyncio.CancelledError, Exception):
                pass
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
    safety_svc: SafetyService = Depends(get_safety_service),
    setup_handler: SetupHandler = Depends(get_setup_handler),
    story_planner: StoryPlannerService = Depends(get_story_planner),
    character_bible_svc: CharacterBibleService = Depends(get_character_bible_svc),
    image_svc: ImageGenerationService = Depends(get_image_svc),
    tts_svc: TTSService = Depends(get_tts_svc),
    media_svc: MediaPersistenceService = Depends(get_media_svc),
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

    # ── Per-session safety gate ───────────────────────────────────────────
    safety_gate = _SafetyGate()

    # ── Per-session setup state ───────────────────────────────────────────
    setup_state = SetupState()

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
                    if turn_loop_task is None or turn_loop_task.done():
                        turn_loop_task = asyncio.create_task(
                            _turn_loop(
                                session_id,
                                websocket,
                                voice_svc,
                                safety_svc,
                                store,
                                safety_gate,
                                setup_handler,
                                setup_state,
                                story_planner,
                                character_bible_svc,
                                image_svc,
                                tts_svc,
                                media_svc,
                            )
                        )
                    await emit(websocket, "voice_session_ready")

            elif msg_type == "transcript_input":
                text = (
                    str(data.get("text", "")) if isinstance(data, dict) else ""
                )
                synthetic_turn = VoiceTurn(
                    role="user",
                    transcript=text,
                    audio_bytes=None,
                    is_final=True,
                )
                if safety_gate.awaiting_ack:
                    await _complete_safety_ack(
                        websocket, session_id, store, safety_gate
                    )
                else:
                    result = await safety_svc.evaluate(text, session_id=session_id)
                    if not result.safe:
                        turn_id = str(uuid.uuid4())
                        proposed = result.rewrite or SAFE_FALLBACK_REWRITE
                        await _begin_safety_rewrite(
                            websocket,
                            synthetic_turn,
                            turn_id,
                            session_id,
                            voice_svc,
                            safety_gate,
                            proposed,
                            result.category,
                        )
                    else:
                        await _route_user_turn(
                            websocket,
                            synthetic_turn,
                            session_id,
                            voice_svc,
                            setup_handler,
                            setup_state,
                            store,
                        )

            else:
                await emit(websocket, "session_error", code="unknown_message_type")

    finally:
        # Disconnect with pending safety gate → persist rejection.
        if safety_gate.awaiting_ack:
            await _persist_abandoned_safety_decision(session_id, store, safety_gate)

        if turn_loop_task is not None and not turn_loop_task.done():
            turn_loop_task.cancel()
            try:
                await turn_loop_task
            except (asyncio.CancelledError, Exception):
                pass

        await voice_svc.end(session_id)
        logger.info("WS handler cleaned up (session=%s)", session_id)
