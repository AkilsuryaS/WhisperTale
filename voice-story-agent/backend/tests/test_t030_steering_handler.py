"""
test_t030_steering_handler.py

Unit tests for T-030: SteeringHandler — steering window + VoiceCommand flow.

All services are mocked. No real Gemini, Firestore, or WebSocket calls.
Covers all 5 spec "Done when" criteria.

Depends: T-028, T-029, T-019, T-026
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.models.character_bible import CharacterBible, ContentPolicy, ProtagonistProfile, StyleBible
from app.models.safety import SafetyResult
from app.models.session import Session
from app.models.voice_command import CommandType
from app.services.adk_voice_service import VoiceTurn
from app.websocket.steering_handler import SteeringHandler, _derive_char_ref
from app.models.voice_command import VoiceCommand


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_result() -> SafetyResult:
    return SafetyResult(safe=True, category=None, rewrite=None)


def _unsafe_result() -> SafetyResult:
    from app.models.safety import SafetyCategory
    return SafetyResult(safe=False, category=SafetyCategory.physical_harm, rewrite="safe rewrite")


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


def _make_handler(
    safety_result: SafetyResult = None,
    new_arc: list[str] = None,
) -> tuple[SteeringHandler, dict[str, Any]]:
    """Build a SteeringHandler with all mocked dependencies."""
    mocks: dict[str, Any] = {}

    safety_svc = MagicMock()
    safety_svc.evaluate = AsyncMock(
        return_value=safety_result or _safe_result()
    )
    mocks["safety_svc"] = safety_svc

    story_planner = MagicMock()
    story_planner.apply_steering = AsyncMock(
        return_value=new_arc or [f"revised beat {i}" for i in range(1, 6)]
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


def _make_queue(*turns: VoiceTurn | None) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    for t in turns:
        q.put_nowait(t)
    return q


# ---------------------------------------------------------------------------
# TC-1: Spec "Done when" — 10 s timeout fires steering_window_closed(reason="timeout")
# ---------------------------------------------------------------------------


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_emits_steering_window_closed_timeout(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        # Use a tiny window so test doesn't wait 10 s
        await handler.run_steering_window(
            session_id="s1",
            page_number=1,
            emit=emit,
            window_seconds=0.05,
            turn_queue=None,  # no queue → pure timeout
        )

        event_types = [e["type"] for e in events]
        assert "steering_window_open" in event_types
        closed = next(e for e in events if e["type"] == "steering_window_closed")
        assert closed["reason"] == "timeout"

    @pytest.mark.asyncio
    async def test_timeout_with_empty_queue_also_times_out(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q: asyncio.Queue = asyncio.Queue()  # nothing in queue

        await handler.run_steering_window(
            session_id="s1",
            page_number=1,
            emit=emit,
            window_seconds=0.05,
            turn_queue=q,
        )

        closed = next(e for e in events if e["type"] == "steering_window_closed")
        assert closed["reason"] == "timeout"


# ---------------------------------------------------------------------------
# TC-2: Spec "Done when" — tone_change flow emits voice_command_received then
#        voice_command_applied
# ---------------------------------------------------------------------------


class TestToneChangeFlow:
    @pytest.mark.asyncio
    async def test_tone_change_emits_received_then_applied(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it funnier"))

        await handler.run_steering_window(
            session_id="s1",
            page_number=2,
            emit=emit,
            window_seconds=5.0,
            turn_queue=q,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_received" in event_types
        assert "voice_command_applied" in event_types

        # voice_command_received MUST come before voice_command_applied
        idx_received = event_types.index("voice_command_received")
        idx_applied = event_types.index("voice_command_applied")
        assert idx_received < idx_applied

    @pytest.mark.asyncio
    async def test_tone_change_command_type_is_tone_change(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it funnier"))
        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        received = next(e for e in events if e["type"] == "voice_command_received")
        assert received["command_type"] == CommandType.tone_change.value

    @pytest.mark.asyncio
    async def test_steering_window_closed_after_applied(self) -> None:
        """TC-5: steering_window_closed fires after voice_command_applied."""
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it funnier"))
        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_applied" in event_types
        idx_applied = event_types.index("voice_command_applied")
        idx_closed = event_types.index("steering_window_closed")
        assert idx_applied < idx_closed


# ---------------------------------------------------------------------------
# TC-3: Spec "Done when" — ambiguous triggers one clarifying speak call before
#        re-classification
# ---------------------------------------------------------------------------


class TestAmbiguousFlow:
    @pytest.mark.asyncio
    async def test_ambiguous_triggers_one_clarifying_speak(self) -> None:
        handler, mocks = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        # First turn is ambiguous, second turn is a tone_change
        q = _make_queue(
            _make_turn("make it different"),    # ambiguous
            _make_turn("make it funnier"),       # tone_change
        )

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        # Exactly one speak call for the clarifying question
        mocks["voice_svc"].speak.assert_called_once()
        speak_arg = mocks["voice_svc"].speak.call_args[0][1]
        assert "funnier" in speak_arg or "shorter" in speak_arg or "else" in speak_arg

    @pytest.mark.asyncio
    async def test_ambiguous_then_classified_applies_command(self) -> None:
        handler, mocks = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(
            _make_turn("make it different"),
            _make_turn("make it funnier"),
        )

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_applied" in event_types

    @pytest.mark.asyncio
    async def test_ambiguous_no_second_turn_closes_with_ambiguous(self) -> None:
        handler, mocks = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it different"))  # only ambiguous turn

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=0.05, turn_queue=q,
        )

        closed = next(e for e in events if e["type"] == "steering_window_closed")
        assert closed["reason"] in ("ambiguous", "timeout")


# ---------------------------------------------------------------------------
# TC-4: Spec "Done when" — character_introduction adds character to CharacterBible
# ---------------------------------------------------------------------------


class TestCharacterIntroduction:
    @pytest.mark.asyncio
    async def test_character_introduction_calls_add_secondary_character(self) -> None:
        handler, mocks = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("add a wise owl to the story"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        mocks["char_bible_svc"].add_secondary_character.assert_called_once()

    @pytest.mark.asyncio
    async def test_tone_change_does_not_add_character(self) -> None:
        handler, mocks = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it funnier"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        mocks["char_bible_svc"].add_secondary_character.assert_not_called()


# ---------------------------------------------------------------------------
# TC-5: Already covered inside TestToneChangeFlow.test_steering_window_closed_after_applied
#        Additional: unsafe turn closes window with "unsafe" reason
# ---------------------------------------------------------------------------


class TestUnsafeTurn:
    @pytest.mark.asyncio
    async def test_unsafe_turn_closes_window_with_unsafe_reason(self) -> None:
        handler, _ = _make_handler(safety_result=_unsafe_result())
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("hurt the character"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        closed = next(e for e in events if e["type"] == "steering_window_closed")
        assert closed["reason"] == "unsafe"

    @pytest.mark.asyncio
    async def test_unsafe_turn_does_not_apply_command(self) -> None:
        handler, mocks = _make_handler(safety_result=_unsafe_result())
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("hurt the character"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        event_types = [e["type"] for e in events]
        assert "voice_command_applied" not in event_types
        mocks["story_planner"].apply_steering.assert_not_called()


# ---------------------------------------------------------------------------
# TC-6: apply_steering and update_story_arc are called on successful classification
# ---------------------------------------------------------------------------


class TestArcUpdate:
    @pytest.mark.asyncio
    async def test_apply_steering_called_with_correct_from_page(self) -> None:
        handler, mocks = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it faster"))

        await handler.run_steering_window(
            session_id="s1", page_number=3, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        # from_page should be page_number + 1 = 4
        call_kwargs = mocks["story_planner"].apply_steering.call_args
        assert call_kwargs.kwargs["from_page"] == 4

    @pytest.mark.asyncio
    async def test_update_story_arc_called_with_new_arc(self) -> None:
        revised = [f"new beat {i}" for i in range(1, 6)]
        handler, mocks = _make_handler(new_arc=revised)
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it faster"))

        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        mocks["store"].update_story_arc.assert_called_once_with("s1", revised)


# ---------------------------------------------------------------------------
# TC-7: user_silent turn closes window with "user_silent" reason
# ---------------------------------------------------------------------------


class TestUserSilent:
    @pytest.mark.asyncio
    async def test_empty_transcript_gives_user_silent(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("   "))  # whitespace only

        await handler.run_steering_window(
            session_id="s1", page_number=1, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        closed = next(e for e in events if e["type"] == "steering_window_closed")
        assert closed["reason"] == "user_silent"


# ---------------------------------------------------------------------------
# TC-8: steering_window_open is always the first emitted event
# ---------------------------------------------------------------------------


class TestEventOrder:
    @pytest.mark.asyncio
    async def test_steering_window_open_is_first_event(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        await handler.run_steering_window(
            session_id="s1", page_number=1, emit=emit,
            window_seconds=0.05, turn_queue=None,
        )

        assert events[0]["type"] == "steering_window_open"

    @pytest.mark.asyncio
    async def test_steering_window_closed_is_last_event(self) -> None:
        handler, _ = _make_handler()
        events: list[dict] = []

        async def emit(event_type: str, **fields: object) -> None:
            events.append({"type": event_type, **fields})

        q = _make_queue(_make_turn("make it funnier"))
        await handler.run_steering_window(
            session_id="s1", page_number=2, emit=emit,
            window_seconds=5.0, turn_queue=q,
        )

        assert events[-1]["type"] == "steering_window_closed"


# ---------------------------------------------------------------------------
# TC-9: _derive_char_ref unit test
# ---------------------------------------------------------------------------


class TestDeriveCharRef:
    def test_derives_name_from_intent(self) -> None:
        command = VoiceCommand(
            command_id=uuid4(),
            turn_id=uuid4(),
            raw_transcript="add a wise owl",
            interpreted_intent="add a wise owl to the story",
            command_type=CommandType.character_introduction,
            applied_to_pages=[3, 4, 5],
            received_at=datetime.now(timezone.utc),
        )
        char_ref = _derive_char_ref(command, page_number=3)
        assert char_ref.introduced_on_page == 3
        assert char_ref.char_id is not None
        assert char_ref.name is not None
