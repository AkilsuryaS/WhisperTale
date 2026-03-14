"""
SteeringHandler — mid-story steering window flow (T-030).

Public interface:
    async def run_steering_window(
        session_id: str,
        page_number: int,
        emit: Callable,
        voice_svc: VoiceSessionService,
        safety_svc: SafetyService,
        story_planner: StoryPlannerService,
        character_bible_svc: CharacterBibleService,
        store: SessionStore,
        *,
        window_seconds: float = 10.0,
        turn_queue: asyncio.Queue | None = None,
    ) -> None

Flow
----
1. Emit ``steering_window_open(page, duration_ms)`` and start a ``window_seconds``
   asyncio timeout.
2. If a user turn arrives on ``turn_queue`` before the timeout:
   a. Run safety check via SafetyService.evaluate.
   b. If unsafe → emit ``steering_unsafe`` and close the window.
   c. If safe  → classify_steering.
   d. If ambiguous: speak a clarifying question, await one more turn, reclassify.
   e. If still ambiguous or no second turn arrives: close with reason="ambiguous".
   f. If classified (tone_change / pacing_change / element_reintroduction /
      character_introduction):
        i.  Emit ``voice_command_received`` with ``interpreted_as``.
        ii. Fetch latest arc + content policy; call apply_steering.
        iii. Persist updated arc via SessionStore.update_story_arc.
        iv. Build + persist VoiceCommand.
        v.  If character_introduction: derive CharacterRef + call
            CharacterBibleService.add_secondary_character.
        vi. Emit ``voice_command_applied``.
        vii. Emit ``steering_window_closed(reason="voice_command_applied")``.
3. On timeout with no command → emit ``steering_window_closed(reason="timeout")``.
4. On timeout after user_silent turn → ``steering_window_closed(reason="user_silent")``.

The ``turn_queue`` is an asyncio.Queue[VoiceTurn | None] filled externally by the
WebSocket receive loop.  None signals end-of-window from the outside.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from app.models.character_bible import CharacterRef
from app.models.safety import SafetyResult
from app.models.voice_command import CommandType, VoiceCommand
from app.services.adk_voice_service import VoiceTurn, VoiceSessionService
from app.services.character_bible_service import CharacterBibleService
from app.services.safety_service import SafetyService
from app.services.session_store import SessionStore
from app.services.story_planner import StoryPlannerService
from app.websocket.steering_router import SteeringClassification, classify_steering

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SECONDS = 10.0
_CLARIFYING_QUESTION = (
    "Different how — funnier, shorter, or something else?"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_char_ref(command: VoiceCommand, page_number: int) -> CharacterRef:
    """Build a minimal CharacterRef from a character_introduction VoiceCommand."""
    name_match = re.search(
        r"(?:add|introduce|new friend|new character|give\s+\w+\s+a)\s+(?:a\s+)?([A-Za-z][A-Za-z\s]{0,40})",
        command.interpreted_intent,
        re.IGNORECASE,
    )
    name = name_match.group(1).strip().title() if name_match else "New Character"
    char_id = re.sub(r"\s+", "_", name.lower())
    return CharacterRef(
        char_id=char_id,
        name=name,
        description=command.interpreted_intent,
        introduced_on_page=page_number,
        voice_command_id=command.command_id,
    )


# ---------------------------------------------------------------------------
# SteeringHandler
# ---------------------------------------------------------------------------


class SteeringHandler:
    """
    Manages one steering window for a single page boundary.

    Designed to be instantiated per steering window (not reused across pages).
    All services are injected for full test isolation.
    """

    def __init__(
        self,
        safety_svc: SafetyService | None = None,
        story_planner: StoryPlannerService | None = None,
        character_bible_svc: CharacterBibleService | None = None,
        store: SessionStore | None = None,
        voice_svc: VoiceSessionService | None = None,
        ws: object | None = None,
    ) -> None:
        self._safety_svc = safety_svc
        self._story_planner = story_planner
        self._character_bible_svc = character_bible_svc
        self._store = store
        self._voice_svc = voice_svc
        self._ws = ws

    async def run_steering_window(
        self,
        session_id: str,
        page_number: int,
        emit: Callable,
        *,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        turn_queue: asyncio.Queue | None = None,
    ) -> str:
        """
        Run one complete steering window and emit all events.

        Args:
            session_id:     Session identifier.
            page_number:    The page that just completed (1–4).
            emit:           Async callable ``(event_type, **fields) -> None``.
            window_seconds: How long to wait for a command (default 10 s).
            turn_queue:     asyncio.Queue[VoiceTurn | None] that receives user turns
                            from the WebSocket frame loop. If None, the window runs as
                            a pure timeout (no command possible).
        """
        await emit(
            "steering_window_open",
            page=page_number,
            duration_ms=int(window_seconds * 1000),
        )

        close_reason = await self._collect_command(
            session_id=session_id,
            page_number=page_number,
            emit=emit,
            window_seconds=window_seconds,
            turn_queue=turn_queue,
        )

        await emit("steering_window_closed", page=page_number, reason=close_reason)
        return close_reason

    # ------------------------------------------------------------------
    # Internal pipeline
    # ------------------------------------------------------------------

    async def _collect_command(
        self,
        session_id: str,
        page_number: int,
        emit: Callable,
        window_seconds: float,
        turn_queue: asyncio.Queue | None,
    ) -> str:
        """
        Wait for a user turn within the window and process it.

        Returns the close reason string.
        """
        if turn_queue is None:
            await asyncio.sleep(window_seconds)
            return "timeout"

        try:
            turn: VoiceTurn | None = await asyncio.wait_for(
                turn_queue.get(), timeout=window_seconds
            )
        except asyncio.TimeoutError:
            return "timeout"

        if turn is None:
            # Sentinel: window closed externally
            return "timeout"

        # Silence signal
        if not turn.transcript or not turn.transcript.strip():
            return "user_silent"

        # Safety check
        safety_result = await self._run_safety(turn.transcript, session_id)
        if not safety_result.safe:
            await emit(
                "steering_unsafe",
                page=page_number,
                reason="safety_check_failed",
            )
            return "unsafe"

        # Classify
        classification = classify_steering(turn.transcript, safety_result)

        # Ambiguous: ask one clarifying question, await one more turn
        if classification.type == "ambiguous":
            try:
                async def _forward_audio(chunk: bytes) -> None:
                    if self._ws is not None:
                        await self._ws.send_bytes(chunk)

                await self._voice_svc.speak(session_id, _CLARIFYING_QUESTION, on_audio=_forward_audio)  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning(
                    "SteeringHandler: clarifying speak failed (session=%s): %s",
                    session_id,
                    exc,
                )

            try:
                second_turn: VoiceTurn | None = await asyncio.wait_for(
                    turn_queue.get(), timeout=window_seconds
                )
            except asyncio.TimeoutError:
                return "ambiguous"

            if second_turn is None or not second_turn.transcript.strip():
                return "ambiguous"

            safety_result = await self._run_safety(second_turn.transcript, session_id)
            if not safety_result.safe:
                await emit("steering_unsafe", page=page_number, reason="safety_check_failed")
                return "unsafe"

            classification = classify_steering(second_turn.transcript, safety_result)
            turn = second_turn  # use the clarified utterance going forward

        if classification.type in ("ambiguous", "unsafe"):
            return str(classification.type)

        # Classified — apply the command
        await self._apply_command(
            session_id=session_id,
            page_number=page_number,
            turn=turn,
            classification=classification,
            emit=emit,
        )
        return "voice_command_applied"

    async def _run_safety(self, transcript: str, session_id: str) -> SafetyResult:
        """Run safety evaluation; fall back to safe=True on service error."""
        try:
            return await self._safety_svc.evaluate(transcript, session_id=session_id)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SteeringHandler: safety evaluation failed (session=%s): %s",
                session_id,
                exc,
            )
            from app.models.safety import SafetyResult as SR
            return SR(safe=True)

    async def _apply_command(
        self,
        session_id: str,
        page_number: int,
        turn: VoiceTurn,
        classification: SteeringClassification,
        emit: Callable,
    ) -> None:
        """
        Apply a classified steering command: update arc, persist VoiceCommand,
        optionally add secondary character, emit events.
        """
        command_type: CommandType = classification.type  # type: ignore[assignment]
        interpreted_intent = classification.detail or turn.transcript

        # Emit voice_command_received
        await emit(
            "voice_command_received",
            page=page_number,
            command_type=command_type.value,
            interpreted_as=interpreted_intent,
        )

        # Fetch current arc + content policy
        try:
            session = await self._store.get_session(session_id)  # type: ignore[union-attr]
            current_arc = list(session.story_arc) if session.story_arc else []
            bible = await self._store.get_character_bible(session_id)  # type: ignore[union-attr]
            content_policy = bible.content_policy if bible else None
        except Exception as exc:
            logger.error(
                "SteeringHandler: failed to fetch session/bible (session=%s): %s",
                session_id,
                exc,
            )
            return

        # Apply steering to the current page and all following pages so edits are
        # visible immediately (same page) and remain consistent going forward.
        from_page = page_number
        if from_page > 5 or len(current_arc) < 5:
            logger.warning(
                "SteeringHandler: no pages left to steer (session=%s, page=%d)",
                session_id,
                page_number,
            )
            return

        try:
            command_id = uuid4()
            command = VoiceCommand(
                command_id=command_id,
                turn_id=uuid4(),
                raw_transcript=turn.transcript,
                interpreted_intent=interpreted_intent,
                command_type=command_type,
                applied_to_pages=list(range(from_page, 6)),
                received_at=datetime.now(timezone.utc),
            )

            new_arc = await self._story_planner.apply_steering(  # type: ignore[union-attr]
                arc=current_arc,
                command=command,
                from_page=from_page,
                content_policy=content_policy,
            )
            await self._store.update_story_arc(session_id, new_arc)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SteeringHandler: apply_steering failed (session=%s): %s",
                session_id,
                exc,
            )
            return

        # Persist VoiceCommand
        try:
            await self._store.save_voice_command(session_id, command)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SteeringHandler: save_voice_command failed (session=%s): %s",
                session_id,
                exc,
            )

        # Character introduction: add secondary character to CharacterBible
        if command_type == CommandType.character_introduction:
            try:
                char_ref = _derive_char_ref(command, page_number=from_page)
                await self._character_bible_svc.add_secondary_character(  # type: ignore[union-attr]
                    session_id, char_ref
                )
            except Exception as exc:
                logger.error(
                    "SteeringHandler: add_secondary_character failed (session=%s): %s",
                    session_id,
                    exc,
                )

        # Tone change: update StyleBible.mood for carry-forward (T-033)
        if command_type == CommandType.tone_change:
            try:
                await self._character_bible_svc.update_mood(  # type: ignore[union-attr]
                    session_id,
                    new_mood=interpreted_intent,
                    command_id=command.command_id,
                )
            except Exception as exc:
                logger.error(
                    "SteeringHandler: update_mood failed (session=%s): %s",
                    session_id,
                    exc,
                )

        # Emit voice_command_applied
        await emit(
            "voice_command_applied",
            page=page_number,
            command_type=command_type.value,
            applied_to_pages=command.applied_to_pages,
        )


# ---------------------------------------------------------------------------
# Factory helper for dependency injection
# ---------------------------------------------------------------------------


def make_steering_handler(
    safety_svc: SafetyService,
    story_planner: StoryPlannerService,
    character_bible_svc: CharacterBibleService,
    store: SessionStore,
    voice_svc: VoiceSessionService,
    ws: object | None = None,
) -> SteeringHandler:
    """Construct a SteeringHandler with all dependencies injected."""
    return SteeringHandler(
        safety_svc=safety_svc,
        story_planner=story_planner,
        character_bible_svc=character_bible_svc,
        store=store,
        voice_svc=voice_svc,
        ws=ws,
    )
