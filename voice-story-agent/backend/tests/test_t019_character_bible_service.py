"""
Tests for T-019: CharacterBibleService — initialise.

Strategy
--------
The genai.Client and SessionStore are injected via the CharacterBibleService
constructor. _call_gemini is patched to return controlled dicts so no real
Gemini or Firestore calls are made.

Covers:
  initialise — success:
    T19-01  returns a CharacterBible instance
    T19-02  protagonist.name comes from brief.protagonist_name (not Gemini)
    T19-03  protagonist.color matches the Gemini-derived color
    T19-04  protagonist.species_or_type set from Gemini response
    T19-05  protagonist.notable_traits has 2–4 items
    T19-06  protagonist.attire is None when Gemini returns null
    T19-07  protagonist.attire is set when Gemini returns a value
    T19-08  style_bible.mood reflects the tone
    T19-09  style_bible.art_style is non-empty
    T19-10  style_bible.negative_style_terms is a list
    T19-11  content_policy.exclusions contains all 6 base exclusions
    T19-12  character_refs is an empty list on initialise
    T19-13  store.save_character_bible called once with correct session_id
    T19-14  store.save_character_bible called with the returned bible

  error handling:
    T19-15  Gemini API exception → raises CharacterBibleServiceError
    T19-16  CharacterBibleServiceError.cause is the original exception
    T19-17  JSON parse error → raises CharacterBibleServiceError
    T19-18  store.save_character_bible failure → raises CharacterBibleServiceError
    T19-19  missing protagonist key in response → raises CharacterBibleServiceError
    T19-20  missing style_bible key in response → raises CharacterBibleServiceError

  prompt construction (_build_prompt):
    T19-21  prompt includes protagonist_description
    T19-22  prompt includes tone

  _parse_bible_data:
    T19-23  valid response → CharacterBible with correct protagonist.name
    T19-24  notable_traits with 1 item → raises ValueError
    T19-25  notable_traits with 5 items → raises ValueError
    T19-26  non-dict protagonist field → raises ValueError
    T19-27  non-dict style_bible field → raises ValueError
    T19-28  non-list negative_style_terms → raises ValueError

  BASE_EXCLUSIONS constant:
    T19-29  contains exactly the 6 expected exclusion strings
    T19-30  all 6 base exclusions present in returned content_policy.exclusions
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions import CharacterBibleServiceError
from app.models.character_bible import CharacterBible
from app.models.session import StoryBrief, Tone
from app.services.character_bible_service import (
    BASE_EXCLUSIONS,
    CharacterBibleService,
    _build_prompt,
    _parse_bible_data,
)

NOW = datetime.now(timezone.utc)
SESSION_ID = "test-session-t019"

# ---------------------------------------------------------------------------
# Test factories
# ---------------------------------------------------------------------------

VALID_RESPONSE = {
    "protagonist": {
        "species_or_type": "rabbit",
        "color": "blue",
        "attire": "a small red scarf",
        "notable_traits": ["floppy ears", "bright curious eyes", "tiny paws"],
    },
    "style_bible": {
        "art_style": "soft watercolour illustration",
        "color_palette": "warm pastels with gentle blues",
        "mood": "dreamy and cosy",
        "negative_style_terms": ["dark shadows", "sharp edges", "scary faces"],
    },
}

VALID_RESPONSE_NO_ATTIRE = {
    "protagonist": {
        "species_or_type": "fox",
        "color": "golden",
        "attire": None,
        "notable_traits": ["bushy tail", "pointed ears"],
    },
    "style_bible": {
        "art_style": "pastel pencil sketch",
        "color_palette": "warm autumn tones",
        "mood": "adventurous",
        "negative_style_terms": ["dark", "scary"],
    },
}


def _make_brief(**overrides) -> StoryBrief:
    defaults = dict(
        protagonist_name="Pip",
        protagonist_description="A small blue rabbit with floppy ears and a little red scarf",
        setting="the Meadow village",
        tone=Tone.sleepy,
        raw_setup_transcript="tell me a story about pip",
        confirmed_at=NOW,
    )
    defaults.update(overrides)
    return StoryBrief(**defaults)


def _mock_client(data: dict) -> MagicMock:
    """Return an injectable mock client that returns data as JSON."""
    mock_response = MagicMock()
    mock_response.text = json.dumps(data)
    mock_models = MagicMock()
    mock_models.generate_content = AsyncMock(return_value=mock_response)
    mock_aio = MagicMock()
    mock_aio.models = mock_models
    client = MagicMock()
    client.aio = mock_aio
    return client


def _mock_client_raising(exc: Exception) -> MagicMock:
    """Return an injectable mock client that raises exc on generate_content."""
    mock_models = MagicMock()
    mock_models.generate_content = AsyncMock(side_effect=exc)
    mock_aio = MagicMock()
    mock_aio.models = mock_models
    client = MagicMock()
    client.aio = mock_aio
    return client


def _mock_store() -> MagicMock:
    store = MagicMock()
    store.save_character_bible = AsyncMock()
    return store


def _make_svc(data: dict | None = None, store: MagicMock | None = None) -> CharacterBibleService:
    client = _mock_client(data or VALID_RESPONSE)
    return CharacterBibleService(client=client, store=store or _mock_store())


# ---------------------------------------------------------------------------
# T19-01 / T19-02 / T19-03 / T19-04 / T19-05 / T19-06 / T19-07  success
# ---------------------------------------------------------------------------


class TestInitialiseSuccess:
    @pytest.mark.asyncio
    async def test_returns_character_bible(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert isinstance(result, CharacterBible)

    @pytest.mark.asyncio
    async def test_protagonist_name_from_brief(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief(protagonist_name="Milo"))
        assert result.protagonist.name == "Milo"

    @pytest.mark.asyncio
    async def test_protagonist_color_from_gemini(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.protagonist.color == "blue"

    @pytest.mark.asyncio
    async def test_protagonist_species_from_gemini(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.protagonist.species_or_type == "rabbit"

    @pytest.mark.asyncio
    async def test_protagonist_notable_traits_count(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert 2 <= len(result.protagonist.notable_traits) <= 4

    @pytest.mark.asyncio
    async def test_protagonist_attire_none_when_null(self):
        svc = _make_svc(data=VALID_RESPONSE_NO_ATTIRE)
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.protagonist.attire is None

    @pytest.mark.asyncio
    async def test_protagonist_attire_set_when_present(self):
        svc = _make_svc(data=VALID_RESPONSE)
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.protagonist.attire == "a small red scarf"


# ---------------------------------------------------------------------------
# T19-08 / T19-09 / T19-10  StyleBible
# ---------------------------------------------------------------------------


class TestStyleBible:
    @pytest.mark.asyncio
    async def test_style_bible_mood_from_gemini(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.style_bible.mood == "dreamy and cosy"

    @pytest.mark.asyncio
    async def test_style_bible_art_style_non_empty(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.style_bible.art_style

    @pytest.mark.asyncio
    async def test_style_bible_negative_style_terms_is_list(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert isinstance(result.style_bible.negative_style_terms, list)


# ---------------------------------------------------------------------------
# T19-11 / T19-12  ContentPolicy + character_refs
# ---------------------------------------------------------------------------


class TestContentPolicyAndRefs:
    @pytest.mark.asyncio
    async def test_base_exclusions_all_present(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        for ex in BASE_EXCLUSIONS:
            assert ex in result.content_policy.exclusions, (
                f"Base exclusion missing: {ex!r}"
            )

    @pytest.mark.asyncio
    async def test_character_refs_empty_on_initialise(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        assert result.character_refs == []


# ---------------------------------------------------------------------------
# T19-13 / T19-14  persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_character_bible_called_once(self):
        store = _mock_store()
        svc = _make_svc(store=store)
        await svc.initialise(SESSION_ID, _make_brief())
        store.save_character_bible.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_character_bible_called_with_correct_session_id(self):
        store = _mock_store()
        svc = _make_svc(store=store)
        await svc.initialise(SESSION_ID, _make_brief())
        call_args = store.save_character_bible.call_args
        passed_session_id = call_args[0][0] if call_args[0] else call_args[1].get("session_id")
        assert passed_session_id == SESSION_ID

    @pytest.mark.asyncio
    async def test_save_character_bible_called_with_returned_bible(self):
        store = _mock_store()
        svc = _make_svc(store=store)
        result = await svc.initialise(SESSION_ID, _make_brief())
        call_args = store.save_character_bible.call_args
        passed_bible = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("bible")
        assert passed_bible == result


# ---------------------------------------------------------------------------
# T19-15 / T19-16 / T19-17 / T19-18 / T19-19 / T19-20  error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_gemini_api_error_raises_character_bible_service_error(self):
        client = _mock_client_raising(RuntimeError("quota exceeded"))
        svc = CharacterBibleService(client=client, store=_mock_store())
        with pytest.raises(CharacterBibleServiceError):
            await svc.initialise(SESSION_ID, _make_brief())

    @pytest.mark.asyncio
    async def test_error_cause_is_original_exception(self):
        original = RuntimeError("api down")
        client = _mock_client_raising(original)
        svc = CharacterBibleService(client=client, store=_mock_store())
        with pytest.raises(CharacterBibleServiceError) as exc_info:
            await svc.initialise(SESSION_ID, _make_brief())
        assert exc_info.value.cause is original

    @pytest.mark.asyncio
    async def test_json_parse_error_raises_character_bible_service_error(self):
        mock_response = MagicMock()
        mock_response.text = "not valid json {{{"
        mock_models = MagicMock()
        mock_models.generate_content = AsyncMock(return_value=mock_response)
        mock_aio = MagicMock()
        mock_aio.models = mock_models
        client = MagicMock()
        client.aio = mock_aio
        svc = CharacterBibleService(client=client, store=_mock_store())
        with pytest.raises(CharacterBibleServiceError):
            await svc.initialise(SESSION_ID, _make_brief())

    @pytest.mark.asyncio
    async def test_store_failure_raises_character_bible_service_error(self):
        store = MagicMock()
        store.save_character_bible = AsyncMock(side_effect=RuntimeError("firestore down"))
        svc = _make_svc(store=store)
        with pytest.raises(CharacterBibleServiceError):
            await svc.initialise(SESSION_ID, _make_brief())

    @pytest.mark.asyncio
    async def test_missing_protagonist_key_raises_character_bible_service_error(self):
        bad_data = {"style_bible": VALID_RESPONSE["style_bible"]}
        svc = _make_svc(data=bad_data)
        with pytest.raises(CharacterBibleServiceError):
            await svc.initialise(SESSION_ID, _make_brief())

    @pytest.mark.asyncio
    async def test_missing_style_bible_key_raises_character_bible_service_error(self):
        bad_data = {"protagonist": VALID_RESPONSE["protagonist"]}
        svc = _make_svc(data=bad_data)
        with pytest.raises(CharacterBibleServiceError):
            await svc.initialise(SESSION_ID, _make_brief())


# ---------------------------------------------------------------------------
# T19-21 / T19-22  _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_includes_protagonist_description(self):
        brief = _make_brief(
            protagonist_description="A tiny green frog with golden spots"
        )
        prompt = _build_prompt(brief)
        assert "A tiny green frog with golden spots" in prompt

    def test_prompt_includes_tone(self):
        brief = _make_brief(tone=Tone.adventurous)
        prompt = _build_prompt(brief)
        assert "adventurous" in prompt.lower()

    def test_prompt_does_not_include_protagonist_name(self):
        brief = _make_brief(protagonist_name="SuperSecretName")
        prompt = _build_prompt(brief)
        assert "SuperSecretName" not in prompt


# ---------------------------------------------------------------------------
# T19-23 / T19-24 / T19-25 / T19-26 / T19-27 / T19-28  _parse_bible_data
# ---------------------------------------------------------------------------


class TestParseBibleData:
    def test_valid_response_returns_character_bible(self):
        result = _parse_bible_data(VALID_RESPONSE, protagonist_name="Pip")
        assert isinstance(result, CharacterBible)
        assert result.protagonist.name == "Pip"

    def test_notable_traits_one_item_raises(self):
        data = {
            **VALID_RESPONSE,
            "protagonist": {**VALID_RESPONSE["protagonist"], "notable_traits": ["one"]},
        }
        with pytest.raises((ValueError, Exception)):
            _parse_bible_data(data, protagonist_name="Pip")

    def test_notable_traits_five_items_raises(self):
        data = {
            **VALID_RESPONSE,
            "protagonist": {
                **VALID_RESPONSE["protagonist"],
                "notable_traits": ["a", "b", "c", "d", "e"],
            },
        }
        with pytest.raises((ValueError, Exception)):
            _parse_bible_data(data, protagonist_name="Pip")

    def test_non_dict_protagonist_raises(self):
        data = {**VALID_RESPONSE, "protagonist": "not a dict"}
        with pytest.raises(ValueError, match="protagonist"):
            _parse_bible_data(data, protagonist_name="Pip")

    def test_non_dict_style_bible_raises(self):
        data = {**VALID_RESPONSE, "style_bible": ["a list"]}
        with pytest.raises(ValueError, match="style_bible"):
            _parse_bible_data(data, protagonist_name="Pip")

    def test_non_list_negative_style_terms_raises(self):
        data = {
            **VALID_RESPONSE,
            "style_bible": {**VALID_RESPONSE["style_bible"], "negative_style_terms": "not a list"},
        }
        with pytest.raises(ValueError, match="negative_style_terms"):
            _parse_bible_data(data, protagonist_name="Pip")


# ---------------------------------------------------------------------------
# T19-29 / T19-30  BASE_EXCLUSIONS constant
# ---------------------------------------------------------------------------


class TestBaseExclusions:
    def test_base_exclusions_has_six_items(self):
        assert len(BASE_EXCLUSIONS) == 6

    def test_base_exclusions_contains_expected_items(self):
        expected = {
            "no gore",
            "no character death",
            "no physical harm",
            "no sexual content",
            "no fear escalation",
            "no destruction of characters",
        }
        assert set(BASE_EXCLUSIONS) == expected

    @pytest.mark.asyncio
    async def test_all_base_exclusions_in_content_policy(self):
        svc = _make_svc()
        result = await svc.initialise(SESSION_ID, _make_brief())
        for ex in BASE_EXCLUSIONS:
            assert ex in result.content_policy.exclusions
