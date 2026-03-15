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
from app.websocket.steering_handler import make_steering_handler
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
from app.services.edit_classifier import EditClassifierService
from app.services.edit_handler import EditHandlerService
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
# Page-loop shared state (T-031)
# ---------------------------------------------------------------------------


@dataclass
class _PageLoopState:
    """
    Shared mutable state between the page-generation loop background task and
    the WebSocket message-dispatch loop.

    interrupt_event:     Set by the ``interrupt`` client message to signal the
                         page loop that the client wants to enter the steering
                         window immediately (cancels the current narration wait).
    steering_turn_queue: asyncio.Queue filled by ``voice_feedback`` client
                         messages; consumed by SteeringHandler.run_steering_window
                         as the source of synthetic user turns.
    in_steering_window:  True while a steering window is active; guards against
                         duplicate ``interrupt`` handling.
    user_interrupted:    True when the user actively interrupted (via mic tap)
                         rather than entering a natural between-page window.
                         Extends the steering timeout and suppresses ADK noise.
    page_loop_active:    True while the page generation loop is running.
    """

    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    steering_turn_queue: asyncio.Queue = field(  # type: ignore[type-arg]
        default_factory=asyncio.Queue
    )
    in_steering_window: bool = False
    page_loop_started: bool = False
    user_interrupted: bool = False
    page_loop_active: bool = False


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
        async def _forward_audio(chunk: bytes) -> None:
            await ws.send_bytes(chunk)

        await voice_svc.speak(session_id, proposed_rewrite, on_audio=_forward_audio)
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
    safety_svc: SafetyService | None = None,
    voice_svc: VoiceSessionService | None = None,
    steering_window_seconds: float = _STEERING_WINDOW_SECONDS,
    page_loop_state: _PageLoopState | None = None,
    initial_page_history: list[str] | None = None,
) -> None:
    """
    Run the full 5-page story generation loop after setup is complete.

    Sequence for each page:
      1. Fetch the current session to get the story arc.
      2. Call run_page (emits page events via the WebSocket).
         If ``page_loop_state.interrupt_event`` is set mid-narration, skip
         directly to the steering window (T-031).
      3. After page_complete: append first-25-word snippet of page text to the
         in-memory page_history list and persist it to the Session document
         (T-032).  The accumulated list is passed to all subsequent expand_page
         calls for narrative coherence.
      4. Open a steering window via SteeringHandler, passing the shared
         ``steering_turn_queue`` so voice_feedback messages reach it.
    After all 5 pages: emit story_complete, update session status to complete.

    Args:
        initial_page_history: Pre-seeded history from ``Session.page_history``
                              used when the loop is restarted after a reconnect.
    """
    from app.websocket.page_orchestrator import run_page

    # Ensure we have a state object (may be None when called from tests or REST)
    loop_state = page_loop_state or _PageLoopState()
    loop_state.page_loop_active = True

    # T-032: in-memory page history; seeded from persisted data on reconnect
    page_history: list[str] = list(initial_page_history) if initial_page_history else []

    # Extended timeout (seconds) when the user actively interrupted mid-page.
    _USER_INTERRUPT_TIMEOUT = 120.0

    async def ws_emit(event_type: str, **fields: object) -> None:
        try:
            await emit(ws, event_type, **fields)
        except Exception:
            pass  # WebSocket may have closed during generation

    try:
        for page_number in range(1, 6):
            appended_current_page_history = False
            # Re-fetch session to get latest story_arc
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

            # T-031: clear interrupt flag before starting page narration
            loop_state.interrupt_event.clear()
            loop_state.in_steering_window = False

            # Run page generation; interrupt_event allows early exit to steering
            page_task = asyncio.ensure_future(
                run_page(
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
            )

            interrupt_task = asyncio.ensure_future(
                loop_state.interrupt_event.wait()
            )

            done, pending = await asyncio.wait(
                {page_task, interrupt_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever task didn't finish first
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

            if interrupt_task in done:
                # Client sent interrupt — jump directly to steering window
                logger.info(
                    "_page_generation_loop: interrupt received mid-narration "
                    "(session=%s, page=%d)",
                    session_id,
                    page_number,
                )

            # T-032: accumulate page history from the completed page text
            # (only when page completed normally, not on interrupt before text)
            if page_task in done and not page_task.cancelled():
                try:
                    completed_page = await store.get_page(session_id, page_number)
                    if completed_page is not None and completed_page.text:
                        snippet = " ".join(
                            completed_page.text.split()[:25]
                        )
                        page_history.append(snippet)
                        appended_current_page_history = True
                        # Persist to Session for reconnect recovery
                        await store.update_page_history(session_id, page_history)
                        logger.debug(
                            "_page_generation_loop: page_history updated "
                            "(session=%s, page=%d, entries=%d)",
                            session_id,
                            page_number,
                            len(page_history),
                        )
                except Exception as exc:
                    logger.error(
                        "_page_generation_loop: page_history update failed "
                        "(session=%s, page=%d): %s",
                        session_id,
                        page_number,
                        exc,
                    )

            # Steering window between pages (not after the last page)
            if page_number < 5:
                was_user_interrupt = loop_state.user_interrupted
                effective_timeout = (
                    _USER_INTERRUPT_TIMEOUT
                    if was_user_interrupt
                    else steering_window_seconds
                )

                # Only drain stale turns for natural (between-page) windows.
                # User-initiated interrupts may already have a queued turn
                # from transcript_input that we must NOT discard.
                if not was_user_interrupt:
                    try:
                        while True:
                            loop_state.steering_turn_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

                loop_state.in_steering_window = True

                logger.info(
                    "_page_generation_loop: steering window opened "
                    "(session=%s, page=%d, user_interrupt=%s, timeout=%.0fs)",
                    session_id,
                    page_number,
                    was_user_interrupt,
                    effective_timeout,
                )

                steering_handler = make_steering_handler(
                    safety_svc=safety_svc,  # type: ignore[arg-type]
                    story_planner=story_planner,
                    character_bible_svc=character_bible_svc,
                    store=store,
                    voice_svc=voice_svc,  # type: ignore[arg-type]
                    ws=ws,
                ) if safety_svc and voice_svc else None

                if steering_handler:
                    close_reason = await steering_handler.run_steering_window(
                        session_id=session_id,
                        page_number=page_number,
                        emit=ws_emit,
                        window_seconds=effective_timeout,
                        turn_queue=loop_state.steering_turn_queue,
                    )
                else:
                    # Fallback: bare open/sleep/close (no voice commands possible)
                    await ws_emit(
                        "steering_window_open",
                        page=page_number,
                        duration_seconds=steering_window_seconds,
                    )
                    await asyncio.sleep(steering_window_seconds)
                    await ws_emit("steering_window_closed", page=page_number)
                    close_reason = "timeout"

                loop_state.in_steering_window = False
                loop_state.user_interrupted = False  # reset after window closes

                # If a steering command was applied, regenerate this same page
                # with the updated beat so text/image/audio reflect the change
                # immediately, then continue with subsequent pages.
                if close_reason == "voice_command_applied":
                    try:
                        refreshed = await store.get_session(session_id)
                        if refreshed.story_arc and len(refreshed.story_arc) >= page_number:
                            updated_beat = refreshed.story_arc[page_number - 1]
                            # Remove stale snippet for this page, then regenerate.
                            if appended_current_page_history and page_history:
                                page_history.pop()
                                await store.update_page_history(session_id, page_history)
                            await run_page(
                                session_id=session_id,
                                page_number=page_number,
                                beat=updated_beat,
                                page_history=page_history,
                                emit=ws_emit,
                                story_planner=story_planner,
                                character_bible_svc=character_bible_svc,
                                image_svc=image_svc,
                                tts_svc=tts_svc,
                                media_svc=media_svc,
                                session_store=store,
                            )
                            regenerated = await store.get_page(session_id, page_number)
                            if regenerated is not None and regenerated.text:
                                snippet = " ".join(regenerated.text.split()[:25])
                                page_history.append(snippet)
                                await store.update_page_history(session_id, page_history)
                    except Exception as exc:
                        logger.error(
                            "_page_generation_loop: same-page regeneration failed "
                            "(session=%s, page=%d): %s",
                            session_id,
                            page_number,
                            exc,
                        )

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
    finally:
        loop_state.page_loop_active = False


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
    page_loop_state: _PageLoopState | None = None,
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
    loop_state = page_loop_state or _PageLoopState()

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
                logger.info(
                    "voice command received: final user turn",
                    extra={
                        "event_type": "voice_command_received",
                        "session_id": session_id,
                        "command": "user_turn",
                        "safety_awaiting_ack": safety_gate.awaiting_ack,
                    },
                )
                # While steering window is open, route spoken mic turns to the
                # steering queue so they update the arc (instead of setup flow).
                if loop_state.in_steering_window:
                    # During user-initiated interrupts the mic is live, so the
                    # ADK produces noisy partial "final" turns.  Skip them and
                    # rely on the complete transcript_input from the frontend.
                    if not loop_state.user_interrupted:
                        await loop_state.steering_turn_queue.put(turn)
                    else:
                        logger.debug(
                            "_turn_loop: skipping ADK turn during user-interrupt "
                            "steering window (session=%s, text=%r)",
                            session_id,
                            turn.transcript[:60] if turn.transcript else "",
                        )
                    continue
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

                        if not loop_state.page_loop_started:
                            try:
                                session = await store.get_session(session_id)
                                if session.status == SessionStatus.generating:
                                    logger.info(
                                        "session status changed to generating",
                                        extra={
                                            "event_type": "session_status_changed",
                                            "session_id": session_id,
                                            "status": "generating",
                                        },
                                    )
                                    loop_state.page_loop_started = True
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
                                            safety_svc=safety_svc,
                                            voice_svc=voice_svc,
                                            page_loop_state=loop_state,
                                            initial_page_history=list(
                                                session.page_history
                                            ),
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
            "WS rejected — missing or empty token",
            extra={
                "event_type": "ws_rejected",
                "session_id": session_id,
                "reason": "invalid_token",
            },
        )
        await websocket.close(code=4001)
        return

    # ── Session lookup ────────────────────────────────────────────────────
    try:
        session = await store.get_session(session_id)
    except SessionNotFoundError:
        logger.warning(
            "WS rejected — session not found",
            extra={
                "event_type": "ws_rejected",
                "session_id": session_id,
                "reason": "session_not_found",
            },
        )
        await emit(websocket, "session_error", code="session_not_found")
        await websocket.close(code=4001)
        return

    # ── Emit connected ────────────────────────────────────────────────────
    await emit(websocket, "connected", session_status=session.status)
    logger.info(
        "WebSocket connected",
        extra={
            "event_type": "ws_connect",
            "session_id": session_id,
            "session_status": str(session.status),
        },
    )

    # ── Per-session safety gate ───────────────────────────────────────────
    safety_gate = _SafetyGate()

    # ── Per-session setup state ───────────────────────────────────────────
    setup_state = SetupState()

    # ── Per-session page-loop state (T-031) ───────────────────────────────
    page_loop_state = _PageLoopState()

    # ── Message dispatch loop ─────────────────────────────────────────────
    turn_loop_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]
    page_gen_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

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
                    logger.info(
                        "voice command received and applied: session_start",
                        extra={
                            "event_type": "voice_command_applied",
                            "session_id": session_id,
                            "command": "session_start",
                        },
                    )
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
                                page_loop_state=page_loop_state,
                            )
                        )
                    await emit(websocket, "voice_session_ready")

            elif msg_type == "transcript_input":
                text = (
                    str(data.get("text", "")) if isinstance(data, dict) else ""
                )
                if not text.strip():
                    continue
                turn_id = str(uuid.uuid4())
                synthetic_turn = VoiceTurn(
                    role="user",
                    transcript=text,
                    audio_bytes=None,
                    is_final=True,
                )

                # Echo the user's text back as a transcript event so the
                # frontend can clear "Processing" and show the user bubble.
                await emit(
                    websocket,
                    "transcript",
                    role="user",
                    text=text,
                    is_final=True,
                    phase="setup",
                    turn_id=turn_id,
                )

                if page_loop_state.in_steering_window:
                    await page_loop_state.steering_turn_queue.put(synthetic_turn)
                    logger.info(
                        "transcript_input routed to active steering window "
                        "(session=%s)",
                        session_id,
                    )
                    continue

                # Fallback: page loop is running but the steering window has
                # already closed (e.g. 10 s timeout expired while user was
                # still speaking).  Pre-queue the turn and force an interrupt
                # so the page loop opens a new steering window to process it.
                if page_loop_state.page_loop_active:
                    page_loop_state.user_interrupted = True
                    await page_loop_state.steering_turn_queue.put(synthetic_turn)
                    if not page_loop_state.interrupt_event.is_set():
                        page_loop_state.interrupt_event.set()
                    logger.info(
                        "transcript_input: force-entering steering "
                        "(session=%s, text=%r)",
                        session_id,
                        text[:80],
                    )
                    continue

                if safety_gate.awaiting_ack:
                    await _complete_safety_ack(
                        websocket, session_id, store, safety_gate
                    )
                else:
                    result = await safety_svc.evaluate(text, session_id=session_id)
                    if not result.safe:
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

                        # After setup completes, session status becomes
                        # "generating".  _turn_loop only checks this when
                        # the ADK stream yields a turn, which won't happen
                        # while the mic is off.  Launch the page loop here.
                        if not page_loop_state.page_loop_started:
                            try:
                                sess = await store.get_session(session_id)
                                if sess.status == SessionStatus.generating:
                                    logger.info(
                                        "transcript_input: launching page generation loop",
                                        extra={
                                            "event_type": "session_status_changed",
                                            "session_id": session_id,
                                            "status": "generating",
                                        },
                                    )
                                    page_loop_state.page_loop_started = True
                                    page_gen_task = asyncio.create_task(
                                        _page_generation_loop(
                                            ws=websocket,
                                            session_id=session_id,
                                            store=store,
                                            story_planner=story_planner,
                                            character_bible_svc=character_bible_svc,
                                            image_svc=image_svc,
                                            tts_svc=tts_svc,
                                            media_svc=media_svc,
                                            safety_svc=safety_svc,
                                            voice_svc=voice_svc,
                                            page_loop_state=page_loop_state,
                                            initial_page_history=list(
                                                sess.page_history
                                            ),
                                        )
                                    )
                            except Exception as exc:
                                logger.error(
                                    "transcript_input: failed to launch page loop "
                                    "(session=%s): %s",
                                    session_id,
                                    exc,
                                )

            elif msg_type == "interrupt":
                # T-031: client interrupts mid-narration to enter steering window
                if not page_loop_state.in_steering_window:
                    page_loop_state.user_interrupted = True
                    page_loop_state.interrupt_event.set()
                    logger.info(
                        "WS interrupt received (session=%s, user_interrupted=True)",
                        session_id,
                    )
                    logger.info(
                        "voice command received and applied: interrupt",
                        extra={
                            "event_type": "voice_command_applied",
                            "session_id": session_id,
                            "command": "interrupt",
                        },
                    )
                else:
                    logger.debug(
                        "WS interrupt ignored — already in steering window "
                        "(session=%s)",
                        session_id,
                    )

            elif msg_type == "voice_feedback":
                # T-031: non-audio client injects a transcript directly into the
                # active steering window's turn queue.
                raw_transcript = (
                    str(data.get("raw_transcript", ""))
                    if isinstance(data, dict)
                    else ""
                )
                logger.info(
                    "voice command received: voice_feedback",
                    extra={
                        "event_type": "voice_command_received",
                        "session_id": session_id,
                        "command": "voice_feedback",
                        "in_steering_window": page_loop_state.in_steering_window,
                    },
                )
                synthetic_turn = VoiceTurn(
                    role="user",
                    transcript=raw_transcript,
                    audio_bytes=None,
                    is_final=True,
                )
                if page_loop_state.in_steering_window:
                    await page_loop_state.steering_turn_queue.put(synthetic_turn)
                else:
                    # Outside steering window: run safety + route as transcript_input
                    result = await safety_svc.evaluate(
                        raw_transcript, session_id=session_id
                    )
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

            elif msg_type == "edit_request":
                edit_instruction = (
                    str(data.get("instruction", "")) if isinstance(data, dict) else ""
                )
                edit_hint_page = (
                    data.get("hint_page") if isinstance(data, dict) else None
                )

                if not edit_instruction.strip():
                    await emit(
                        websocket, "edit_failed",
                        error="Edit instruction is empty",
                    )
                    continue

                # Guard: only allow edits when story is complete and no
                # active page loop is running
                try:
                    edit_session = await store.get_session(session_id)
                except Exception:
                    await emit(
                        websocket, "edit_failed",
                        error="Failed to fetch session",
                    )
                    continue

                if edit_session.status != SessionStatus.complete:
                    await emit(
                        websocket, "edit_failed",
                        error="Edits are only allowed after story is complete",
                    )
                    continue

                if page_loop_state.page_loop_active:
                    await emit(
                        websocket, "edit_failed",
                        error="Cannot edit while pages are generating",
                    )
                    continue

                async def _run_edit_task(
                    _instruction: str = edit_instruction,
                    _hint_page: int | None = edit_hint_page,
                ) -> None:
                    async def ws_emit(event_type: str, **fields: object) -> None:
                        try:
                            await emit(websocket, event_type, **fields)
                        except Exception:
                            pass

                    try:
                        classifier = EditClassifierService(store=store)
                        decision = await classifier.classify(
                            session_id, _instruction, _hint_page
                        )

                        handler = EditHandlerService(
                            store=store,
                            story_planner=story_planner,
                            character_bible_svc=character_bible_svc,
                            image_svc=image_svc,
                            tts_svc=tts_svc,
                            media_svc=media_svc,
                        )
                        await handler.run_edit(session_id, decision, ws_emit)
                    except Exception as exc:
                        logger.error(
                            "edit_request: task failed (session=%s): %s",
                            session_id, exc,
                        )
                        try:
                            await ws_emit("edit_failed", error=str(exc))
                        except Exception:
                            pass

                asyncio.create_task(_run_edit_task())
                logger.info(
                    "edit_request: spawned edit task (session=%s, instruction=%r)",
                    session_id,
                    edit_instruction[:80],
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

        if page_gen_task is not None and not page_gen_task.done():
            page_gen_task.cancel()
            try:
                await page_gen_task
            except (asyncio.CancelledError, Exception):
                pass

        await voice_svc.end(session_id)
        logger.info(
            "WebSocket disconnected",
            extra={
                "event_type": "ws_disconnect",
                "session_id": session_id,
            },
        )
