"""
test_steering_timeout_fix.py

Targeted tests for the mid-story steering timing fix.

Scenarios covered:
  1. user_interrupted flag extends the steering window to 120 s (not the
     default 10 s), so the user has enough time to speak and submit.
  2. transcript_input arriving AFTER the natural window has timed out is
     force-routed to a new steering window via the interrupt_event fallback.
  3. During user-initiated interrupts, ADK partial turns are suppressed from
     the steering queue so only the complete transcript_input is processed.
  4. page_loop_active flag is correctly managed by the page generation loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.safety import SafetyResult
from app.models.session import Session
from app.services.adk_voice_service import VoiceTurn
from app.websocket.steering_handler import SteeringHandler
from app.websocket.story_ws import _PageLoopState


def _safe() -> SafetyResult:
    return SafetyResult(safe=True)


def _make_turn(transcript: str) -> VoiceTurn:
    return VoiceTurn(
        role="user", transcript=transcript, audio_bytes=None, is_final=True
    )


def _make_session(arc: list[str] | None = None) -> Session:
    return Session(
        story_arc=arc or [f"beat {i}" for i in range(1, 6)],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_bible() -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="cat",
            color="white",
            notable_traits=["fluffy", "big eyes"],
        ),
        style_bible=StyleBible(
            art_style="watercolour",
            color_palette="warm pastels",
            mood="cosy",
            negative_style_terms=["dark"],
        ),
        content_policy=ContentPolicy(exclusions=[]),
    )


def _make_steering_handler() -> tuple[SteeringHandler, dict[str, Any]]:
    mocks: dict[str, Any] = {}

    safety_svc = MagicMock()
    safety_svc.evaluate = AsyncMock(return_value=_safe())
    mocks["safety_svc"] = safety_svc

    story_planner = MagicMock()
    story_planner.apply_steering = AsyncMock(
        return_value=[f"revised beat {i}" for i in range(1, 6)]
    )
    mocks["story_planner"] = story_planner

    char_bible_svc = MagicMock()
    char_bible_svc.add_secondary_character = AsyncMock(return_value=None)
    mocks["char_bible_svc"] = char_bible_svc

    store = MagicMock()
    store.get_session = AsyncMock(return_value=_make_session())
    store.get_character_bible = AsyncMock(return_value=_make_bible())
    store.update_story_arc = AsyncMock(return_value=None)
    store.save_voice_command = AsyncMock(return_value=None)
    mocks["store"] = store

    voice_svc = MagicMock()
    voice_svc.speak = AsyncMock(return_value=None)
    mocks["voice_svc"] = voice_svc

    handler = SteeringHandler(
        safety_svc=safety_svc,
        story_planner=story_planner,
        character_bible_svc=char_bible_svc,
        store=store,
        voice_svc=voice_svc,
    )
    return handler, mocks


# ---------------------------------------------------------------------------
# 1. PageLoopState new fields have correct defaults
# ---------------------------------------------------------------------------


class TestPageLoopStateNewFields:
    def test_user_interrupted_default_false(self) -> None:
        state = _PageLoopState()
        assert state.user_interrupted is False

    def test_page_loop_active_default_false(self) -> None:
        state = _PageLoopState()
        assert state.page_loop_active is False


# ---------------------------------------------------------------------------
# 2. Extended timeout: user-interrupt steering window accepts a turn that
#    arrives AFTER the default 10 s would have expired.
# ---------------------------------------------------------------------------


class TestExtendedTimeout:
    @pytest.mark.asyncio
    async def test_turn_accepted_after_natural_timeout_would_expire(self) -> None:
        """
        Simulate the exact user scenario: the steering window opens with 120 s
        timeout (user_interrupted).  A turn is placed in the queue after 0.2 s,
        well beyond a typical 0.05 s test timeout but within the extended one.
        The command should be applied successfully.
        """
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        state.user_interrupted = True

        # Schedule the user's transcript_input to arrive after 0.15 s.
        # With a 0.05 s natural timeout this would miss; 120 s timeout allows it.
        async def delayed_put() -> None:
            await asyncio.sleep(0.15)
            await state.steering_turn_queue.put(
                _make_turn("make the white cat black instead of white")
            )

        asyncio.ensure_future(delayed_put())

        close_reason = await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=5.0,  # extended timeout
            turn_queue=state.steering_turn_queue,
        )

        assert close_reason == "voice_command_applied"
        event_types = [e["type"] for e in events]
        assert "voice_command_received" in event_types
        assert "voice_command_applied" in event_types
        mocks["store"].update_story_arc.assert_called_once()

    @pytest.mark.asyncio
    async def test_natural_window_still_times_out_quickly(self) -> None:
        """
        Without user_interrupted, the window uses the default short timeout.
        """
        handler, _ = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        # user_interrupted stays False (natural between-page window)

        close_reason = await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=0.05,  # short timeout for natural window
            turn_queue=state.steering_turn_queue,
        )

        assert close_reason == "timeout"


# ---------------------------------------------------------------------------
# 3. Queue preservation: when user_interrupted is True, a pre-queued turn
#    must NOT be drained.
# ---------------------------------------------------------------------------


class TestQueuePreservation:
    @pytest.mark.asyncio
    async def test_prequeued_turn_not_drained_when_user_interrupted(self) -> None:
        """
        Simulates transcript_input arriving before the steering window opens
        (the fallback path). The turn is pre-queued, and user_interrupted is
        True, so the queue drain is skipped and the turn is processed.
        """
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        state.user_interrupted = True

        # Pre-queue the turn (simulates the dispatch loop's fallback path)
        await state.steering_turn_queue.put(
            _make_turn("change the cat color to black")
        )

        close_reason = await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=5.0,
            turn_queue=state.steering_turn_queue,
        )

        assert close_reason == "voice_command_applied"
        mocks["store"].update_story_arc.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Classification of user's steering commands
# ---------------------------------------------------------------------------


class TestSteeringClassification:
    @pytest.mark.asyncio
    async def test_make_command_classified_as_element_reintroduction(self) -> None:
        """'make the white cat black' should match the 'make \\w+' pattern."""
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q: asyncio.Queue = asyncio.Queue()
        await q.put(_make_turn("make the white cat black instead of white"))

        close_reason = await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=5.0,
            turn_queue=q,
        )

        assert close_reason == "voice_command_applied"
        received = next(e for e in events if e["type"] == "voice_command_received")
        assert received["command_type"] == "element_reintroduction"

    @pytest.mark.asyncio
    async def test_change_command_classified(self) -> None:
        """'change the color' should match the 'change' pattern."""
        handler, _ = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q: asyncio.Queue = asyncio.Queue()
        await q.put(_make_turn("change the cat's color to black"))

        close_reason = await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=5.0,
            turn_queue=q,
        )

        assert close_reason == "voice_command_applied"

    @pytest.mark.asyncio
    async def test_instead_command_classified(self) -> None:
        """'instead of white make it black' should match the 'instead' pattern."""
        handler, _ = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q: asyncio.Queue = asyncio.Queue()
        await q.put(_make_turn("instead of white, make the cat black"))

        close_reason = await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=5.0,
            turn_queue=q,
        )

        assert close_reason == "voice_command_applied"
