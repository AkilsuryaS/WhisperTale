"""
StoryPlannerService — 5-beat narrative arc generator backed by Gemini 2.5 Pro.

Public interface (T-018):
    async def create_arc(brief: StoryBrief, bible: CharacterBible) -> list[str]

Public interface (T-022):
    async def expand_page(
        beat: str,
        page_history: list[str],
        bible: CharacterBible,
    ) -> tuple[str, str]   # (display_text, narration_script)

Public interface (T-029):
    async def apply_steering(
        arc: list[str],
        command: VoiceCommand,
        from_page: int,
        content_policy: ContentPolicy | None = None,
    ) -> list[str]

Design
------
- A single Gemini 2.5 Pro call with structured JSON output generates a 5-beat
  story arc from StoryBrief parameters and CharacterBible ContentPolicy.
- ContentPolicy.exclusions are injected as hard constraints in the user prompt.
- Retry logic for create_arc:
    Attempt 1: Gemini 2.5 Pro
    Attempt 2: Gemini 2.5 Pro  (1st retry)
    Attempt 3: Gemini 2.5 Flash (fallback on 3rd attempt)
  If all 3 attempts fail, raises StoryPlannerError.
- expand_page uses a single Gemini 2.5 Flash call; if the returned text word
  count is outside 60-120 words the call is retried once with a stricter prompt.
  Raises StoryPlannerError after both attempts fail.
- The genai.Client is injectable via the constructor so unit tests never make
  real network calls.

Output JSON schema for create_arc:
    { "beats": ["...", "...", "...", "...", "..."] }  — 5 strings, each ≤ 40 words

Output JSON schema for expand_page:
    { "text": "<60-120 word page text>", "narration_script": "<narration text>" }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.config import settings, get_genai_client
from app.exceptions import StoryPlannerError
from app.models.character_bible import CharacterBible, ContentPolicy
from app.models.session import StoryBrief
from app.models.voice_command import VoiceCommand

logger = logging.getLogger(__name__)

# Total attempts: 2 × Pro + 1 × Flash
_MAX_ATTEMPTS = 3

_PAGE_WORD_MIN = 60
_PAGE_WORD_MAX = 120


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert children's story planner. Create a five-beat narrative arc
for an interactive bedtime story aimed at children aged 4–10.

NARRATIVE STRUCTURE (one beat per page):
  Page 1 — Opening:      Introduce the protagonist and setting; establish tone.
  Page 2 — Complication: Present the central challenge or quest.
  Page 3 — Rising Action: Deepen the challenge; add a helpful character or twist.
  Page 4 — Climax:       The protagonist faces the hardest moment and shows courage.
  Page 5 — Resolution:   The problem is solved warmly and satisfyingly.

BEAT REQUIREMENTS:
  • Each beat MUST be ≤ 40 words.
  • Each beat must be a complete narrative summary — not a stage direction.
  • Each beat must reference the protagonist and setting directly.
  • Use warm, imaginative, age-appropriate language.
  • Do NOT include any content listed under CONTENT POLICY constraints.

OUTPUT FORMAT — respond ONLY with a single valid JSON object, no prose, no \
markdown, no code fences:
{ "beats": ["<beat 1>", "<beat 2>", "<beat 3>", "<beat 4>", "<beat 5>"] }
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prompt(brief: StoryBrief, bible: CharacterBible) -> str:
    """Construct the user-turn prompt from confirmed story parameters."""
    exclusions = bible.content_policy.exclusions
    exclusion_block = (
        "\n".join(f"  • {ex}" for ex in exclusions)
        if exclusions
        else "  (none)"
    )
    tone_val = brief.tone if isinstance(brief.tone, str) else brief.tone.value
    return (
        f"STORY PARAMETERS\n"
        f"Protagonist name:        {brief.protagonist_name}\n"
        f"Protagonist description: {brief.protagonist_description}\n"
        f"Setting:                 {brief.setting}\n"
        f"Tone:                    {tone_val}\n"
        f"\n"
        f"CONTENT POLICY — hard constraints (must not appear in any beat):\n"
        f"{exclusion_block}\n"
        f"\n"
        f"Generate a 5-beat story arc following the required structure above."
    )


def _validate_beats(data: dict[str, Any]) -> list[str]:
    """
    Extract and validate the beats list from the Gemini JSON response.

    Raises ValueError on any structural violation so the caller can retry.
    """
    beats = data.get("beats")
    if not isinstance(beats, list):
        raise ValueError(
            f"'beats' must be a list, got {type(beats).__name__!r}"
        )
    if len(beats) != 5:
        raise ValueError(f"Expected 5 beats, got {len(beats)}")
    cleaned = [str(b).strip() for b in beats]
    empties = [i for i, b in enumerate(cleaned) if not b]
    if empties:
        raise ValueError(f"Empty beats at indices: {empties}")
    return cleaned


# ---------------------------------------------------------------------------
# expand_page helpers
# ---------------------------------------------------------------------------

_EXPAND_PAGE_SYSTEM_PROMPT = """\
You are an expert children's story author writing one page of an illustrated
bedtime story for children aged 4–10.

