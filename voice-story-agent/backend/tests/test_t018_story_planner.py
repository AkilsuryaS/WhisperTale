"""
Tests for T-018: StoryPlannerService — create_arc.

Strategy
--------
The genai.Client is injected via the StoryPlannerService constructor so no
real Gemini API calls are made. _call_gemini is patched to return controlled
dicts for success paths and raise controlled exceptions for failure paths.

Covers:
  create_arc — success:
    T18-01  returns a list of exactly 5 non-empty strings
    T18-02  uses Gemini Pro model on the first attempt
    T18-03  returned beats are stripped of leading/trailing whitespace

  retry logic:
    T18-04  1st attempt failure → retries with Pro on 2nd attempt
    T18-05  2nd attempt failure → falls back to Flash on 3rd attempt
    T18-06  all 3 attempts fail → raises StoryPlannerError
    T18-07  success on 2nd attempt (1st Pro retry) → returns beats
    T18-08  success on 3rd attempt (Flash fallback) → returns beats
    T18-09  StoryPlannerError.cause is the last exception raised

  response validation:
    T18-10  non-list beats field → treated as failure, retries
    T18-11  beats list with ≠ 5 elements → treated as failure, retries
    T18-12  beats list with an empty string → treated as failure, retries
    T18-13  JSON parse error → treated as failure, retries

  prompt construction (_build_prompt):
    T18-14  prompt includes protagonist_name
    T18-15  prompt includes protagonist_description
    T18-16  prompt includes setting
    T18-17  prompt includes tone
    T18-18  prompt includes ContentPolicy exclusions
    T18-19  prompt includes "(none)" when exclusions list is empty

  _validate_beats:
    T18-20  valid 5-element list → returns cleaned list
    T18-21  dict without beats key → raises ValueError
    T18-22  beats is not a list → raises ValueError
    T18-23  beats has 4 elements → raises ValueError
    T18-24  beats has 6 elements → raises ValueError
    T18-25  beats contains empty string → raises ValueError
    T18-26  beats contains whitespace-only string → raises ValueError

  exclusion enforcement (structural check):
    T18-27  returned beats contain no terms from ContentPolicy.exclusions

  StoryPlannerError:
    T18-28  StoryPlannerError carries a non-None cause after total failure
    T18-29  StoryPlannerError message mentions number of attempts
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
from app.services.story_planner import StoryPlannerService, _build_prompt, _validate_beats

NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Test fixtures / factories
# ---------------------------------------------------------------------------

FIVE_BEATS = [
    "Pip the small blue rabbit woke in the cosy Meadow village, excited to explore the golden Autumn forest for the very first time today.",
    "Deep in the forest, Pip discovered a hidden path where her favourite acorn collection had scattered and gone completely missing overnight.",
    "A wise old owl named Hoot offered to help Pip search, guiding her through winding trails and hollow trees as clouds gathered above.",
    "At the tallest oak, a gust blew Pip off her paws — but she held tight, found the last acorn, and felt braver than ever before.",
    "Pip returned home to the Meadow as stars appeared, sharing her acorns joyfully with neighbours and drifting into the sweetest, safest sleep.",
]

FIVE_BEATS_RESPONSE = {"beats": FIVE_BEATS}


def _make_brief(**overrides) -> StoryBrief:
    defaults = dict(
        protagonist_name="Pip",
        protagonist_description="A small blue rabbit with floppy ears",
        setting="the Meadow village and Autumn forest",
        tone=Tone.sleepy,
        raw_setup_transcript="tell me a story about pip the rabbit",
        confirmed_at=NOW,
    )
    defaults.update(overrides)
    return StoryBrief(**defaults)


def _make_bible(exclusions: list[str] | None = None) -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="rabbit",
            color="blue",
            notable_traits=["floppy ears", "curious nature"],
        ),
        style_bible=StyleBible(
            art_style="soft watercolour",
            color_palette="warm pastels",
            mood="cosy",
            negative_style_terms=["dark", "scary"],
        ),
        content_policy=ContentPolicy(
            exclusions=exclusions if exclusions is not None else ["no gore", "no character death"],
        ),
    )


def _mock_client_returning(data: dict) -> MagicMock:
    """Return an injectable mock client whose generate_content returns data as JSON."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(data)

    mock_models = MagicMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)

    mock_aio = MagicMock()
    mock_aio.models = mock_models

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


def _mock_client_raising(exc: Exception) -> MagicMock:
    """Return an injectable mock client whose generate_content raises exc."""
    mock_models = MagicMock()
    mock_models.generate_content = AsyncMock(side_effect=exc)

    mock_aio = MagicMock()
    mock_aio.models = mock_models

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


# ---------------------------------------------------------------------------
# T18-01 / T18-02 / T18-03  create_arc — success
# ---------------------------------------------------------------------------


