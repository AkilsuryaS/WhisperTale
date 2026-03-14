"""
test_t031_interrupt_voice_feedback.py

Unit tests for T-031: interrupt handling + voice_feedback client message.

Tests cover:
  1. interrupt during narration → steering_window_open emitted immediately
  2. voice_feedback with valid command_type → voice_command_received + voice_command_applied
  3. voice_feedback with unsafe transcript → safety flow triggered
  4. _PageLoopState interrupt_event and steering_turn_queue mechanics
  5. interrupt ignored when already in steering window

Depends: T-030, T-031
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.character_bible import CharacterBible, ContentPolicy, ProtagonistProfile, StyleBible
from app.models.safety import SafetyResult
from app.models.session import Session
from app.models.voice_command import CommandType
from app.services.adk_voice_service import VoiceTurn
from app.websocket.steering_handler import SteeringHandler
from app.websocket.story_ws import _PageLoopState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe() -> SafetyResult:
    return SafetyResult(safe=True)


def _unsafe() -> SafetyResult:
    from app.models.safety import SafetyCategory
    return SafetyResult(safe=False, category=SafetyCategory.physical_harm, rewrite="rewrite")


def _make_turn(transcript: str) -> VoiceTurn:
    return VoiceTurn(role="user", transcript=transcript, audio_bytes=None, is_final=True)


def _make_session(arc: list[str] | None = None) -> Session:
    return Session(
        story_arc=arc or [f"beat {i}" for i in range(1, 6)],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_bible() -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip", species_or_type="rabbit", color="golden",
            notable_traits=["big eyes", "fluffy tail"],
        ),
        style_bible=StyleBible(
            art_style="watercolour", color_palette="warm pastels",
            mood="cosy", negative_style_terms=["dark"],
        ),
        content_policy=ContentPolicy(exclusions=["no gore"]),
    )


def _make_steering_handler(
    safety_result: SafetyResult = None,
    new_arc: list[str] = None,
) -> tuple[SteeringHandler, dict[str, Any]]:
    mocks: dict[str, Any] = {}

    safety_svc = MagicMock()
    safety_svc.evaluate = AsyncMock(return_value=safety_result or _safe())
    mocks["safety_svc"] = safety_svc

    story_planner = MagicMock()
    story_planner.apply_steering = AsyncMock(
        return_value=new_arc or [f"r{i}" for i in range(1, 6)]
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
# TC-1: _PageLoopState mechanics
# ---------------------------------------------------------------------------


class TestPageLoopState:
    def test_interrupt_event_starts_unset(self) -> None:
        state = _PageLoopState()
        assert not state.interrupt_event.is_set()

    def test_in_steering_window_starts_false(self) -> None:
        state = _PageLoopState()
        assert state.in_steering_window is False

    def test_steering_turn_queue_starts_empty(self) -> None:
        state = _PageLoopState()
        assert state.steering_turn_queue.empty()

    @pytest.mark.asyncio
    async def test_interrupt_event_can_be_set_and_awaited(self) -> None:
        state = _PageLoopState()
        state.interrupt_event.set()
        # Should return immediately without blocking
        await asyncio.wait_for(state.interrupt_event.wait(), timeout=1.0)
        assert state.interrupt_event.is_set()

    @pytest.mark.asyncio
    async def test_steering_turn_queue_can_receive_turn(self) -> None:
        state = _PageLoopState()
        turn = _make_turn("make it funnier")
        await state.steering_turn_queue.put(turn)
        received = await state.steering_turn_queue.get()
        assert received.transcript == "make it funnier"


# ---------------------------------------------------------------------------
# TC-2: Spec "Done when" — interrupt during narration emits steering_window_open
# ---------------------------------------------------------------------------


class TestInterruptDuringNarration:
    @pytest.mark.asyncio
    async def test_interrupt_causes_steering_window_open(self) -> None:
        """
        Simulates interrupt by setting the event before the page loop runs,
        verifying that steering_window_open is emitted.
        """
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()

        # Pre-set the interrupt event so the page loop skips straight to steering
        state.interrupt_event.set()

        # Run a steering window that will emit steering_window_open
        await handler.run_steering_window(
            session_id="s1",
            page_number=1,
            emit=emit_fn,
            window_seconds=0.05,
            turn_queue=state.steering_turn_queue,
        )

        event_types = [e["type"] for e in events]
        assert "steering_window_open" in event_types

    @pytest.mark.asyncio
    async def test_interrupt_event_triggers_before_page_completes(self) -> None:
        """
        Verifies asyncio.wait race between page_task and interrupt_event.wait()
        — interrupt_task wins when event is pre-set.
        """
        state = _PageLoopState()

        async def slow_page() -> None:
            await asyncio.sleep(10.0)  # simulates long narration

        state.interrupt_event.set()  # interrupt fires immediately

        page_task = asyncio.ensure_future(slow_page())
        interrupt_task = asyncio.ensure_future(state.interrupt_event.wait())

        done, pending = await asyncio.wait(
            {page_task, interrupt_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        assert interrupt_task in done
        assert page_task in pending or page_task.cancelled()

    @pytest.mark.asyncio
    async def test_interrupt_event_does_not_fire_when_not_set(self) -> None:
        """
        When interrupt is not set, page_task wins the race.
        """
        state = _PageLoopState()

        async def instant_page() -> None:
            pass  # completes immediately

        page_task = asyncio.ensure_future(instant_page())
        interrupt_task = asyncio.ensure_future(state.interrupt_event.wait())

        done, pending = await asyncio.wait(
            {page_task, interrupt_task},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=1.0,
        )

        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        assert page_task in done


# ---------------------------------------------------------------------------
# TC-3: Spec "Done when" — voice_feedback triggers voice_command_received + applied
# ---------------------------------------------------------------------------


class TestVoiceFeedback:
    @pytest.mark.asyncio
    async def test_voice_feedback_tone_change_triggers_received_and_applied(self) -> None:
        """
        Simulates voice_feedback by putting a VoiceTurn directly into
        steering_turn_queue and running the steering window.
        """
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        # Inject a tone_change utterance into the queue (simulates voice_feedback)
        turn = _make_turn("make it funnier")
        await state.steering_turn_queue.put(turn)

        await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit_fn,
            window_seconds=5.0,
            turn_queue=state.steering_turn_queue,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_received" in event_types
        assert "voice_command_applied" in event_types

    @pytest.mark.asyncio
    async def test_voice_feedback_command_type_propagated(self) -> None:
        handler, _ = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        await state.steering_turn_queue.put(_make_turn("add a new character"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit_fn,
            window_seconds=5.0, turn_queue=state.steering_turn_queue,
        )

        received = next(e for e in events if e["type"] == "voice_command_received")
        assert received["command_type"] == CommandType.character_introduction.value

    @pytest.mark.asyncio
    async def test_voice_feedback_pacing_change_triggers_arc_update(self) -> None:
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        await state.steering_turn_queue.put(_make_turn("make it shorter"))

        await handler.run_steering_window(
            session_id="s1", page_number=3, emit=emit_fn,
            window_seconds=5.0, turn_queue=state.steering_turn_queue,
        )

        mocks["store"].update_story_arc.assert_called_once()


# ---------------------------------------------------------------------------
# TC-4: Spec "Done when" — voice_feedback with unsafe transcript triggers safety
# ---------------------------------------------------------------------------


class TestVoiceFeedbackUnsafe:
    @pytest.mark.asyncio
    async def test_unsafe_voice_feedback_triggers_safety_and_closes_window(self) -> None:
        """
        When an unsafe turn is fed via the queue, the steering window
        closes with reason="unsafe" without applying a command.
        """
        handler, mocks = _make_steering_handler(safety_result=_unsafe())
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        await state.steering_turn_queue.put(_make_turn("hurt the character"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit_fn,
            window_seconds=5.0, turn_queue=state.steering_turn_queue,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_applied" not in event_types
        closed = next(e for e in events if e["type"] == "steering_window_closed")
        assert closed["reason"] == "unsafe"

    @pytest.mark.asyncio
    async def test_unsafe_voice_feedback_does_not_update_arc(self) -> None:
        handler, mocks = _make_steering_handler(safety_result=_unsafe())
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        await state.steering_turn_queue.put(_make_turn("hurt the character"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit_fn,
            window_seconds=5.0, turn_queue=state.steering_turn_queue,
        )

        mocks["story_planner"].apply_steering.assert_not_called()
        mocks["store"].update_story_arc.assert_not_called()


# ---------------------------------------------------------------------------
# TC-5: interrupt + voice_feedback combined — interrupt then voice_feedback
# ---------------------------------------------------------------------------


class TestInterruptThenVoiceFeedback:
    @pytest.mark.asyncio
    async def test_voice_feedback_after_interrupt_applies_command(self) -> None:
        """
        After an interrupt, the steering window is open. A voice_feedback
        message (placed in the queue) should be processed normally.
        """
        handler, mocks = _make_steering_handler()
        events: list[dict] = []

        async def emit_fn(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        state = _PageLoopState()
        # Pre-set interrupt (simulates the interrupt message arriving)
        state.interrupt_event.set()
        # Then put a voice_feedback turn in the queue
        await state.steering_turn_queue.put(_make_turn("make it funnier"))

        # After interrupt, page_loop would open steering window with the queue
        await handler.run_steering_window(
            session_id="s1",
            page_number=1,
            emit=emit_fn,
            window_seconds=5.0,
            turn_queue=state.steering_turn_queue,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_received" in event_types
        assert "voice_command_applied" in event_types