YOUR TASK:
  Write the text for a single story page and a matching narration script.

TEXT REQUIREMENTS:
  • Exactly 60–120 words (count carefully).
  • Written in warm, vivid, age-appropriate prose.
  • Must directly advance the CURRENT BEAT provided.
  • Must maintain narrative continuity with PAGE HISTORY (if any).
  • Must NOT include any content from CONTENT EXCLUSIONS.

NARRATION SCRIPT REQUIREMENTS:
  • A fluent read-aloud version of the page text suitable for a narrator's voice.
  • May differ slightly in phrasing for natural speech cadence.
  • Should roughly match the word count of the text field.

OUTPUT FORMAT — respond ONLY with valid JSON, no prose, no markdown:
{ "text": "<page text — 60–120 words>", "narration_script": "<narration text>" }
"""

_EXPAND_PAGE_STRICT_SYSTEM_PROMPT = """\
You are an expert children's story author writing one page of an illustrated
bedtime story for children aged 4–10.

CRITICAL INSTRUCTION: The text field MUST be between 60 and 120 words — count
every word before responding. Previous attempt was outside this range.

YOUR TASK:
  Write the text for a single story page and a matching narration script.

TEXT REQUIREMENTS:
  • MUST be 60–120 words (hard requirement — recount before submitting).
  • Written in warm, vivid, age-appropriate prose.
  • Must directly advance the CURRENT BEAT provided.
  • Must maintain narrative continuity with PAGE HISTORY (if any).
  • Must NOT include any content from CONTENT EXCLUSIONS.

NARRATION SCRIPT REQUIREMENTS:
  • A fluent read-aloud version of the page text suitable for a narrator's voice.
  • Should roughly match the word count of the text field.

OUTPUT FORMAT — respond ONLY with valid JSON, no prose, no markdown:
{ "text": "<page text — EXACTLY 60–120 words>", "narration_script": "<narration text>" }
"""


def _count_words(text: str) -> int:
    return len(text.split())


def _build_expand_page_prompt(
    beat: str,
    page_history: list[str],
    bible: CharacterBible,
    strict: bool = False,
) -> str:
    """Build the user-turn prompt for the expand_page Gemini call."""
    protagonist = bible.protagonist
    exclusions = bible.content_policy.exclusions

    history_block = (
        "\n".join(f"  Page {i + 1}: {summary}" for i, summary in enumerate(page_history))
        if page_history
        else "  (this is the first page)"
    )
    exclusion_block = (
        "\n".join(f"  • {ex}" for ex in exclusions) if exclusions else "  (none)"
    )
    mood = bible.style_bible.mood

    strict_note = (
        "\n\nSTRICT MODE: Your previous response was outside the 60–120 word range. "
        "Count every word. Do not submit until the count is between 60 and 120."
        if strict
        else ""
    )

    return (
        f"PROTAGONIST\n"
        f"  Name:        {protagonist.name}\n"
        f"  Description: {protagonist.species_or_type}, {protagonist.color}"
        + (f", {protagonist.attire}" if protagonist.attire else "")
        + "\n"
        f"  Traits:      {', '.join(protagonist.notable_traits)}\n"
        f"\n"
        f"STORY TONE: {mood}\n"
        f"\n"
        f"CURRENT BEAT (what happens on this page):\n"
        f"  {beat}\n"
        f"\n"
        f"PAGE HISTORY (narrative context from prior pages):\n"
        f"{history_block}\n"
        f"\n"
        f"CONTENT EXCLUSIONS (must not appear in the text):\n"
        f"{exclusion_block}"
        f"{strict_note}"
    )


def _validate_page_response(data: dict[str, Any]) -> tuple[str, str]:
    """
    Extract and validate text/narration_script from the Gemini JSON response.

    Returns (text, narration_script) on success.
    Raises ValueError with a descriptive message on any structural violation.
    """
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object, got {type(data).__name__!r}")

    text = data.get("text")
    narration = data.get("narration_script")

    if not isinstance(text, str) or not text.strip():
        raise ValueError("'text' field is missing or empty")
    if not isinstance(narration, str) or not narration.strip():
        raise ValueError("'narration_script' field is missing or empty")

    text = text.strip()
    word_count = _count_words(text)
    if not (_PAGE_WORD_MIN <= word_count <= _PAGE_WORD_MAX):
        raise ValueError(
            f"'text' word count {word_count} is outside [{_PAGE_WORD_MIN}, {_PAGE_WORD_MAX}]"
        )

    return text, narration.strip()


# ---------------------------------------------------------------------------
# apply_steering helpers (T-029)
# ---------------------------------------------------------------------------

_APPLY_STEERING_SYSTEM_PROMPT = """\
You are an expert children's story editor. You are given a partial story arc \
(the remaining unwritten pages) and a parent's steering instruction. Your job \
is to revise the remaining beats so the story reflects the instruction while \
remaining age-appropriate, warm, and consistent with the characters already \
introduced.

