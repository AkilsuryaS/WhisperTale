"""
Tests for T-006 (part 2): Pydantic v2 models — VoiceCommand, SafetyDecision,
SafetyResult, and SAFE_FALLBACK_REWRITE.

Covers:
- CommandType enum (all 4 values)
- VoiceCommand: construction, defaults, auto-generated command_id,
  optional new_character_ref_id and safety_decision_id
- SafetyCategory enum (all 6 values)
- SafetyDecision: construction, all required fields, optional fields,
  auto-generated decision_id
- SafetyResult dataclass: safe/unsafe variants, defaults
- SAFE_FALLBACK_REWRITE: importable, non-empty, child-safe string
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.safety import (
    SAFE_FALLBACK_REWRITE,
    SafetyCategory,
    SafetyDecision,
    SafetyResult,
)
from app.models.voice_command import CommandType, VoiceCommand

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_voice_command(**overrides) -> VoiceCommand:
    defaults = dict(
        turn_id=uuid4(),
        raw_transcript="Make the story more exciting",
        interpreted_intent="increase pacing from page 3 onward",
        command_type=CommandType.pacing_change,
        applied_to_pages=[3, 4, 5],
        safe=True,
        received_at=NOW,
    )
    defaults.update(overrides)
    return VoiceCommand(**defaults)


def make_safety_decision(**overrides) -> SafetyDecision:
    defaults = dict(
        turn_id=uuid4(),
        phase="setup",
        raw_input="Let's add lots of blood and gore",
        detected_category=SafetyCategory.gore,
        proposed_rewrite="How about a colorful painting adventure instead?",
        user_accepted=True,
        triggered_at=NOW,
    )
    defaults.update(overrides)
    return SafetyDecision(**defaults)


# ---------------------------------------------------------------------------
# CommandType enum
# ---------------------------------------------------------------------------


class TestCommandTypeEnum:
    @pytest.mark.parametrize(
        "cmd_type",
        ["tone_change", "pacing_change", "element_reintroduction", "character_introduction"],
    )
    def test_all_values_accepted(self, cmd_type):
        vc = make_voice_command(command_type=cmd_type)
        assert vc.command_type == cmd_type

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            make_voice_command(command_type="volume_change")


# ---------------------------------------------------------------------------
# VoiceCommand — construction
# ---------------------------------------------------------------------------


class TestVoiceCommandConstruction:
    def test_minimal_command_constructs(self):
        vc = make_voice_command()
        assert isinstance(vc.command_id, UUID)
        assert vc.command_type == "pacing_change"
        assert vc.safe is True
        assert vc.new_character_ref_id is None
        assert vc.safety_decision_id is None

    def test_command_id_auto_generated(self):
        vc1 = make_voice_command()
        vc2 = make_voice_command()
        assert vc1.command_id != vc2.command_id

    def test_use_enum_values_stores_string(self):
        vc = make_voice_command(command_type=CommandType.tone_change)
        assert vc.command_type == "tone_change"
        assert isinstance(vc.command_type, str)

    def test_applied_to_pages_stored(self):
        vc = make_voice_command(applied_to_pages=[4, 5])
        assert vc.applied_to_pages == [4, 5]

    def test_empty_applied_to_pages_valid(self):
        vc = make_voice_command(applied_to_pages=[])
        assert vc.applied_to_pages == []

    def test_character_introduction_with_ref_id(self):
        vc = make_voice_command(
            command_type="character_introduction",
            new_character_ref_id="yellow_bird",
            applied_to_pages=[3, 4, 5],
        )
        assert vc.new_character_ref_id == "yellow_bird"
        assert vc.command_type == "character_introduction"

    def test_unsafe_command_with_safety_decision_id(self):
        sd_id = uuid4()
        vc = make_voice_command(safe=False, safety_decision_id=sd_id)
        assert vc.safe is False
        assert vc.safety_decision_id == sd_id

    def test_turn_id_required(self):
        with pytest.raises(ValidationError):
            VoiceCommand(
                raw_transcript="test",
                interpreted_intent="test",
                command_type="tone_change",
                safe=True,
                received_at=NOW,
            )

    def test_two_commands_have_independent_applied_to_pages(self):
        vc1 = make_voice_command(applied_to_pages=[3])
        vc2 = make_voice_command(applied_to_pages=[4])
        assert vc1.applied_to_pages != vc2.applied_to_pages


# ---------------------------------------------------------------------------
# SafetyCategory enum
# ---------------------------------------------------------------------------


class TestSafetyCategoryEnum:
    @pytest.mark.parametrize(
        "category",
        [
            "physical_harm",
            "character_death",
            "gore",
            "destruction",
            "sexual_content",
            "fear_escalation",
        ],
    )
    def test_all_six_categories_accepted(self, category):
        sd = make_safety_decision(detected_category=category)
        assert sd.detected_category == category

    def test_invalid_category_raises(self):
        with pytest.raises(ValidationError):
            make_safety_decision(detected_category="mild_rudeness")


# ---------------------------------------------------------------------------
# SafetyDecision — construction
# ---------------------------------------------------------------------------


class TestSafetyDecisionConstruction:
    def test_minimal_decision_constructs(self):
        sd = make_safety_decision()
        assert isinstance(sd.decision_id, UUID)
        assert sd.detected_category == "gore"
        assert sd.user_accepted is True
        assert sd.final_premise is None
        assert sd.exclusion_added is None

    def test_decision_id_auto_generated(self):
        sd1 = make_safety_decision()
        sd2 = make_safety_decision()
        assert sd1.decision_id != sd2.decision_id

    def test_use_enum_values_stores_string(self):
        sd = make_safety_decision(detected_category=SafetyCategory.physical_harm)
        assert sd.detected_category == "physical_harm"
        assert isinstance(sd.detected_category, str)

    def test_raw_input_stored(self):
        sd = make_safety_decision(raw_input="explicit unsafe content here")
        assert sd.raw_input == "explicit unsafe content here"

    def test_user_accepted_false(self):
        sd = make_safety_decision(user_accepted=False)
        assert sd.user_accepted is False

    def test_final_premise_stored(self):
        sd = make_safety_decision(
            user_accepted=True,
            final_premise="A fun painting adventure with bright colors",
        )
        assert sd.final_premise == "A fun painting adventure with bright colors"

    def test_exclusion_added_stored(self):
        sd = make_safety_decision(exclusion_added="no gore")
        assert sd.exclusion_added == "no gore"

    def test_all_phases_accepted(self):
        for phase in ("setup", "steering"):
            sd = make_safety_decision(phase=phase)
            assert sd.phase == phase

    def test_narration_phase_raises(self):
        """SafetyDecision phase is only setup or steering — never narration."""
        with pytest.raises(ValidationError):
            make_safety_decision(phase="narration")

    def test_turn_id_required(self):
        with pytest.raises(ValidationError):
            SafetyDecision(
                phase="setup",
                raw_input="bad content",
                detected_category="gore",
                proposed_rewrite="safe alternative",
                user_accepted=True,
                triggered_at=NOW,
            )


# ---------------------------------------------------------------------------
# SafetyResult dataclass
# ---------------------------------------------------------------------------


class TestSafetyResult:
    def test_safe_result_constructs(self):
        result = SafetyResult(safe=True)
        assert result.safe is True
        assert result.category is None
        assert result.rewrite is None

    def test_unsafe_result_constructs(self):
        result = SafetyResult(
            safe=False,
            category=SafetyCategory.physical_harm,
            rewrite="Let's go on a treasure hunt instead!",
        )
        assert result.safe is False
        assert result.category == SafetyCategory.physical_harm
        assert result.rewrite == "Let's go on a treasure hunt instead!"

    def test_unsafe_result_with_string_category(self):
        result = SafetyResult(safe=False, category="gore", rewrite="something safe")
        assert result.category == "gore"

    def test_safe_result_with_fallback_rewrite(self):
        result = SafetyResult(safe=False, category="destruction", rewrite=SAFE_FALLBACK_REWRITE)
        assert result.rewrite == SAFE_FALLBACK_REWRITE


# ---------------------------------------------------------------------------
# SAFE_FALLBACK_REWRITE constant
# ---------------------------------------------------------------------------


class TestSafeFallbackRewrite:
    def test_is_importable(self):
        from app.models.safety import SAFE_FALLBACK_REWRITE as sfr
        assert sfr is not None

    def test_is_non_empty_string(self):
        assert isinstance(SAFE_FALLBACK_REWRITE, str)
        assert len(SAFE_FALLBACK_REWRITE) > 0

    def test_contains_child_safe_premise(self):
        lower = SAFE_FALLBACK_REWRITE.lower()
        assert "adventure" in lower or "friend" in lower or "fun" in lower

    def test_does_not_contain_unsafe_terms(self):
        lower = SAFE_FALLBACK_REWRITE.lower()
        for term in ("blood", "death", "gore", "violence", "weapon"):
            assert term not in lower, f"Unsafe term '{term}' found in SAFE_FALLBACK_REWRITE"
