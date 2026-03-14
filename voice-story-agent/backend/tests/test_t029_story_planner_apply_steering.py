"""
test_t029_story_planner_apply_steering.py

Unit tests for T-029: StoryPlannerService.apply_steering

All external calls are mocked — no real Gemini API calls are made.
Covers all spec "Done when" criteria plus edge cases.

Depends: T-029
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.exceptions import StoryPlannerError
from app.models.character_bible import ContentPolicy
from app.models.voice_command import CommandType, VoiceCommand
from app.services.story_planner import (
    StoryPlannerService,
    _build_apply_steering_prompt,
    _validate_steering_beats,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_BASE_ARC = [
    "Pip the rabbit discovers a shiny key near the old oak tree.",
    "Pip follows a winding path that leads deeper into the forest.",
    "A friendly owl offers a clue about where the key belongs.",
    "Pip reaches the locked garden gate and faces her biggest test.",
    "Pip opens the gate and finds a secret meadow full of flowers.",
]


def _make_command(
    interpreted_intent: str = "make the story funnier with silly jokes",
    command_type: CommandType = CommandType.tone_change,
) -> VoiceCommand:
    return VoiceCommand(
        command_id=uuid4(),
        turn_id=uuid4(),
        raw_transcript="make it funnier",
        interpreted_intent=interpreted_intent,
        command_type=command_type,
        applied_to_pages=[3, 4, 5],
        received_at=datetime.now(timezone.utc),
    )


def _make_content_policy(exclusions: list[str] | None = None) -> ContentPolicy:
    return ContentPolicy(exclusions=exclusions or ["no gore", "no character death"])


def _mock_gemini_response(beats: list[str]) -> MagicMock:
    """Build a mock that imitates client.aio.models.generate_content."""
    response = MagicMock()
    response.text = json.dumps({"beats": beats})
    mock_generate = AsyncMock(return_value=response)
    mock_aio = MagicMock()
    mock_aio.models.generate_content = mock_generate
    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


# ---------------------------------------------------------------------------
# TC-1: Spec "Done when" — unchanged prefix
# ---------------------------------------------------------------------------


class TestUnchangedPrefix:
    """Pages 1..from_page-1 must be identical to the input arc."""

    @pytest.mark.parametrize("from_page", [1, 2, 3, 4, 5])
    @pytest.mark.asyncio
    async def test_unchanged_beats_preserved(self, from_page: int) -> None:
        revised_beats = [f"revised beat {i}" for i in range(from_page, 6)]
        mock_client = _mock_gemini_response(revised_beats)

        svc = StoryPlannerService(client=mock_client)
        result = await svc.apply_steering(_BASE_ARC, _make_command(), from_page)

        # Prefix is untouched
        for i in range(from_page - 1):
            assert result[i] == _BASE_ARC[i], (
                f"Beat {i + 1} changed (from_page={from_page}): "
                f"{result[i]!r} != {_BASE_ARC[i]!r}"
            )

    @pytest.mark.asyncio
    async def test_unchanged_prefix_from_page_3(self) -> None:
        revised = ["funny beat 3", "funny beat 4", "funny beat 5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)

        result = await svc.apply_steering(_BASE_ARC, _make_command(), from_page=3)

        assert result[0] == _BASE_ARC[0]
        assert result[1] == _BASE_ARC[1]


# ---------------------------------------------------------------------------
# TC-2: Spec "Done when" — revised beats reflect command intent
# ---------------------------------------------------------------------------


class TestRevisedBeatsReflectIntent:
    """Pages from_page..5 are modified (keyword presence verified via mock)."""

    @pytest.mark.asyncio
    async def test_revised_beats_replace_tail(self) -> None:
        revised = ["silly beat 3", "silly beat 4", "silly beat 5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)

        result = await svc.apply_steering(_BASE_ARC, _make_command(), from_page=3)

        assert result[2] == "silly beat 3"
        assert result[3] == "silly beat 4"
        assert result[4] == "silly beat 5"

    @pytest.mark.asyncio
    async def test_full_arc_returned(self) -> None:
        revised = ["r1", "r2", "r3", "r4", "r5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)

        result = await svc.apply_steering(_BASE_ARC, _make_command(), from_page=1)

        assert len(result) == 5
        assert result == revised

    @pytest.mark.asyncio
    async def test_from_page_5_only_last_beat_revised(self) -> None:
        revised = ["new ending"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)

        result = await svc.apply_steering(_BASE_ARC, _make_command(), from_page=5)

        assert result[:4] == _BASE_ARC[:4]
        assert result[4] == "new ending"


# ---------------------------------------------------------------------------
# TC-3: Spec "Done when" — no ContentPolicy exclusion in updated beats
# ---------------------------------------------------------------------------


class TestContentPolicyExclusions:
    """Exclusions are passed into the Gemini prompt (mock captures the call)."""

    @pytest.mark.asyncio
    async def test_exclusions_appear_in_prompt(self) -> None:
        """The Gemini prompt must contain each content exclusion string."""
        revised = ["clean beat 3", "clean beat 4", "clean beat 5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)
        policy = _make_content_policy(["no gore", "no character death"])

        await svc.apply_steering(_BASE_ARC, _make_command(), from_page=3, content_policy=policy)

        call_args = mock_client.aio.models.generate_content.call_args
        prompt_text = call_args.kwargs.get("contents") or call_args.args[1]
        assert "no gore" in prompt_text
        assert "no character death" in prompt_text

    @pytest.mark.asyncio
    async def test_no_content_policy_still_works(self) -> None:
        """apply_steering works when content_policy=None."""
        revised = ["beat 3", "beat 4", "beat 5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)

        result = await svc.apply_steering(
            _BASE_ARC, _make_command(), from_page=3, content_policy=None
        )
        assert len(result) == 5


# ---------------------------------------------------------------------------
# TC-4: Prompt content verification
# ---------------------------------------------------------------------------


class TestPromptContent:
    @pytest.mark.asyncio
    async def test_interpreted_intent_in_prompt(self) -> None:
        revised = ["beat 3", "beat 4", "beat 5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)
        command = _make_command(interpreted_intent="add silly jokes to every page")

        await svc.apply_steering(_BASE_ARC, command, from_page=3)

        call_args = mock_client.aio.models.generate_content.call_args
        prompt_text = call_args.kwargs.get("contents") or call_args.args[1]
        assert "add silly jokes to every page" in prompt_text

    @pytest.mark.asyncio
    async def test_remaining_beats_in_prompt(self) -> None:
        """The prompt must contain the current remaining beats text."""
        revised = ["beat 4", "beat 5"]
        mock_client = _mock_gemini_response(revised)
        svc = StoryPlannerService(client=mock_client)

        await svc.apply_steering(_BASE_ARC, _make_command(), from_page=4)

        call_args = mock_client.aio.models.generate_content.call_args
        prompt_text = call_args.kwargs.get("contents") or call_args.args[1]
        # The 4th beat should appear in the prompt
        assert _BASE_ARC[3] in prompt_text


# ---------------------------------------------------------------------------
# TC-5: Error handling — Gemini failure → StoryPlannerError
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_gemini_failure_raises_story_planner_error(self) -> None:
        mock_generate = AsyncMock(side_effect=RuntimeError("API down"))
        mock_aio = MagicMock()
        mock_aio.models.generate_content = mock_generate
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        svc = StoryPlannerService(client=mock_client)
        with pytest.raises(StoryPlannerError) as exc_info:
            await svc.apply_steering(_BASE_ARC, _make_command(), from_page=3)

        assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_malformed_json_raises_story_planner_error(self) -> None:
        response = MagicMock()
        response.text = "not valid json {{{"
        mock_generate = AsyncMock(return_value=response)
        mock_aio = MagicMock()
        mock_aio.models.generate_content = mock_generate
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        svc = StoryPlannerService(client=mock_client)
        with pytest.raises(StoryPlannerError):
            await svc.apply_steering(_BASE_ARC, _make_command(), from_page=3)

    @pytest.mark.asyncio
    async def test_wrong_beat_count_raises_story_planner_error(self) -> None:
        """Gemini returning wrong number of beats raises StoryPlannerError."""
        # from_page=3 expects 3 beats, but returns 2
        mock_client = _mock_gemini_response(["only two beats", "here"])
        svc = StoryPlannerService(client=mock_client)

        with pytest.raises(StoryPlannerError):
            await svc.apply_steering(_BASE_ARC, _make_command(), from_page=3)


# ---------------------------------------------------------------------------
# TC-6: Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_arc_wrong_length_raises_value_error(self) -> None:
        svc = StoryPlannerService()
        with pytest.raises(ValueError, match="exactly 5 beats"):
            await svc.apply_steering(["only", "three", "beats"], _make_command(), from_page=1)

    @pytest.mark.asyncio
    async def test_from_page_zero_raises_value_error(self) -> None:
        svc = StoryPlannerService()
        with pytest.raises(ValueError, match="from_page must be 1"):
            await svc.apply_steering(_BASE_ARC, _make_command(), from_page=0)

    @pytest.mark.asyncio
    async def test_from_page_six_raises_value_error(self) -> None:
        svc = StoryPlannerService()
        with pytest.raises(ValueError, match="from_page must be 1"):
            await svc.apply_steering(_BASE_ARC, _make_command(), from_page=6)


# ---------------------------------------------------------------------------
# TC-7: Pure helper unit tests (_build_apply_steering_prompt, _validate_steering_beats)
# ---------------------------------------------------------------------------


class TestBuildApplySteeringPrompt:
    def test_intent_in_prompt(self) -> None:
        prompt = _build_apply_steering_prompt(
            remaining_beats=["beat 3", "beat 4"],
            from_page=3,
            interpreted_intent="make it funnier",
            content_policy=None,
        )
        assert "make it funnier" in prompt

    def test_remaining_beats_in_prompt(self) -> None:
        prompt = _build_apply_steering_prompt(
            remaining_beats=["Page 3 beat", "Page 4 beat"],
            from_page=3,
            interpreted_intent="tone change",
            content_policy=None,
        )
        assert "Page 3 beat" in prompt
        assert "Page 4 beat" in prompt

    def test_exclusions_in_prompt(self) -> None:
        policy = ContentPolicy(exclusions=["no fear escalation", "no gore"])
        prompt = _build_apply_steering_prompt(
            remaining_beats=["beat"],
            from_page=5,
            interpreted_intent="pacing change",
            content_policy=policy,
        )
        assert "no fear escalation" in prompt
        assert "no gore" in prompt

    def test_no_exclusions_shows_none(self) -> None:
        prompt = _build_apply_steering_prompt(
            remaining_beats=["beat"],
            from_page=5,
            interpreted_intent="pacing",
            content_policy=None,
        )
        assert "(none)" in prompt


class TestValidateSteeringBeats:
    def test_valid_beats_returned(self) -> None:
        data = {"beats": ["beat A", "beat B", "beat C"]}
        result = _validate_steering_beats(data, expected_count=3)
        assert result == ["beat A", "beat B", "beat C"]

    def test_wrong_count_raises_value_error(self) -> None:
        data = {"beats": ["only one"]}
        with pytest.raises(ValueError, match="Expected 3"):
            _validate_steering_beats(data, expected_count=3)

    def test_missing_beats_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must be a list"):
            _validate_steering_beats({}, expected_count=2)

    def test_empty_beat_raises_value_error(self) -> None:
        data = {"beats": ["good beat", "   ", "another"]}
        with pytest.raises(ValueError, match="Empty beats"):
            _validate_steering_beats(data, expected_count=3)

    def test_beats_stripped_of_whitespace(self) -> None:
        data = {"beats": ["  spaced beat  "]}
        result = _validate_steering_beats(data, expected_count=1)
        assert result == ["spaced beat"]