RULES:
  • Return EXACTLY as many beats as you receive — one per remaining page.
  • Each beat MUST be ≤ 40 words.
  • Each beat must be a complete narrative summary — not a stage direction.
  • Do NOT include any content listed in CONTENT EXCLUSIONS.
  • Do NOT change beats that are marked as already written (none provided here).
  • Honour the parent's STEERING INTENT faithfully.

OUTPUT FORMAT — respond ONLY with a single valid JSON object, no prose, no \
markdown, no code fences:
{ "beats": ["<beat>", "<beat>", ...] }
"""


def _build_apply_steering_prompt(
    remaining_beats: list[str],
    from_page: int,
    interpreted_intent: str,
    content_policy: ContentPolicy | None,
) -> str:
    """Build the user-turn prompt for apply_steering."""
    beats_block = "\n".join(
        f"  Page {from_page + i}: {beat}"
        for i, beat in enumerate(remaining_beats)
    )
    exclusions = (content_policy.exclusions if content_policy else [])
    exclusion_block = (
        "\n".join(f"  • {ex}" for ex in exclusions) if exclusions else "  (none)"
    )
    n = len(remaining_beats)
    return (
        f"REMAINING BEATS (pages {from_page}–{from_page + n - 1}):\n"
        f"{beats_block}\n"
        f"\n"
        f"STEERING INTENT (parent's instruction):\n"
        f"  {interpreted_intent}\n"
        f"\n"
        f"CONTENT EXCLUSIONS (must not appear in any beat):\n"
        f"{exclusion_block}\n"
        f"\n"
        f"Revise the {n} remaining beat(s) to reflect the steering intent."
    )


def _validate_steering_beats(data: dict[str, Any], expected_count: int) -> list[str]:
    """
    Extract and validate the beats list from apply_steering Gemini response.

    Raises ValueError on structural violations so the caller can retry.
    """
    beats = data.get("beats")
    if not isinstance(beats, list):
        raise ValueError(
            f"'beats' must be a list, got {type(beats).__name__!r}"
        )
    if len(beats) != expected_count:
        raise ValueError(
            f"Expected {expected_count} beat(s), got {len(beats)}"
        )
    cleaned = [str(b).strip() for b in beats]
    empties = [i for i, b in enumerate(cleaned) if not b]
    if empties:
        raise ValueError(f"Empty beats at indices: {empties}")
    return cleaned


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class StoryPlannerService:
    """
    Generates a 5-beat narrative arc using Gemini 2.5 Pro (with Flash fallback).

    Usage:
        svc = StoryPlannerService()
        beats = await svc.create_arc(brief, bible)
        # beats is a list of 5 non-empty strings
    """

    def __init__(self, client: genai.Client | None = None) -> None:
        # Injecting a client lets unit tests avoid real network calls.
        self._client = client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = get_genai_client("StoryPlannerService")
        return self._client

    async def _call_gemini(self, model: str, prompt: str) -> dict[str, Any]:
        """
        Issue one generate_content call and return the parsed JSON dict.

        Raises on any API error or JSON parse failure — the caller (create_arc)
        is responsible for catching and retrying.
        """
        client = self._get_client()
        response = await client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        return json.loads(response.text)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def create_arc(
        self,
        brief: StoryBrief,
        bible: CharacterBible,
    ) -> list[str]:
        """
        Generate a 5-beat story arc.

        Retry schedule:
            Attempt 1 — Gemini 2.5 Pro
            Attempt 2 — Gemini 2.5 Pro  (1st retry)
            Attempt 3 — Gemini 2.5 Flash (fallback)

        Returns:
            list[str]: exactly 5 non-empty beat strings.

        Raises:
            StoryPlannerError: when all 3 attempts are exhausted.
        """
        prompt = _build_prompt(brief, bible)
        model_schedule = [
            settings.GEMINI_PRO_MODEL,   # attempt 1
            settings.GEMINI_PRO_MODEL,   # attempt 2 (1st retry)
            settings.GEMINI_FLASH_MODEL, # attempt 3 (Flash fallback)
        ]
        last_exc: Exception | None = None

        for attempt, model in enumerate(model_schedule, start=1):
            try:
                data = await self._call_gemini(model, prompt)
                beats = _validate_beats(data)
                logger.info(
                    "StoryPlannerService: arc created on attempt %d/%d (model=%s)",
                    attempt,
                    _MAX_ATTEMPTS,
                    model,
                )
                return beats

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "StoryPlannerService: attempt %d/%d failed "
                    "(model=%s, error_type=%s): %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    model,
                    type(exc).__name__,
                    exc,
                )

        raise StoryPlannerError(
            f"create_arc failed after {_MAX_ATTEMPTS} attempts",
            cause=last_exc,
        )

    async def expand_page(
        self,
        beat: str,
        page_history: list[str],
        bible: CharacterBible,
    ) -> tuple[str, str]:
        """
        Expand a single story beat into page text and a narration script.

        Makes a single Gemini 2.5 Flash call. If the returned ``text`` word
        count is outside 60–120 words, retries once with a stricter prompt.

        Args:
            beat:         The narrative beat describing what happens on this page.
            page_history: One-sentence summaries of prior pages (empty for page 1).
            bible:        CharacterBible providing protagonist info + content policy.

        Returns:
            (display_text, narration_script) where ``display_text`` is 60–120 words.

        Raises:
            StoryPlannerError: when both attempts (normal + strict) fail.
        """
        last_exc: Exception | None = None

        for attempt, strict in enumerate([False, True], start=1):
            try:
                prompt = _build_expand_page_prompt(beat, page_history, bible, strict=strict)
                data = await self._call_gemini(settings.GEMINI_FLASH_MODEL, prompt)
                text, narration = _validate_page_response(data)
                logger.info(
                    "StoryPlannerService: expand_page succeeded on attempt %d/2",
                    attempt,
                )
                return text, narration

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "StoryPlannerService: expand_page attempt %d/2 failed "
                    "(strict=%s, error_type=%s): %s",
                    attempt,
                    strict,
                    type(exc).__name__,
                    exc,
                )

        raise StoryPlannerError(
            "expand_page failed after 2 attempts",
            cause=last_exc,
        )

    async def apply_steering(
        self,
        arc: list[str],
        command: VoiceCommand,
        from_page: int,
        content_policy: ContentPolicy | None = None,
    ) -> list[str]:
        """
        Revise the story arc from ``from_page`` onward to reflect a steering command.

        Pages before ``from_page`` are copied unchanged. A single Gemini 2.5 Flash
        call rewrites the remaining beats (pages ``from_page``–5) according to
        ``command.interpreted_intent`` while honouring ``content_policy.exclusions``.

        Args:
            arc:            Current 5-beat arc (list of 5 strings).
            command:        The accepted VoiceCommand driving the revision.
            from_page:      1-based page number; beats at indices [from_page-1, 4]
                            are revised; earlier beats are unchanged.
            content_policy: Optional content policy; exclusions are injected into
                            the Gemini prompt as hard constraints.

        Returns:
            A new list of 5 beat strings; beats 1..(from_page-1) are identical
            to the input arc.

        Raises:
            StoryPlannerError: when the Gemini call or response validation fails.
            ValueError:        when ``arc`` does not contain exactly 5 elements or
                               ``from_page`` is outside 1–5.
        """
        if len(arc) != 5:
            raise ValueError(f"arc must contain exactly 5 beats, got {len(arc)}")
        if not (1 <= from_page <= 5):
            raise ValueError(f"from_page must be 1–5, got {from_page}")

        # Beats that stay unchanged (0-indexed: 0 .. from_page-2)
        unchanged = arc[: from_page - 1]
        remaining = arc[from_page - 1 :]  # beats to revise (1 to 5 items)
        expected = len(remaining)

        prompt = _build_apply_steering_prompt(
            remaining_beats=remaining,
            from_page=from_page,
            interpreted_intent=command.interpreted_intent,
            content_policy=content_policy,
        )

        try:
            client = self._get_client()
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_FLASH_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_APPLY_STEERING_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.7,
                ),
            )
            data = json.loads(response.text)
            revised = _validate_steering_beats(data, expected_count=expected)
        except StoryPlannerError:
            raise
        except Exception as exc:
            logger.error(
                "StoryPlannerService: apply_steering failed "
                "(command_id=%s, from_page=%d, error_type=%s): %s",
                command.command_id,
                from_page,
                type(exc).__name__,
                exc,
            )
            raise StoryPlannerError(
                f"apply_steering failed for command {command.command_id}",
                cause=exc,
            ) from exc

        new_arc = unchanged + revised
        logger.info(
            "StoryPlannerService: arc updated via steering "
            "(command_id=%s, from_page=%d, revised_pages=%d)",
            command.command_id,
            from_page,
            expected,
        )
        return new_arc