class TestCreateArcSuccess:
    @pytest.mark.asyncio
    async def test_returns_list_of_five_strings(self):
        client = _mock_client_returning(FIVE_BEATS_RESPONSE)
        svc = StoryPlannerService(client=client)
        result = await svc.create_arc(_make_brief(), _make_bible())
        assert isinstance(result, list)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_all_beats_are_non_empty_strings(self):
        client = _mock_client_returning(FIVE_BEATS_RESPONSE)
        svc = StoryPlannerService(client=client)
        result = await svc.create_arc(_make_brief(), _make_bible())
        for i, beat in enumerate(result):
            assert isinstance(beat, str), f"beat[{i}] is not a str"
            assert beat, f"beat[{i}] is empty"

    @pytest.mark.asyncio
    async def test_uses_pro_model_on_first_attempt(self):
        client = _mock_client_returning(FIVE_BEATS_RESPONSE)
        svc = StoryPlannerService(client=client)
        with patch.object(svc, "_call_gemini", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = FIVE_BEATS_RESPONSE
            await svc.create_arc(_make_brief(), _make_bible())
        first_call_model = mock_call.call_args_list[0][0][0]
        assert "pro" in first_call_model.lower() or "gemini-2.5-pro" in first_call_model

    @pytest.mark.asyncio
    async def test_beats_are_stripped(self):
        padded_beats = {"beats": [f"  {b}  " for b in FIVE_BEATS]}
        client = _mock_client_returning(padded_beats)
        svc = StoryPlannerService(client=client)
        result = await svc.create_arc(_make_brief(), _make_bible())
        for beat in result:
            assert beat == beat.strip()


# ---------------------------------------------------------------------------
# T18-04 / T18-05 / T18-06 / T18-07 / T18-08 / T18-09  retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_first_failure_retries_on_second_attempt(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert call_count == 2
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_second_failure_falls_back_to_flash(self):
        svc = StoryPlannerService(client=MagicMock())
        models_called = []

        async def _side_effect(model, prompt):
            models_called.append(model)
            if len(models_called) < 3:
                raise RuntimeError("transient error")
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            await svc.create_arc(_make_brief(), _make_bible())
        # 3rd attempt must use Flash
        assert "flash" in models_called[2].lower() or "gemini-2.5-flash" in models_called[2]

    @pytest.mark.asyncio
    async def test_all_three_failures_raise_story_planner_error(self):
        svc = StoryPlannerService(client=MagicMock())
        with patch.object(svc, "_call_gemini", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("quota exceeded")
            with pytest.raises(StoryPlannerError):
                await svc.create_arc(_make_brief(), _make_bible())
        assert mock_call.call_count == 3

    @pytest.mark.asyncio
    async def test_success_on_second_attempt_returns_beats(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first fail")
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert result == [b.strip() for b in FIVE_BEATS]

    @pytest.mark.asyncio
    async def test_success_on_third_attempt_returns_beats(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("first two fail")
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_story_planner_error_cause_is_last_exception(self):
        svc = StoryPlannerService(client=MagicMock())
        last_error = RuntimeError("final failure")
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"failure {call_count}")
            raise last_error

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            with pytest.raises(StoryPlannerError) as exc_info:
                await svc.create_arc(_make_brief(), _make_bible())
        assert exc_info.value.cause is last_error


# ---------------------------------------------------------------------------
# T18-10 / T18-11 / T18-12 / T18-13  response validation triggers retry
# ---------------------------------------------------------------------------


class TestResponseValidationRetry:
    @pytest.mark.asyncio
    async def test_non_list_beats_triggers_retry(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"beats": "not a list"}
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert call_count == 2
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_wrong_beat_count_triggers_retry(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"beats": ["only", "four", "beats", "here"]}
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert call_count == 2
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_empty_beat_triggers_retry(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                bad = list(FIVE_BEATS)
                bad[2] = ""
                return {"beats": bad}
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert call_count == 2
        assert all(b for b in result)

    @pytest.mark.asyncio
    async def test_json_parse_error_triggers_retry(self):
        svc = StoryPlannerService(client=MagicMock())
        call_count = 0

        async def _side_effect(model, prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise json.JSONDecodeError("bad json", "", 0)
            return FIVE_BEATS_RESPONSE

        with patch.object(svc, "_call_gemini", side_effect=_side_effect):
            result = await svc.create_arc(_make_brief(), _make_bible())
        assert call_count == 2
        assert len(result) == 5


# ---------------------------------------------------------------------------
# T18-14 / T18-15 / T18-16 / T18-17 / T18-18 / T18-19  prompt construction
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_includes_protagonist_name(self):
        brief = _make_brief(protagonist_name="Milo the Fox")
        prompt = _build_prompt(brief, _make_bible())
        assert "Milo the Fox" in prompt

    def test_prompt_includes_protagonist_description(self):
        brief = _make_brief(protagonist_description="A rusty-red fox with a white tail tip")
        prompt = _build_prompt(brief, _make_bible())
        assert "rusty-red fox with a white tail tip" in prompt

    def test_prompt_includes_setting(self):
        brief = _make_brief(setting="the misty Harbour town")
        prompt = _build_prompt(brief, _make_bible())
        assert "misty Harbour town" in prompt

    def test_prompt_includes_tone(self):
        brief = _make_brief(tone=Tone.adventurous)
        prompt = _build_prompt(brief, _make_bible())
        assert "adventurous" in prompt.lower()

    def test_prompt_includes_all_exclusions(self):
        exclusions = ["no gore", "no character death", "no physical harm"]
        bible = _make_bible(exclusions=exclusions)
        prompt = _build_prompt(_make_brief(), bible)
        for ex in exclusions:
            assert ex in prompt

    def test_prompt_shows_none_when_exclusions_empty(self):
        bible = _make_bible(exclusions=[])
        prompt = _build_prompt(_make_brief(), bible)
        assert "(none)" in prompt


# ---------------------------------------------------------------------------
# T18-20 / T18-21 / T18-22 / T18-23 / T18-24 / T18-25 / T18-26  _validate_beats
# ---------------------------------------------------------------------------


class TestValidateBeats:
    def test_valid_five_beats_returns_cleaned_list(self):
        data = {"beats": FIVE_BEATS}
        result = _validate_beats(data)
        assert result == [b.strip() for b in FIVE_BEATS]

    def test_missing_beats_key_raises_value_error(self):
        with pytest.raises(ValueError):
            _validate_beats({"not_beats": []})

    def test_beats_not_list_raises_value_error(self):
        with pytest.raises(ValueError, match="must be a list"):
            _validate_beats({"beats": "a string"})

    def test_four_beats_raises_value_error(self):
        with pytest.raises(ValueError, match="5"):
            _validate_beats({"beats": ["a", "b", "c", "d"]})

    def test_six_beats_raises_value_error(self):
        with pytest.raises(ValueError, match="5"):
            _validate_beats({"beats": ["a", "b", "c", "d", "e", "f"]})

    def test_empty_string_beat_raises_value_error(self):
        beats = list(FIVE_BEATS)
        beats[1] = ""
        with pytest.raises(ValueError):
            _validate_beats({"beats": beats})

    def test_whitespace_only_beat_raises_value_error(self):
        beats = list(FIVE_BEATS)
        beats[3] = "   "
        with pytest.raises(ValueError):
            _validate_beats({"beats": beats})


# ---------------------------------------------------------------------------
# T18-27  exclusion enforcement
# ---------------------------------------------------------------------------


class TestExclusionEnforcement:
    @pytest.mark.asyncio
    async def test_returned_beats_do_not_contain_exclusion_terms(self):
        """
        When Gemini returns clean beats, none should mention terms from
        ContentPolicy.exclusions (structural guard — validated by the test,
        not by the service itself at this stage).
        """
        exclusions = ["no gore", "no character death", "no physical harm"]
        bible = _make_bible(exclusions=exclusions)
        client = _mock_client_returning(FIVE_BEATS_RESPONSE)
        svc = StoryPlannerService(client=client)
        beats = await svc.create_arc(_make_brief(), bible)
        forbidden_terms = ["gore", "death", "physical harm", "hurt", "kill"]
        for beat in beats:
            for term in forbidden_terms:
                assert term.lower() not in beat.lower(), (
                    f"Forbidden term {term!r} found in beat: {beat!r}"
                )


# ---------------------------------------------------------------------------
# T18-28 / T18-29  StoryPlannerError properties
# ---------------------------------------------------------------------------


class TestStoryPlannerError:
    @pytest.mark.asyncio
    async def test_error_cause_is_non_none_after_total_failure(self):
        svc = StoryPlannerService(client=MagicMock())
        with patch.object(svc, "_call_gemini", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("all attempts failed")
            with pytest.raises(StoryPlannerError) as exc_info:
                await svc.create_arc(_make_brief(), _make_bible())
        assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_error_message_mentions_attempts(self):
        svc = StoryPlannerService(client=MagicMock())
        with patch.object(svc, "_call_gemini", new_callable=AsyncMock) as mock_call:
            mock_call.side_effect = RuntimeError("fail")
            with pytest.raises(StoryPlannerError) as exc_info:
                await svc.create_arc(_make_brief(), _make_bible())
        assert "3" in str(exc_info.value) or "attempt" in str(exc_info.value).lower()
