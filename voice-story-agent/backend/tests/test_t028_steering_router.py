"""
test_t028_steering_router.py

Unit tests for T-028: SteeringRouter — classify_steering

All tests are pure (no I/O, no mocking of external services).
Covers every classification type, edge cases, and all spec "Done when" criteria.

Depends: T-028
"""

from __future__ import annotations

import pytest

from app.models.safety import SafetyResult
from app.models.voice_command import CommandType
from app.websocket.steering_router import SteeringClassification, classify_steering


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe() -> SafetyResult:
    return SafetyResult(safe=True, category=None, rewrite=None)


def _unsafe() -> SafetyResult:
    from app.models.safety import SafetyCategory

    return SafetyResult(safe=False, category=SafetyCategory.physical_harm, rewrite="rewrite")


# ---------------------------------------------------------------------------
# TC-1: Spec "Done when" — exact utterances from spec
# ---------------------------------------------------------------------------


class TestSpecDoneWhen:
    """Directly mirrors the four 'Done when' bullets in the T-028 spec."""

    def test_make_it_funnier_returns_tone_change(self) -> None:
        result = classify_steering("make it funnier", _safe())
        assert result.type == CommandType.tone_change

    def test_give_him_a_bird_friend_returns_character_introduction(self) -> None:
        result = classify_steering("give him a bird friend", _safe())
        assert result.type == CommandType.character_introduction

    def test_make_it_different_returns_ambiguous(self) -> None:
        result = classify_steering("make it different", _safe())
        assert result.type == "ambiguous"

    def test_hurt_the_bird_unsafe_returns_unsafe_regardless(self) -> None:
        result = classify_steering("hurt the bird", _unsafe())
        assert result.type == "unsafe"


# ---------------------------------------------------------------------------
# TC-2: Unsafe — safety_result.safe == False always wins
# ---------------------------------------------------------------------------


class TestUnsafeAlwaysWins:
    def test_unsafe_wins_over_tone_change_keyword(self) -> None:
        """Even if utterance contains a tone keyword, unsafe wins."""
        result = classify_steering("make it funnier and hurt him", _unsafe())
        assert result.type == "unsafe"

    def test_unsafe_wins_over_character_introduction_keyword(self) -> None:
        result = classify_steering("add a new character", _unsafe())
        assert result.type == "unsafe"

    def test_unsafe_confidence_is_1_0(self) -> None:
        result = classify_steering("anything goes", _unsafe())
        assert result.confidence == 1.0

    def test_unsafe_detail_mentions_safety(self) -> None:
        result = classify_steering("bad request", _unsafe())
        assert result.detail is not None
        assert "Safety" in result.detail or "safety" in result.detail


# ---------------------------------------------------------------------------
# TC-3: Tone change keywords
# ---------------------------------------------------------------------------


class TestToneChange:
    @pytest.mark.parametrize(
        "utterance",
        [
            "make it funnier",
            "can you make this sillier",
            "let's make it calmer",
            "I want it scarier",
            "make it more exciting please",
            "make the story sleepier",
        ],
    )
    def test_tone_change_detected(self, utterance: str) -> None:
        result = classify_steering(utterance, _safe())
        assert result.type == CommandType.tone_change

    def test_tone_change_case_insensitive(self) -> None:
        result = classify_steering("MAKE IT FUNNIER", _safe())
        assert result.type == CommandType.tone_change

    def test_tone_change_confidence(self) -> None:
        result = classify_steering("make it funnier", _safe())
        assert result.confidence == pytest.approx(0.9)

    def test_tone_change_detail_contains_matched_word(self) -> None:
        result = classify_steering("make it funnier", _safe())
        assert result.detail is not None
        assert "funnier" in result.detail


# ---------------------------------------------------------------------------
# TC-4: Pacing change keywords
# ---------------------------------------------------------------------------


class TestPacingChange:
    @pytest.mark.parametrize(
        "utterance",
        [
            "make it go faster",
            "can we make it slower",
            "keep it shorter please",
            "I want it longer",
            "add more detail to that part",
        ],
    )
    def test_pacing_change_detected(self, utterance: str) -> None:
        result = classify_steering(utterance, _safe())
        assert result.type == CommandType.pacing_change

    def test_pacing_change_case_insensitive(self) -> None:
        result = classify_steering("Go FASTER", _safe())
        assert result.type == CommandType.pacing_change


# ---------------------------------------------------------------------------
# TC-5: Element reintroduction keywords
# ---------------------------------------------------------------------------


class TestElementReintroduction:
    @pytest.mark.parametrize(
        "utterance",
        [
            "can you bring back the dragon",
            "remember the red balloon",
            "what happened to the little frog",
        ],
    )
    def test_element_reintroduction_detected(self, utterance: str) -> None:
        result = classify_steering(utterance, _safe())
        assert result.type == CommandType.element_reintroduction

    def test_element_reintroduction_case_insensitive(self) -> None:
        result = classify_steering("BRING BACK the dragon", _safe())
        assert result.type == CommandType.element_reintroduction


# ---------------------------------------------------------------------------
# TC-6: Character introduction keywords
# ---------------------------------------------------------------------------


class TestCharacterIntroduction:
    @pytest.mark.parametrize(
        "utterance",
        [
            "add a dragon to the story",
            "give him a new friend",
            "give her a magic wand",
            "give them a companion",
            "introduce a wise owl",
            "can there be a new friend",
            "let's have a new character join",
        ],
    )
    def test_character_introduction_detected(self, utterance: str) -> None:
        result = classify_steering(utterance, _safe())
        assert result.type == CommandType.character_introduction

    def test_character_introduction_case_insensitive(self) -> None:
        result = classify_steering("ADD A wizard", _safe())
        assert result.type == CommandType.character_introduction

    def test_spec_example_give_him_a_bird_friend(self) -> None:
        """Exact utterance from spec 'Done when' section."""
        result = classify_steering("give him a bird friend", _safe())
        assert result.type == CommandType.character_introduction


# ---------------------------------------------------------------------------
# TC-7: Ambiguous — no pattern matches
# ---------------------------------------------------------------------------


class TestAmbiguous:
    @pytest.mark.parametrize(
        "utterance",
        [
            "make it different",
            "change something",
            "I don't know, just do something",
            "yes",
            "hmm",
            "",
        ],
    )
    def test_ambiguous_when_no_pattern_matches(self, utterance: str) -> None:
        result = classify_steering(utterance, _safe())
        assert result.type == "ambiguous"

    def test_ambiguous_confidence_is_1_0(self) -> None:
        result = classify_steering("make it different", _safe())
        assert result.confidence == 1.0

    def test_ambiguous_detail_is_set(self) -> None:
        result = classify_steering("make it different", _safe())
        assert result.detail is not None


# ---------------------------------------------------------------------------
# TC-8: SteeringClassification properties
# ---------------------------------------------------------------------------


class TestSteeringClassificationProperties:
    def test_result_is_steering_classification(self) -> None:
        result = classify_steering("make it funnier", _safe())
        assert isinstance(result, SteeringClassification)

    def test_classification_is_frozen(self) -> None:
        result = classify_steering("make it funnier", _safe())
        with pytest.raises((AttributeError, TypeError)):
            result.type = "ambiguous"  # type: ignore[misc]

    def test_detail_is_none_or_str(self) -> None:
        result = classify_steering("make it funnier", _safe())
        assert result.detail is None or isinstance(result.detail, str)

    def test_confidence_in_valid_range(self) -> None:
        for utterance in ["funnier", "faster", "bring back", "add a", "hmm"]:
            result = classify_steering(utterance, _safe())
            assert 0.0 <= result.confidence <= 1.0
