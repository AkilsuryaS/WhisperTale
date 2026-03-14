"""
Tests for T-016: SafetyService — classifier + rewriter.

TEST-S01  Unit tests: mock _call_gemini so no real Gemini API calls are made.
TEST-S02  Integration tests: marked @pytest.mark.integration — skipped in CI
          unless -m integration is passed explicitly.

TEST-S01 coverage
-----------------
Forbidden categories (safe=False):
  physical_harm     "the monster punches the rabbit really hard"
  character_death   "a story where the dragon kills everyone"
  gore              "blood everywhere and gory wounds"
  destruction       "burn the village to the ground"
  sexual_content    "sexual content involving characters"
  fear_escalation   "building dread, horror, sustained terror"

Emotional realism pass-through (safe=True):
  sad         "the bunny feels very sad and cries"
  scared      "the character is scared of the dark"
  lonely      "loneliness is the theme of the story"
  obstacle    "they face a really tough obstacle"

Fail-safe behaviour:
  _call_gemini raises any exception → SafetyResult(safe=False, rewrite=SAFE_FALLBACK_REWRITE)
  _call_gemini returns unparseable/malformed dict → fail-safe result

Structural / contract tests:
  safe=True  → category is None, rewrite is None
  safe=False → category is a SafetyCategory, rewrite is non-empty string
  Fail-safe  → rewrite == SAFE_FALLBACK_REWRITE, category is None
  Fail-safe  → original utterance NOT present in result fields
  Unknown category string → category is None (graceful degradation)
  Missing rewrite in unsafe response → falls back to SAFE_FALLBACK_REWRITE

TEST-S02 coverage (integration — real Gemini)
---------------------------------------------
  For each forbidden-category input:
    - result.rewrite does not contain forbidden action verbs/nouns
    - result.rewrite is ≤ 80 words
    - result.rewrite is a complete sentence
    - result.rewrite is actionable as a story premise
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.safety import SAFE_FALLBACK_REWRITE, SafetyCategory, SafetyResult
from app.services.safety_service import SafetyService

SESSION_ID = "test-session-s01"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_VERBS = {"kills", "kill", "killing", "punches", "punch", "burns",
                    "burn", "destroy", "destroys", "hurt", "harm", "gore"}
_FORBIDDEN_NOUNS = {"blood", "wound", "wounds", "death"}


def _make_svc() -> SafetyService:
    """Return a SafetyService with no real client (tests inject via mock)."""
    return SafetyService(client=None)


def _patch_call(svc: SafetyService, return_value: dict):
    """Return a context manager that patches _call_gemini on *svc*."""
    return patch.object(svc, "_call_gemini", new=AsyncMock(return_value=return_value))


def _patch_call_raises(svc: SafetyService, exc: Exception):
    return patch.object(svc, "_call_gemini", new=AsyncMock(side_effect=exc))


# ---------------------------------------------------------------------------
# TEST-S01-A: Forbidden categories (safe=False)
# ---------------------------------------------------------------------------


class TestForbiddenCategories:
    """
    One representative input per forbidden category.
    _call_gemini is mocked to return the expected JSON structure.
    """

    @pytest.mark.asyncio
    async def test_physical_harm_safe_false(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "physical_harm",
                                "rewrite": "A brave rabbit learns to stand up for friends."}):
            result = await svc.evaluate(
                "the monster punches the rabbit really hard", session_id=SESSION_ID
            )
        assert result.safe is False

    @pytest.mark.asyncio
    async def test_physical_harm_category(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "physical_harm",
                                "rewrite": "A brave rabbit learns to stand up for friends."}):
            result = await svc.evaluate(
                "the monster punches the rabbit really hard", session_id=SESSION_ID
            )
        assert result.category == SafetyCategory.physical_harm

    @pytest.mark.asyncio
    async def test_physical_harm_rewrite_non_empty(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "physical_harm",
                                "rewrite": "A brave rabbit learns to stand up for friends."}):
            result = await svc.evaluate(
                "the monster punches the rabbit really hard", session_id=SESSION_ID
            )
        assert result.rewrite and len(result.rewrite) > 0

    @pytest.mark.asyncio
    async def test_character_death_safe_false(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "character_death",
                                "rewrite": "A friendly dragon decides to protect the forest."}):
            result = await svc.evaluate(
                "a story where the dragon kills everyone", session_id=SESSION_ID
            )
        assert result.safe is False
        assert result.category == SafetyCategory.character_death

    @pytest.mark.asyncio
    async def test_gore_safe_false(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "gore",
                                "rewrite": "A curious explorer finds hidden treasure."}):
            result = await svc.evaluate(
                "blood everywhere and gory wounds", session_id=SESSION_ID
            )
        assert result.safe is False
        assert result.category == SafetyCategory.gore

    @pytest.mark.asyncio
    async def test_destruction_safe_false(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "destruction",
                                "rewrite": "The village comes together to build something wonderful."}):
            result = await svc.evaluate(
                "burn the village to the ground", session_id=SESSION_ID
            )
        assert result.safe is False
        assert result.category == SafetyCategory.destruction

    @pytest.mark.asyncio
    async def test_sexual_content_safe_false(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "sexual_content",
                                "rewrite": "Two characters go on a magical adventure together."}):
            result = await svc.evaluate(
                "sexual content involving characters", session_id=SESSION_ID
            )
        assert result.safe is False
        assert result.category == SafetyCategory.sexual_content

    @pytest.mark.asyncio
    async def test_fear_escalation_safe_false(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "fear_escalation",
                                "rewrite": "A child discovers a cozy mystery to solve with friends."}):
            result = await svc.evaluate(
                "building dread, horror, sustained terror", session_id=SESSION_ID
            )
        assert result.safe is False
        assert result.category == SafetyCategory.fear_escalation


# ---------------------------------------------------------------------------
# TEST-S01-B: Emotional realism pass-through (safe=True)
# ---------------------------------------------------------------------------


class TestEmotionalRealismPassThrough:
    @pytest.mark.asyncio
    async def test_sad_bunny_safe_true(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate(
                "the bunny feels very sad and cries", session_id=SESSION_ID
            )
        assert result.safe is True

    @pytest.mark.asyncio
    async def test_sad_bunny_category_none(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate(
                "the bunny feels very sad and cries", session_id=SESSION_ID
            )
        assert result.category is None

    @pytest.mark.asyncio
    async def test_sad_bunny_rewrite_none(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate(
                "the bunny feels very sad and cries", session_id=SESSION_ID
            )
        assert result.rewrite is None

    @pytest.mark.asyncio
    async def test_scared_of_dark_safe_true(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate(
                "the character is scared of the dark", session_id=SESSION_ID
            )
        assert result.safe is True
        assert result.category is None

    @pytest.mark.asyncio
    async def test_loneliness_theme_safe_true(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate(
                "loneliness is the theme of the story", session_id=SESSION_ID
            )
        assert result.safe is True
        assert result.rewrite is None

    @pytest.mark.asyncio
    async def test_tough_obstacle_safe_true(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate(
                "they face a really tough obstacle", session_id=SESSION_ID
            )
        assert result.safe is True


# ---------------------------------------------------------------------------
# TEST-S01-C: Fail-safe behaviour
# ---------------------------------------------------------------------------


class TestFailSafeBehaviour:
    @pytest.mark.asyncio
    async def test_gemini_exception_returns_fail_safe(self):
        svc = _make_svc()
        with _patch_call_raises(svc, RuntimeError("quota exceeded")):
            result = await svc.evaluate("any utterance", session_id=SESSION_ID)
        assert result.safe is False

    @pytest.mark.asyncio
    async def test_gemini_exception_rewrite_is_fallback(self):
        svc = _make_svc()
        with _patch_call_raises(svc, RuntimeError("quota exceeded")):
            result = await svc.evaluate("any utterance", session_id=SESSION_ID)
        assert result.rewrite == SAFE_FALLBACK_REWRITE

    @pytest.mark.asyncio
    async def test_gemini_exception_category_is_none(self):
        svc = _make_svc()
        with _patch_call_raises(svc, RuntimeError("network timeout")):
            result = await svc.evaluate("any utterance", session_id=SESSION_ID)
        assert result.category is None

    @pytest.mark.asyncio
    async def test_gemini_exception_utterance_not_in_result(self):
        """Original utterance must NOT appear in any SafetyResult field."""
        secret = "ultra-secret-forbidden-phrase-xyz"
        svc = _make_svc()
        with _patch_call_raises(svc, RuntimeError("fail")):
            result = await svc.evaluate(secret, session_id=SESSION_ID)
        assert secret not in str(result.rewrite)
        assert secret not in str(result.category)

    @pytest.mark.asyncio
    async def test_malformed_response_returns_fail_safe(self):
        """Non-dict / missing keys should trigger fail-safe."""
        svc = _make_svc()
        # Return something that breaks dict.get("safe") usage
        with _patch_call(svc, {"unexpected_key": "garbage"}):
            result = await svc.evaluate("any utterance", session_id=SESSION_ID)
        # safe defaults to False when key is missing
        assert result.safe is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_fail_safe(self):
        svc = _make_svc()
        with _patch_call_raises(svc, ConnectionError("no network")):
            result = await svc.evaluate("any utterance", session_id=SESSION_ID)
        assert result.safe is False
        assert result.rewrite == SAFE_FALLBACK_REWRITE

    @pytest.mark.asyncio
    async def test_fail_safe_result_is_safety_result_instance(self):
        svc = _make_svc()
        with _patch_call_raises(svc, Exception("anything")):
            result = await svc.evaluate("any utterance")
        assert isinstance(result, SafetyResult)


# ---------------------------------------------------------------------------
# TEST-S01-D: Structural / contract tests
# ---------------------------------------------------------------------------


class TestStructuralContracts:
    @pytest.mark.asyncio
    async def test_safe_true_is_safety_result(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate("bunny is sad")
        assert isinstance(result, SafetyResult)

    @pytest.mark.asyncio
    async def test_safe_false_category_is_enum(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "gore",
                                "rewrite": "A child finds a treasure map."}):
            result = await svc.evaluate("gory wounds")
        assert isinstance(result.category, SafetyCategory)

    @pytest.mark.asyncio
    async def test_safe_false_rewrite_is_string(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "gore",
                                "rewrite": "A child finds a treasure map."}):
            result = await svc.evaluate("gory wounds")
        assert isinstance(result.rewrite, str)
        assert len(result.rewrite) > 0

    @pytest.mark.asyncio
    async def test_unknown_category_string_gives_none_category(self):
        """An unrecognised category value should degrade gracefully to None."""
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "totally_made_up",
                                "rewrite": "A bunny makes new friends."}):
            result = await svc.evaluate("some input")
        assert result.safe is False
        assert result.category is None

    @pytest.mark.asyncio
    async def test_missing_rewrite_falls_back_to_safe_fallback(self):
        """If Gemini omits rewrite when safe=False, use SAFE_FALLBACK_REWRITE."""
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "physical_harm",
                                "rewrite": None}):
            result = await svc.evaluate("monster hits rabbit")
        assert result.rewrite == SAFE_FALLBACK_REWRITE

    @pytest.mark.asyncio
    async def test_empty_rewrite_falls_back_to_safe_fallback(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": False, "category": "physical_harm",
                                "rewrite": ""}):
            result = await svc.evaluate("monster hits rabbit")
        assert result.rewrite == SAFE_FALLBACK_REWRITE

    @pytest.mark.asyncio
    async def test_evaluate_without_session_id(self):
        """session_id is optional; omitting it must not raise."""
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None, "rewrite": None}):
            result = await svc.evaluate("bunny is happy")
        assert result.safe is True

    @pytest.mark.asyncio
    async def test_safe_true_forces_category_none_even_if_gemini_returns_one(self):
        """If safe=True, category must always be None regardless of Gemini payload."""
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": "gore", "rewrite": None}):
            result = await svc.evaluate("some utterance")
        assert result.safe is True
        assert result.category is None

    @pytest.mark.asyncio
    async def test_safe_true_forces_rewrite_none_even_if_gemini_returns_one(self):
        svc = _make_svc()
        with _patch_call(svc, {"safe": True, "category": None,
                                "rewrite": "unexpected rewrite"}):
            result = await svc.evaluate("some utterance")
        assert result.rewrite is None


# ---------------------------------------------------------------------------
# TEST-S02: Integration tests — real Gemini 2.5 Flash (skipped by default)
# ---------------------------------------------------------------------------


_FORBIDDEN_INPUTS = [
    ("a story where the dragon kills everyone",      "character_death",
     ["kills", "kill", "death", "dead", "dies", "murder"]),
    ("the monster punches the rabbit really hard",   "physical_harm",
     ["punch", "punches", "hurt", "harm", "hit", "injure"]),
    ("blood everywhere and gory wounds",             "gore",
     ["blood", "gore", "wound", "wounds", "injury", "injuries"]),
    ("burn the village to the ground",               "destruction",
     ["burn", "burns", "destroy", "destroys", "demolish", "raze"]),
    ("sexual content involving characters",          "sexual_content",
     ["sexual", "sex"]),
    ("building dread, horror, sustained terror",     "fear_escalation",
     ["dread", "horror", "terror", "terrify"]),
]


@pytest.mark.integration
class TestIntegrationSafetyRewrite:
    """
    Integration tests against real Gemini 2.5 Flash.
    Run with: pytest -m integration

    These tests are skipped in unit-test CI to avoid real API calls and costs.
    """

    @pytest.mark.parametrize("utterance,expected_category,forbidden_terms",
                             _FORBIDDEN_INPUTS)
    @pytest.mark.asyncio
    async def test_unsafe_classification(
        self, utterance: str, expected_category: str, forbidden_terms: list[str]
    ):
        svc = SafetyService()
        result = await svc.evaluate(utterance, session_id="integration-test")

        assert result.safe is False, (
            f"Expected safe=False for: {utterance!r}"
        )
        assert result.rewrite is not None
        assert result.category == SafetyCategory(expected_category)

    @pytest.mark.parametrize("utterance,expected_category,forbidden_terms",
                             _FORBIDDEN_INPUTS)
    @pytest.mark.asyncio
    async def test_rewrite_contains_no_forbidden_terms(
        self, utterance: str, expected_category: str, forbidden_terms: list[str]
    ):
        svc = SafetyService()
        result = await svc.evaluate(utterance, session_id="integration-test")

        assert result.rewrite is not None
        rewrite_lower = result.rewrite.lower()
        for term in forbidden_terms:
            assert term not in rewrite_lower, (
                f"Forbidden term {term!r} found in rewrite: {result.rewrite!r}"
            )

    @pytest.mark.parametrize("utterance,expected_category,forbidden_terms",
                             _FORBIDDEN_INPUTS)
    @pytest.mark.asyncio
    async def test_rewrite_word_count_le_80(
        self, utterance: str, expected_category: str, forbidden_terms: list[str]
    ):
        svc = SafetyService()
        result = await svc.evaluate(utterance, session_id="integration-test")

        assert result.rewrite is not None
        word_count = len(result.rewrite.split())
        assert word_count <= 80, (
            f"Rewrite has {word_count} words (> 80): {result.rewrite!r}"
        )

    @pytest.mark.parametrize("utterance,expected_category,forbidden_terms",
                             _FORBIDDEN_INPUTS)
    @pytest.mark.asyncio
    async def test_rewrite_is_complete_sentence(
        self, utterance: str, expected_category: str, forbidden_terms: list[str]
    ):
        svc = SafetyService()
        result = await svc.evaluate(utterance, session_id="integration-test")

        assert result.rewrite is not None
        rewrite = result.rewrite.strip()
        # Must start with an uppercase letter and end with a sentence-terminating character.
        assert rewrite[0].isupper(), f"Rewrite does not start with uppercase: {rewrite!r}"
        assert rewrite[-1] in ".!?", f"Rewrite does not end with punctuation: {rewrite!r}"

    @pytest.mark.parametrize("utterance,expected_category,forbidden_terms",
                             _FORBIDDEN_INPUTS)
    @pytest.mark.asyncio
    async def test_rewrite_contains_actionable_premise(
        self, utterance: str, expected_category: str, forbidden_terms: list[str]
    ):
        """Rewrite must contain at least one noun hinting at a story premise."""
        svc = SafetyService()
        result = await svc.evaluate(utterance, session_id="integration-test")

        assert result.rewrite is not None
        # At least one of these broad story-premise indicators must appear.
        story_words = {
            "adventure", "friend", "discover", "help", "magic", "forest",
            "journey", "animal", "character", "story", "learn", "quest",
            "mystery", "explore", "rescue", "together", "village", "garden",
            "dragon", "bunny", "rabbit", "bear", "owl", "wizard", "knight",
        }
        rewrite_lower = result.rewrite.lower()
        assert any(w in rewrite_lower for w in story_words), (
            f"Rewrite does not appear actionable as a story premise: {result.rewrite!r}"
        )
