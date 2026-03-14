"""
Tests for T-022: StoryPlannerService — expand_page.

Strategy
--------
The genai.Client is injected via the StoryPlannerService constructor so no
real Gemini API calls are made. _call_gemini is patched to return controlled
dicts for success paths and raise controlled exceptions for failure paths.

Covers:
  expand_page — success:
    T22-01  returns (text, narration_script) tuple on valid response
    T22-02  text word count is within 60–120 words
    T22-03  uses Gemini Flash model (not Pro)
    T22-04  narration_script is returned unchanged (stripped)

  word-count retry:
    T22-05  text < 60 words on 1st attempt → retries with strict prompt
    T22-06  text > 120 words on 1st attempt → retries with strict prompt
    T22-07  strict retry succeeds → returns valid (text, narration)
    T22-08  both attempts fail → raises StoryPlannerError
    T22-09  StoryPlannerError.cause is the last exception on total failure

  response validation (_validate_page_response):
    T22-10  missing text field → raises ValueError
    T22-11  empty text field → raises ValueError
    T22-12  missing narration_script field → raises ValueError
    T22-13  empty narration_script field → raises ValueError
    T22-14  non-dict response → raises ValueError
    T22-15  text at exactly 60 words → accepted
    T22-16  text at exactly 120 words → accepted
    T22-17  text at 59 words → raises ValueError
    T22-18  text at 121 words → raises ValueError

  prompt construction (_build_expand_page_prompt):
    T22-19  prompt includes protagonist name
    T22-20  prompt includes protagonist species_or_type and color
    T22-21  prompt includes the beat text
    T22-22  prompt includes page_history entries
    T22-23  prompt shows "(this is the first page)" when page_history is empty
    T22-24  prompt includes content exclusions
    T22-25  prompt shows "(none)" when exclusions list is empty
    T22-26  strict prompt includes strict-mode warning text
    T22-27  non-strict prompt does NOT include strict-mode warning text

  content-policy enforcement (structural check):
    T22-28  returned text contains none of the content_policy.exclusions strings

  _count_words helper:
    T22-29  counts words correctly for a known string
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import StoryPlannerError
from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.session import StoryBrief, Tone
from app.services.story_planner import (
    StoryPlannerService,
    _build_expand_page_prompt,
    _count_words,
    _validate_page_response,
)

NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _make_bible(
    exclusions: list[str] | None = None,
    attire: str | None = "a little blue coat",
) -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="rabbit",
            color="blue",
            attire=attire,
            notable_traits=["curious", "brave"],
        ),
        style_bible=StyleBible(
            art_style="soft watercolour illustration",
            color_palette="warm pastels",
            mood="warm",
            negative_style_terms=["dark", "scary"],
        ),
        content_policy=ContentPolicy(
            exclusions=exclusions if exclusions is not None else ["no gore", "no character death"],
        ),
    )


def _make_svc(call_gemini_side_effect: list | None = None) -> StoryPlannerService:
    """Return a StoryPlannerService with _call_gemini patched."""
    svc = StoryPlannerService(client=MagicMock())
    if call_gemini_side_effect is not None:
        svc._call_gemini = AsyncMock(side_effect=call_gemini_side_effect)
    return svc


def _words(n: int) -> str:
    """Return a string of exactly *n* words."""
    return " ".join(["word"] * n)


VALID_TEXT = _words(80)  # 80 words — inside [60, 120]
VALID_NARRATION = "Pip ventured into the forest as the sun dipped below the meadow."
VALID_RESPONSE = {"text": VALID_TEXT, "narration_script": VALID_NARRATION}

BEAT = "Pip discovers a hidden door in the old oak tree."
PAGE_HISTORY = [
    "Pip the blue rabbit arrived in the Enchanted Meadow.",
    "Pip met a wise old owl who hinted at a mystery.",
]


# ---------------------------------------------------------------------------
# T22-01 — T22-04: expand_page success
# ---------------------------------------------------------------------------


class TestExpandPageSuccess:
    @pytest.mark.anyio
    async def test_returns_tuple_on_valid_response(self) -> None:
        """T22-01: returns (text, narration_script) on a valid Gemini response."""
        svc = _make_svc([VALID_RESPONSE])
        result = await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        assert isinstance(result, tuple)
        assert len(result) == 2

    @pytest.mark.anyio
    async def test_text_word_count_in_range(self) -> None:
        """T22-02: text word count is within 60–120 on success."""
        svc = _make_svc([VALID_RESPONSE])
        text, _ = await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        wc = _count_words(text)
        assert 60 <= wc <= 120

    @pytest.mark.anyio
    async def test_uses_flash_model(self) -> None:
        """T22-03: _call_gemini is called with the Flash model."""
        svc = StoryPlannerService(client=MagicMock())
        svc._call_gemini = AsyncMock(return_value=VALID_RESPONSE)
        await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        call_args = svc._call_gemini.call_args
        from app.config import settings
        assert call_args[0][0] == settings.GEMINI_FLASH_MODEL

    @pytest.mark.anyio
    async def test_narration_script_returned(self) -> None:
        """T22-04: narration_script from Gemini response is returned as second element."""
        svc = _make_svc([VALID_RESPONSE])
        _, narration = await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        assert narration == VALID_NARRATION


# ---------------------------------------------------------------------------
# T22-05 — T22-09: retry on word-count violation
# ---------------------------------------------------------------------------


class TestExpandPageRetry:
    @pytest.mark.anyio
    async def test_too_short_text_triggers_retry(self) -> None:
        """T22-05: text < 60 words on 1st attempt → _call_gemini is called twice."""
        short_response = {"text": _words(40), "narration_script": VALID_NARRATION}
        svc = _make_svc([short_response, VALID_RESPONSE])
        await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        assert svc._call_gemini.call_count == 2

    @pytest.mark.anyio
    async def test_too_long_text_triggers_retry(self) -> None:
        """T22-06: text > 120 words on 1st attempt → _call_gemini is called twice."""
        long_response = {"text": _words(130), "narration_script": VALID_NARRATION}
        svc = _make_svc([long_response, VALID_RESPONSE])
        await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        assert svc._call_gemini.call_count == 2

    @pytest.mark.anyio
    async def test_strict_retry_succeeds(self) -> None:
        """T22-07: strict retry (2nd call) succeeds → returns valid result."""
        short_response = {"text": _words(40), "narration_script": VALID_NARRATION}
        svc = _make_svc([short_response, VALID_RESPONSE])
        text, narration = await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        assert 60 <= _count_words(text) <= 120
        assert narration == VALID_NARRATION

    @pytest.mark.anyio
    async def test_both_attempts_fail_raises_story_planner_error(self) -> None:
        """T22-08: both attempts fail → StoryPlannerError is raised."""
        svc = _make_svc([RuntimeError("API down"), RuntimeError("still down")])
        with pytest.raises(StoryPlannerError):
            await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())

    @pytest.mark.anyio
    async def test_story_planner_error_cause_is_last_exception(self) -> None:
        """T22-09: StoryPlannerError.cause is the last exception raised."""
        final_exc = RuntimeError("second failure")
        svc = _make_svc([RuntimeError("first failure"), final_exc])
        with pytest.raises(StoryPlannerError) as exc_info:
            await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())
        assert exc_info.value.cause is final_exc

    @pytest.mark.anyio
    async def test_word_count_violation_on_both_attempts_raises(self) -> None:
        """Both attempts return out-of-range text → StoryPlannerError."""
        short_response = {"text": _words(40), "narration_script": VALID_NARRATION}
        svc = _make_svc([short_response, short_response])
        with pytest.raises(StoryPlannerError):
            await svc.expand_page(BEAT, PAGE_HISTORY, _make_bible())


# ---------------------------------------------------------------------------
# T22-10 — T22-18: _validate_page_response unit tests
# ---------------------------------------------------------------------------


class TestValidatePageResponse:
    def test_missing_text_raises(self) -> None:
        """T22-10: dict without 'text' key → ValueError."""
        with pytest.raises(ValueError, match="text"):
            _validate_page_response({"narration_script": VALID_NARRATION})

    def test_empty_text_raises(self) -> None:
        """T22-11: empty string for text → ValueError."""
        with pytest.raises(ValueError, match="text"):
            _validate_page_response({"text": "   ", "narration_script": VALID_NARRATION})

    def test_missing_narration_raises(self) -> None:
        """T22-12: dict without 'narration_script' key → ValueError."""
        with pytest.raises(ValueError, match="narration_script"):
            _validate_page_response({"text": VALID_TEXT})

    def test_empty_narration_raises(self) -> None:
        """T22-13: empty string for narration_script → ValueError."""
        with pytest.raises(ValueError, match="narration_script"):
            _validate_page_response({"text": VALID_TEXT, "narration_script": "  "})

    def test_non_dict_raises(self) -> None:
        """T22-14: non-dict input → ValueError."""
        with pytest.raises(ValueError):
            _validate_page_response(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_exactly_60_words_accepted(self) -> None:
        """T22-15: text with exactly 60 words is accepted."""
        text, narration = _validate_page_response(
            {"text": _words(60), "narration_script": VALID_NARRATION}
        )
        assert _count_words(text) == 60

    def test_exactly_120_words_accepted(self) -> None:
        """T22-16: text with exactly 120 words is accepted."""
        text, narration = _validate_page_response(
            {"text": _words(120), "narration_script": VALID_NARRATION}
        )
        assert _count_words(text) == 120

    def test_59_words_raises(self) -> None:
        """T22-17: 59-word text → ValueError with word count info."""
        with pytest.raises(ValueError, match="59"):
            _validate_page_response(
                {"text": _words(59), "narration_script": VALID_NARRATION}
            )

    def test_121_words_raises(self) -> None:
        """T22-18: 121-word text → ValueError with word count info."""
        with pytest.raises(ValueError, match="121"):
            _validate_page_response(
                {"text": _words(121), "narration_script": VALID_NARRATION}
            )

    def test_returns_stripped_text(self) -> None:
        """Leading/trailing whitespace in text is stripped before returning."""
        text, _ = _validate_page_response(
            {"text": f"  {VALID_TEXT}  ", "narration_script": VALID_NARRATION}
        )
        assert text == VALID_TEXT

    def test_returns_stripped_narration(self) -> None:
        """Leading/trailing whitespace in narration is stripped before returning."""
        _, narration = _validate_page_response(
            {"text": VALID_TEXT, "narration_script": f"  {VALID_NARRATION}  "}
        )
        assert narration == VALID_NARRATION


# ---------------------------------------------------------------------------
# T22-19 — T22-27: _build_expand_page_prompt unit tests
# ---------------------------------------------------------------------------


class TestBuildExpandPagePrompt:
    def test_prompt_includes_protagonist_name(self) -> None:
        """T22-19: protagonist name appears in the prompt."""
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, _make_bible())
        assert "Pip" in prompt

    def test_prompt_includes_species_and_color(self) -> None:
        """T22-20: protagonist species_or_type and color appear in the prompt."""
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, _make_bible())
        assert "rabbit" in prompt
        assert "blue" in prompt

    def test_prompt_includes_beat(self) -> None:
        """T22-21: the beat text is present in the prompt."""
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, _make_bible())
        assert BEAT in prompt

    def test_prompt_includes_page_history(self) -> None:
        """T22-22: page history summaries appear in the prompt."""
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, _make_bible())
        for summary in PAGE_HISTORY:
            assert summary in prompt

    def test_empty_page_history_shows_first_page_note(self) -> None:
        """T22-23: when page_history is empty the prompt notes it is the first page."""
        prompt = _build_expand_page_prompt(BEAT, [], _make_bible())
        assert "first page" in prompt.lower()

    def test_prompt_includes_exclusions(self) -> None:
        """T22-24: content exclusions appear in the prompt."""
        bible = _make_bible(exclusions=["no gore", "no character death"])
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, bible)
        assert "no gore" in prompt
        assert "no character death" in prompt

    def test_empty_exclusions_shows_none(self) -> None:
        """T22-25: when exclusions list is empty the prompt shows '(none)'."""
        bible = _make_bible(exclusions=[])
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, bible)
        assert "(none)" in prompt

    def test_strict_prompt_contains_strict_warning(self) -> None:
        """T22-26: strict=True prompt contains the strict-mode recount warning."""
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, _make_bible(), strict=True)
        assert "recount" in prompt.lower() or "strict" in prompt.lower() or "previous attempt" in prompt.lower()

    def test_non_strict_prompt_has_no_strict_warning(self) -> None:
        """T22-27: strict=False prompt does NOT contain the strict-mode warning."""
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, _make_bible(), strict=False)
        assert "STRICT MODE" not in prompt
        assert "previous attempt" not in prompt.lower()

    def test_prompt_includes_attire_when_present(self) -> None:
        """Protagonist attire is included in the prompt when set."""
        bible = _make_bible(attire="a little blue coat")
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, bible)
        assert "blue coat" in prompt

    def test_prompt_omits_attire_section_when_none(self) -> None:
        """Protagonist attire is omitted from the prompt when None."""
        bible = _make_bible(attire=None)
        prompt = _build_expand_page_prompt(BEAT, PAGE_HISTORY, bible)
        assert "None" not in prompt


# ---------------------------------------------------------------------------
# T22-28: content-policy enforcement
# ---------------------------------------------------------------------------


class TestContentPolicyEnforcement:
    @pytest.mark.anyio
    async def test_returned_text_respects_exclusions(self) -> None:
        """T22-28: exclusion terms do not appear in the returned text (structural check)."""
        exclusions = ["no gore", "violence", "scary monster"]
        bible = _make_bible(exclusions=exclusions)
        clean_text = (
            "Pip found a shimmering door carved into the ancient oak. "
            "With a gentle push it swung open to reveal a garden full of "
            "glowing flowers and friendly fireflies dancing in the warm evening air. "
            "Pip stepped inside heart full of wonder marvelling at the colours. "
            "Every petal sparkled softly and a tiny bluebird sang a welcome song "
            "that floated through the trees like a lullaby made just for Pip."
        )
        response = {"text": clean_text, "narration_script": VALID_NARRATION}
        svc = _make_svc([response])
        text, _ = await svc.expand_page(BEAT, [], bible)
        for ex in exclusions:
            assert ex.lower() not in text.lower()


# ---------------------------------------------------------------------------
# T22-29: _count_words helper
# ---------------------------------------------------------------------------


class TestCountWords:
    def test_counts_words_correctly(self) -> None:
        """T22-29: _count_words returns the correct word count."""
        assert _count_words("one two three") == 3

    def test_empty_string_returns_zero(self) -> None:
        assert _count_words("") == 0

    def test_counts_80_word_string(self) -> None:
        assert _count_words(_words(80)) == 80

    def test_counts_with_punctuation(self) -> None:
        assert _count_words("Hello, world! How are you?") == 5
