"""
Tests for T-020: SetupHandler — multi-turn story parameter extraction.

Strategy
--------
All Gemini calls, sub-services, and stores are mocked.  SetupHandler._extract_params
is patched via the injectable constructor (client=mock) or via AsyncMock on the
handler instance, keeping tests fast and deterministic.

Covers:
  SetupState
    T20-01  all_confirmed is False when no params set
    T20-02  all_confirmed is False when protagonist missing description
    T20-03  all_confirmed is False when setting missing
    T20-04  all_confirmed is False when tone missing
    T20-05  all_confirmed is True when all four fields set
    T20-06  has_protagonist is True only when both name and description are set
    T20-07  has_setting / has_tone simple True/False tests

  SetupHandler.handle — single-turn full params
    T20-08  all params in one turn → story_brief_confirmed emitted
    T20-09  all params in one turn → character_bible_ready emitted
    T20-10  all params in one turn → zero voice_svc.speak calls
    T20-11  story_brief_confirmed has 'brief' and 'agent_summary' fields
    T20-12  character_bible_ready has 'session_id' field

  SetupHandler.handle — partial params / follow-up
    T20-13  one param given → story_brief_updated emitted for that param
    T20-14  one param given → story_brief_updated not emitted for missing params
    T20-15  one param given → voice_svc.speak called once with follow-up question
    T20-16  follow-up from Gemini is passed to voice_svc.speak
    T20-17  default follow-up used when Gemini returns no follow_up_question

  SetupHandler.handle — two-turn completion
    T20-18  second turn that completes params → story_brief_confirmed emitted
    T20-19  turn_count incremented each call

  SetupHandler.handle — turn limit (MAX_SETUP_TURNS)
    T20-20  third turn proceeds to completion even when params still missing
    T20-21  fallback protagonist_name used when not collected by turn limit
    T20-22  fallback setting used when not collected by turn limit
    T20-23  fallback tone (warm) used when not collected by turn limit

  SetupHandler.handle — persistence
    T20-24  store.save_story_brief called with StoryBrief
    T20-25  store.update_story_arc called with beats list
    T20-26  store.update_session_status called with SessionStatus.generating

  SetupHandler.handle — sub-service calls
    T20-27  story_planner.create_arc called with brief and minimal_bible
    T20-28  bible_svc.initialise called with session_id and brief

  SetupHandler.handle — ordering
    T20-29  story_brief_confirmed emitted before character_bible_ready

  SetupHandler.handle — resilience
    T20-30  story_planner failure → session continues; character_bible_ready still emitted
    T20-31  bible_svc failure → character_bible_ready still emitted
    T20-32  store.update_session_status failure → no exception raised

  _extract_params
    T20-33  Gemini failure → returns empty ExtractedParams (no exception)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.session import SessionStatus
from app.websocket.setup_handler import (
    ExtractedParams,
    SetupHandler,
    SetupState,
)

# ---------------------------------------------------------------------------
# Constants & factories
# ---------------------------------------------------------------------------

SESSION_ID = str(uuid.uuid4())
NOW = datetime.now(timezone.utc)

FULL_EXTRACTED = ExtractedParams(
    protagonist_name="Pip",
    protagonist_description="a small blue rabbit with soft fur",
    setting="the Meadow",
    tone="sleepy",
    follow_up_question=None,
)

PARTIAL_EXTRACTED = ExtractedParams(
    protagonist_name="Pip",
    protagonist_description="a small blue rabbit",
    setting=None,
    tone=None,
    follow_up_question="Where does Pip's adventure take place?",
)


def _mock_ws() -> MagicMock:
    ws = MagicMock()
    ws.send_json = AsyncMock()
    return ws


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.save_story_brief = AsyncMock()
    store.update_story_arc = AsyncMock()
    store.update_session_status = AsyncMock()
    store.get_character_bible = AsyncMock(return_value=None)
    return store


def _mock_voice_svc() -> MagicMock:
    svc = MagicMock()
    svc.speak = AsyncMock()
    return svc


def _make_handler(
    extracted: ExtractedParams | None = None,
    beats: list[str] | None = None,
) -> SetupHandler:
    """
    Build a SetupHandler with mocked sub-services.

    _extract_params is patched to return *extracted* (defaults to all-None).
    story_planner.create_arc returns *beats* (defaults to 5 dummy beats).
    bible_svc.initialise returns a mock CharacterBible.
    """
    handler = SetupHandler()

    # Patch _extract_params
    handler._extract_params = AsyncMock(  # type: ignore[method-assign]
        return_value=extracted if extracted is not None else ExtractedParams()
    )

    # Mock story_planner
    mock_planner = MagicMock()
    mock_planner.create_arc = AsyncMock(
        return_value=beats
        or [
            "Pip wakes up in the Meadow.",
            "A butterfly leads Pip astray.",
            "Pip gets lost and feels sad.",
            "A friendly hedgehog shows the way.",
            "Pip returns home safe and sleepy.",
        ]
    )
    handler._story_planner = mock_planner

    # Mock bible_svc
    mock_bible = MagicMock()
    mock_bible.initialise = AsyncMock(return_value=MagicMock())
    handler._bible_svc = mock_bible

    return handler


def _events_of(ws: MagicMock) -> list[dict]:
    """Return all JSON payloads sent via ws.send_json."""
    return [call.args[0] for call in ws.send_json.call_args_list]


# ---------------------------------------------------------------------------
# T20-01 … T20-07  SetupState properties
# ---------------------------------------------------------------------------


class TestSetupState:
    def test_all_confirmed_false_when_empty(self):
        state = SetupState()
        assert state.all_confirmed is False

    def test_all_confirmed_false_missing_description(self):
        state = SetupState(
            protagonist_name="Pip",
            protagonist_description=None,
            setting="Meadow",
            tone="sleepy",
        )
        assert state.all_confirmed is False

    def test_all_confirmed_false_missing_setting(self):
        state = SetupState(
            protagonist_name="Pip",
            protagonist_description="a rabbit",
            setting=None,
            tone="sleepy",
        )
        assert state.all_confirmed is False

    def test_all_confirmed_false_missing_tone(self):
        state = SetupState(
            protagonist_name="Pip",
            protagonist_description="a rabbit",
            setting="Meadow",
            tone=None,
        )
        assert state.all_confirmed is False

    def test_all_confirmed_true_when_all_set(self):
        state = SetupState(
            protagonist_name="Pip",
            protagonist_description="a small blue rabbit",
            setting="the Meadow",
            tone="sleepy",
        )
        assert state.all_confirmed is True

    def test_has_protagonist_requires_both(self):
        state = SetupState(protagonist_name="Pip", protagonist_description=None)
        assert state.has_protagonist is False
        state.protagonist_description = "a rabbit"
        assert state.has_protagonist is True

    def test_has_setting_and_tone(self):
        state = SetupState()
        assert state.has_setting is False
        assert state.has_tone is False
        state.setting = "Forest"
        state.tone = "silly"
        assert state.has_setting is True
        assert state.has_tone is True


# ---------------------------------------------------------------------------
# T20-08 … T20-12  Single-turn full params
# ---------------------------------------------------------------------------


class TestSingleTurnFullParams:
    @pytest.mark.asyncio
    async def test_story_brief_confirmed_emitted(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        types = [e["type"] for e in _events_of(ws)]
        assert "story_brief_confirmed" in types

    @pytest.mark.asyncio
    async def test_character_bible_ready_emitted(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        types = [e["type"] for e in _events_of(ws)]
        assert "character_bible_ready" in types

    @pytest.mark.asyncio
    async def test_zero_speak_calls_when_complete(self):
        ws = _mock_ws()
        voice_svc = _mock_voice_svc()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, voice_svc, SetupState(), _mock_store())
        voice_svc.speak.assert_not_called()

    @pytest.mark.asyncio
    async def test_story_brief_confirmed_has_brief_field(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        confirmed = next(e for e in _events_of(ws) if e["type"] == "story_brief_confirmed")
        assert "brief" in confirmed

    @pytest.mark.asyncio
    async def test_story_brief_confirmed_has_agent_summary(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        confirmed = next(e for e in _events_of(ws) if e["type"] == "story_brief_confirmed")
        assert "agent_summary" in confirmed
        assert isinstance(confirmed["agent_summary"], str)

    @pytest.mark.asyncio
    async def test_character_bible_ready_has_session_id(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        ready = next(e for e in _events_of(ws) if e["type"] == "character_bible_ready")
        assert ready["session_id"] == SESSION_ID


# ---------------------------------------------------------------------------
# T20-13 … T20-17  Partial params / follow-up
# ---------------------------------------------------------------------------


class TestPartialParams:
    @pytest.mark.asyncio
    async def test_story_brief_updated_emitted_for_extracted_param(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=PARTIAL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        updated_types = [
            e["parameter"]
            for e in _events_of(ws)
            if e["type"] == "story_brief_updated"
        ]
        assert "protagonist_name" in updated_types

    @pytest.mark.asyncio
    async def test_story_brief_updated_not_emitted_for_missing_params(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=PARTIAL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        updated_params = {
            e["parameter"]
            for e in _events_of(ws)
            if e["type"] == "story_brief_updated"
        }
        assert "setting" not in updated_params
        assert "tone" not in updated_params

    @pytest.mark.asyncio
    async def test_speak_called_once_for_follow_up(self):
        ws = _mock_ws()
        voice_svc = _mock_voice_svc()
        handler = _make_handler(extracted=PARTIAL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, voice_svc, SetupState(), _mock_store())
        voice_svc.speak.assert_called_once()

    @pytest.mark.asyncio
    async def test_follow_up_from_gemini_passed_to_speak(self):
        ws = _mock_ws()
        voice_svc = _mock_voice_svc()
        handler = _make_handler(extracted=PARTIAL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, voice_svc, SetupState(), _mock_store())
        _, pos_args, _ = voice_svc.speak.mock_calls[0]
        assert pos_args[1] == PARTIAL_EXTRACTED.follow_up_question

    @pytest.mark.asyncio
    async def test_default_follow_up_when_gemini_returns_none(self):
        ws = _mock_ws()
        voice_svc = _mock_voice_svc()
        extracted = ExtractedParams(
            protagonist_name="Pip",
            protagonist_description="a rabbit",
            setting=None,
            tone=None,
            follow_up_question=None,  # Gemini returned no question
        )
        handler = _make_handler(extracted=extracted)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, voice_svc, SetupState(), _mock_store())
        voice_svc.speak.assert_called_once()
        _, pos_args, _ = voice_svc.speak.mock_calls[0]
        assert isinstance(pos_args[1], str) and len(pos_args[1]) > 0


# ---------------------------------------------------------------------------
# T20-18 … T20-19  Two-turn completion
# ---------------------------------------------------------------------------


class TestTwoTurnCompletion:
    @pytest.mark.asyncio
    async def test_second_turn_completes_params_emits_confirmed(self):
        state = SetupState()
        store = _mock_store()
        voice_svc = _mock_voice_svc()

        handler = SetupHandler()
        mock_planner = MagicMock()
        mock_planner.create_arc = AsyncMock(
            return_value=["b1", "b2", "b3", "b4", "b5"]
        )
        mock_bible = MagicMock()
        mock_bible.initialise = AsyncMock(return_value=MagicMock())
        handler._story_planner = mock_planner
        handler._bible_svc = mock_bible

        # First turn: partial
        handler._extract_params = AsyncMock(  # type: ignore[method-assign]
            return_value=PARTIAL_EXTRACTED
        )
        ws1 = _mock_ws()
        await handler.handle(ws1, MagicMock(transcript="Pip the rabbit"), SESSION_ID, voice_svc, state, store)
        assert not state.all_confirmed

        # Second turn: completes with setting + tone
        handler._extract_params = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractedParams(
                protagonist_name=None,
                protagonist_description=None,
                setting="the Meadow",
                tone="sleepy",
                follow_up_question=None,
            )
        )
        ws2 = _mock_ws()
        await handler.handle(ws2, MagicMock(transcript="sleepy meadow"), SESSION_ID, voice_svc, state, store)
        types2 = [e["type"] for e in _events_of(ws2)]
        assert "story_brief_confirmed" in types2

    @pytest.mark.asyncio
    async def test_turn_count_incremented_each_call(self):
        state = SetupState()
        handler = _make_handler(extracted=PARTIAL_EXTRACTED)
        voice_svc = _mock_voice_svc()
        store = _mock_store()
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, voice_svc, state, store)
        assert state.turn_count == 1
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, voice_svc, state, store)
        assert state.turn_count == 2


# ---------------------------------------------------------------------------
# T20-20 … T20-23  Turn limit fallbacks
# ---------------------------------------------------------------------------


class TestTurnLimit:
    @pytest.mark.asyncio
    async def test_third_turn_completes_even_with_missing_params(self):
        state = SetupState(turn_count=2)  # Already used 2 turns
        ws = _mock_ws()
        handler = _make_handler(extracted=ExtractedParams())  # nothing extracted
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), state, _mock_store())
        types = [e["type"] for e in _events_of(ws)]
        assert "story_brief_confirmed" in types

    @pytest.mark.asyncio
    async def test_fallback_protagonist_name_in_brief(self):
        state = SetupState(turn_count=2)  # 3rd turn will trigger completion
        ws = _mock_ws()
        handler = _make_handler(extracted=ExtractedParams())
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), state, _mock_store())
        confirmed = next(e for e in _events_of(ws) if e["type"] == "story_brief_confirmed")
        assert confirmed["brief"]["protagonist_name"] == "the hero"

    @pytest.mark.asyncio
    async def test_fallback_setting_in_brief(self):
        state = SetupState(turn_count=2)
        ws = _mock_ws()
        handler = _make_handler(extracted=ExtractedParams())
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), state, _mock_store())
        confirmed = next(e for e in _events_of(ws) if e["type"] == "story_brief_confirmed")
        assert confirmed["brief"]["setting"] == "a magical land"

    @pytest.mark.asyncio
    async def test_fallback_tone_is_warm(self):
        state = SetupState(turn_count=2)
        ws = _mock_ws()
        handler = _make_handler(extracted=ExtractedParams())
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), state, _mock_store())
        confirmed = next(e for e in _events_of(ws) if e["type"] == "story_brief_confirmed")
        assert confirmed["brief"]["tone"] == "warm"


# ---------------------------------------------------------------------------
# T20-24 … T20-26  Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_story_brief_called(self):
        store = _mock_store()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), store)
        store.save_story_brief.assert_called_once()
        _, pos_args, _ = store.save_story_brief.mock_calls[0]
        assert pos_args[0] == SESSION_ID

    @pytest.mark.asyncio
    async def test_update_story_arc_called(self):
        store = _mock_store()
        beats = ["b1", "b2", "b3", "b4", "b5"]
        handler = _make_handler(extracted=FULL_EXTRACTED, beats=beats)
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), store)
        store.update_story_arc.assert_called_once()
        _, pos_args, _ = store.update_story_arc.mock_calls[0]
        assert pos_args[0] == SESSION_ID
        assert pos_args[1] == beats

    @pytest.mark.asyncio
    async def test_update_session_status_called_with_generating(self):
        store = _mock_store()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), store)
        store.update_session_status.assert_called_once()
        _, pos_args, _ = store.update_session_status.mock_calls[0]
        assert pos_args[0] == SESSION_ID
        assert pos_args[1] == SessionStatus.generating


# ---------------------------------------------------------------------------
# T20-27 … T20-28  Sub-service calls
# ---------------------------------------------------------------------------


class TestSubServiceCalls:
    @pytest.mark.asyncio
    async def test_story_planner_create_arc_called(self):
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        handler._story_planner.create_arc.assert_called_once()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_bible_svc_initialise_called(self):
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        handler._bible_svc.initialise.assert_called_once()  # type: ignore[attr-defined]
        _, pos_args, _ = handler._bible_svc.initialise.mock_calls[0]  # type: ignore[attr-defined]
        assert pos_args[0] == SESSION_ID


# ---------------------------------------------------------------------------
# T20-29  Event ordering
# ---------------------------------------------------------------------------


class TestEventOrdering:
    @pytest.mark.asyncio
    async def test_story_brief_confirmed_before_character_bible_ready(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        types = [e["type"] for e in _events_of(ws)]
        idx_confirmed = types.index("story_brief_confirmed")
        idx_ready = types.index("character_bible_ready")
        assert idx_confirmed < idx_ready


# ---------------------------------------------------------------------------
# T20-30 … T20-32  Resilience
# ---------------------------------------------------------------------------


class TestResilience:
    @pytest.mark.asyncio
    async def test_story_planner_failure_still_emits_character_bible_ready(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        handler._story_planner.create_arc = AsyncMock(side_effect=RuntimeError("arc fail"))  # type: ignore[attr-defined]
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        types = [e["type"] for e in _events_of(ws)]
        assert "character_bible_ready" in types

    @pytest.mark.asyncio
    async def test_bible_svc_failure_still_emits_character_bible_ready(self):
        ws = _mock_ws()
        handler = _make_handler(extracted=FULL_EXTRACTED)
        handler._bible_svc.initialise = AsyncMock(side_effect=RuntimeError("bible fail"))  # type: ignore[attr-defined]
        await handler.handle(ws, MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), _mock_store())
        types = [e["type"] for e in _events_of(ws)]
        assert "character_bible_ready" in types

    @pytest.mark.asyncio
    async def test_store_status_update_failure_no_exception_raised(self):
        store = _mock_store()
        store.update_session_status = AsyncMock(side_effect=RuntimeError("firestore down"))
        handler = _make_handler(extracted=FULL_EXTRACTED)
        # Should not raise
        await handler.handle(_mock_ws(), MagicMock(transcript=""), SESSION_ID, _mock_voice_svc(), SetupState(), store)


# ---------------------------------------------------------------------------
# T20-33  _extract_params resilience
# ---------------------------------------------------------------------------


class TestExtractParams:
    @pytest.mark.asyncio
    async def test_gemini_failure_returns_empty_extracted_params(self):
        handler = SetupHandler()
        handler._client = MagicMock()  # type: ignore[assignment]
        handler._client.aio = MagicMock()
        handler._client.aio.models = MagicMock()
        handler._client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API error")
        )
        result = await handler._extract_params("some text", SetupState())
        assert result.protagonist_name is None
        assert result.protagonist_description is None
        assert result.setting is None
        assert result.tone is None
        assert result.follow_up_question is None
