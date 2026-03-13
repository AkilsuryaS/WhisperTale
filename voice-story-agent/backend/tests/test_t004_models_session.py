"""
Tests for T-004: Pydantic v2 models — Session, UserTurn, StoryBrief.

Covers:
- All models import and construct without error
- Enum acceptance: string values and enum members
- Field validators: page_count=5, story_arc non-empty elements
- Session.is_ready_to_generate() logic
- UserTurn optional FK fields and page_context bounds
- StoryBrief max-length constraints (protagonist_name, setting)
- Tone enum maps all five values
- UUID auto-generation (session_id, turn_id)
- use_enum_values=True: stored value is a plain string
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.session import (
    Session,
    SessionStatus,
    Speaker,
    StoryBrief,
    Tone,
    TurnPhase,
    UserTurn,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def make_session(**overrides) -> Session:
    defaults = dict(
        session_id=uuid4(),
        status=SessionStatus.setup,
        created_at=NOW,
        updated_at=NOW,
        page_count=5,
        current_page=0,
        story_arc=[],
    )
    defaults.update(overrides)
    return Session(**defaults)


def make_user_turn(**overrides) -> UserTurn:
    defaults = dict(
        sequence=1,
        phase=TurnPhase.setup,
        speaker=Speaker.user,
        raw_transcript="Hello",
        caption_text="Hello",
        timestamp=NOW,
    )
    defaults.update(overrides)
    return UserTurn(**defaults)


def make_story_brief(**overrides) -> StoryBrief:
    defaults = dict(
        protagonist_name="Spark",
        protagonist_description="A small purple dragon with big golden eyes",
        setting="A mushroom forest at the edge of the world",
        tone=Tone.adventurous,
        raw_setup_transcript="I want a purple dragon in a mushroom forest",
        confirmed_at=NOW,
        confirmed_by_agent=True,
    )
    defaults.update(overrides)
    return StoryBrief(**defaults)


# ---------------------------------------------------------------------------
# Session — construction
# ---------------------------------------------------------------------------


class TestSessionConstruction:
    def test_minimal_session_constructs(self):
        s = make_session()
        assert s.status == "setup"
        assert s.page_count == 5
        assert s.current_page == 0
        assert s.story_arc == []
        assert s.error_message is None

    def test_session_id_is_auto_generated_uuid(self):
        s = Session(created_at=NOW, updated_at=NOW)
        assert isinstance(s.session_id, UUID)

    def test_session_id_accepts_explicit_uuid(self):
        uid = uuid4()
        s = make_session(session_id=uid)
        assert s.session_id == uid

    def test_use_enum_values_stores_string(self):
        s = make_session(status=SessionStatus.generating)
        assert s.status == "generating"
        assert isinstance(s.status, str)

    def test_string_status_accepted(self):
        s = make_session(status="complete")
        assert s.status == "complete"

    def test_error_message_stored_when_set(self):
        s = make_session(status="error", error_message="fatal error")
        assert s.error_message == "fatal error"

    def test_story_arc_with_five_beats(self):
        arc = ["Beat 1", "Beat 2", "Beat 3", "Beat 4", "Beat 5"]
        s = make_session(story_arc=arc)
        assert s.story_arc == arc


# ---------------------------------------------------------------------------
# Session — page_count validator
# ---------------------------------------------------------------------------


class TestSessionPageCountValidator:
    def test_page_count_5_is_valid(self):
        s = make_session(page_count=5)
        assert s.page_count == 5

    def test_page_count_not_5_raises(self):
        with pytest.raises(ValidationError, match="page_count must equal 5"):
            make_session(page_count=3)

    def test_page_count_1_raises(self):
        with pytest.raises(ValidationError):
            make_session(page_count=1)

    def test_page_count_10_raises(self):
        with pytest.raises(ValidationError):
            make_session(page_count=10)


# ---------------------------------------------------------------------------
# Session — story_arc validator
# ---------------------------------------------------------------------------


class TestSessionStoryArcValidator:
    def test_empty_arc_is_valid_during_setup(self):
        s = make_session(story_arc=[])
        assert s.story_arc == []

    def test_five_non_empty_beats_is_valid(self):
        arc = ["a", "b", "c", "d", "e"]
        s = make_session(story_arc=arc)
        assert s.story_arc == arc

    def test_empty_string_element_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            make_session(story_arc=["", "b", "c", "d", "e"])

    def test_whitespace_only_element_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            make_session(story_arc=["  ", "b", "c", "d", "e"])


# ---------------------------------------------------------------------------
# Session — is_ready_to_generate
# ---------------------------------------------------------------------------


class TestSessionIsReadyToGenerate:
    def test_true_when_exactly_five_non_empty_beats(self):
        s = make_session(story_arc=["a", "b", "c", "d", "e"])
        assert s.is_ready_to_generate() is True

    def test_false_when_arc_is_empty(self):
        s = make_session(story_arc=[])
        assert s.is_ready_to_generate() is False

    def test_false_when_fewer_than_five_beats(self):
        s = make_session(story_arc=["a", "b", "c"])
        assert s.is_ready_to_generate() is False

    def test_false_when_more_than_five_beats(self):
        s = make_session(story_arc=["a", "b", "c", "d", "e", "f"])
        assert s.is_ready_to_generate() is False


# ---------------------------------------------------------------------------
# UserTurn — construction
# ---------------------------------------------------------------------------


class TestUserTurnConstruction:
    def test_minimal_turn_constructs(self):
        t = make_user_turn()
        assert isinstance(t.turn_id, UUID)
        assert t.sequence == 1
        assert t.phase == "setup"
        assert t.speaker == "user"

    def test_turn_id_auto_generated(self):
        t1 = make_user_turn()
        t2 = make_user_turn()
        assert t1.turn_id != t2.turn_id

    def test_use_enum_values_stores_string(self):
        t = make_user_turn(phase=TurnPhase.steering, speaker=Speaker.agent)
        assert t.phase == "steering"
        assert t.speaker == "agent"
        assert isinstance(t.phase, str)

    def test_optional_fks_default_to_none(self):
        t = make_user_turn()
        assert t.voice_command_id is None
        assert t.safety_decision_id is None
        assert t.page_context is None

    def test_voice_command_id_accepts_uuid(self):
        uid = uuid4()
        t = make_user_turn(voice_command_id=uid)
        assert t.voice_command_id == uid

    def test_safety_decision_id_accepts_uuid(self):
        uid = uuid4()
        t = make_user_turn(safety_decision_id=uid)
        assert t.safety_decision_id == uid

    def test_page_context_valid_range(self):
        for page in range(1, 6):
            t = make_user_turn(page_context=page)
            assert t.page_context == page

    def test_page_context_zero_raises(self):
        with pytest.raises(ValidationError):
            make_user_turn(page_context=0)

    def test_page_context_six_raises(self):
        with pytest.raises(ValidationError):
            make_user_turn(page_context=6)

    def test_sequence_zero_raises(self):
        with pytest.raises(ValidationError):
            make_user_turn(sequence=0)

    def test_all_phases_accepted(self):
        for phase in ("setup", "steering", "narration"):
            t = make_user_turn(phase=phase)
            assert t.phase == phase

    def test_all_speakers_accepted(self):
        for speaker in ("user", "agent"):
            t = make_user_turn(speaker=speaker)
            assert t.speaker == speaker


# ---------------------------------------------------------------------------
# StoryBrief — construction
# ---------------------------------------------------------------------------


class TestStoryBriefConstruction:
    def test_minimal_brief_constructs(self):
        b = make_story_brief()
        assert b.protagonist_name == "Spark"
        assert b.tone == "adventurous"
        assert b.confirmed_by_agent is True

    def test_use_enum_values_stores_string(self):
        b = make_story_brief(tone=Tone.silly)
        assert b.tone == "silly"
        assert isinstance(b.tone, str)

    def test_additional_constraints_defaults_to_none(self):
        b = make_story_brief()
        assert b.additional_constraints is None

    def test_additional_constraints_accepts_list(self):
        b = make_story_brief(additional_constraints=["no dragons", "daytime only"])
        assert b.additional_constraints == ["no dragons", "daytime only"]

    def test_confirmed_by_agent_defaults_false(self):
        b = StoryBrief(
            protagonist_name="Luna",
            protagonist_description="A silver fox",
            setting="A snowy mountain",
            tone="sleepy",
            raw_setup_transcript="transcript",
            confirmed_at=NOW,
        )
        assert b.confirmed_by_agent is False


# ---------------------------------------------------------------------------
# StoryBrief — field length validators
# ---------------------------------------------------------------------------


class TestStoryBriefLengthValidators:
    def test_protagonist_name_exactly_80_chars_is_valid(self):
        b = make_story_brief(protagonist_name="A" * 80)
        assert len(b.protagonist_name) == 80

    def test_protagonist_name_81_chars_raises(self):
        with pytest.raises(ValidationError):
            make_story_brief(protagonist_name="A" * 81)

    def test_setting_exactly_200_chars_is_valid(self):
        b = make_story_brief(setting="S" * 200)
        assert len(b.setting) == 200

    def test_setting_201_chars_raises(self):
        with pytest.raises(ValidationError):
            make_story_brief(setting="S" * 201)


# ---------------------------------------------------------------------------
# Tone enum — all five values
# ---------------------------------------------------------------------------


class TestToneEnum:
    @pytest.mark.parametrize("tone", ["silly", "sleepy", "adventurous", "warm", "curious"])
    def test_all_tone_values_accepted(self, tone):
        b = make_story_brief(tone=tone)
        assert b.tone == tone

    def test_invalid_tone_raises(self):
        with pytest.raises(ValidationError):
            make_story_brief(tone="scary")

    def test_funny_tone_raises(self):
        """Unmapped tones (e.g. 'funny') must not be silently accepted."""
        with pytest.raises(ValidationError):
            make_story_brief(tone="funny")


# ---------------------------------------------------------------------------
# SessionStatus enum — all four values
# ---------------------------------------------------------------------------


class TestSessionStatusEnum:
    @pytest.mark.parametrize("status", ["setup", "generating", "complete", "error"])
    def test_all_status_values_accepted(self, status):
        s = make_session(status=status)
        assert s.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            make_session(status="unknown")
