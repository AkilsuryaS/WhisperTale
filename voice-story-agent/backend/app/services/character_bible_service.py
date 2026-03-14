"""
CharacterBibleService — derives and persists a CharacterBible from a StoryBrief.

Public interface (T-019):
    async def initialise(session_id: str, brief: StoryBrief) -> CharacterBible

Design
------
- A single Gemini 2.5 Flash call with structured JSON output derives
  ProtagonistProfile fields (species_or_type, color, attire, notable_traits)
  and StyleBible fields (art_style, color_palette, mood, negative_style_terms)
  from the brief's protagonist_description and tone.
- ContentPolicy is always initialised with a fixed set of 6 base exclusions;
  it is extended later by accepted SafetyDecisions (T-017).
- The protagonist.name is taken directly from brief.protagonist_name — Gemini
  is not asked for a name to avoid hallucination.
- CharacterBible and StyleBible are persisted atomically via SessionStore
  (single Firestore batch write).
- The genai.Client and SessionStore are injectable for full test isolation.
- Any Gemini API error or malformed JSON raises CharacterBibleServiceError.

Output JSON schema expected from Gemini:
{
  "protagonist": {
    "species_or_type": "<e.g. rabbit, fox, fairy>",
    "color":           "<primary color from the description>",
    "attire":          "<clothing if mentioned — null if none>",
    "notable_traits":  ["<trait>", "<trait>", ..."]   // 2–4 items
  },
  "style_bible": {
    "art_style":             "<e.g. soft watercolour illustration>",
    "color_palette":         "<e.g. warm pastels, muted earth tones>",
    "mood":                  "<e.g. dreamy, cosy, adventurous>",
    "negative_style_terms":  ["<avoid term>", ...]
  }
}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.exceptions import CharacterBibleServiceError
from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.session import StoryBrief
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base content-policy exclusions (always applied from story start)
# ---------------------------------------------------------------------------

BASE_EXCLUSIONS: list[str] = [
    "no gore",
    "no character death",
    "no physical harm",
    "no sexual content",
    "no fear escalation",
    "no destruction of characters",
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a children's book art director and character designer. Given a story \
protagonist description and a narrative tone, derive the visual and stylistic \
properties needed to illustrate a consistent, age-appropriate bedtime story \
for children aged 4–10.

OUTPUT FORMAT — respond ONLY with a single valid JSON object, no prose, no \
markdown, no code fences:

{
  "protagonist": {
    "species_or_type": "<the character type, e.g. rabbit, fox, fairy, robot>",
    "color":           "<primary color from the description, e.g. blue, golden>",
    "attire":          "<clothing or accessories if explicitly mentioned, else null>",
    "notable_traits":  ["<2 to 4 distinctive visual traits for consistent illustration>"]
  },
  "style_bible": {
    "art_style":             "<illustration style matching the tone, e.g. soft watercolour>",
    "color_palette":         "<overall palette, e.g. warm pastels, cool blues and purples>",
    "mood":                  "<one to three words capturing the emotional feel>",
    "negative_style_terms":  ["<style elements to avoid, e.g. dark shadows, sharp edges>"]
  }
}

RULES:
- notable_traits must contain between 2 and 4 items (no more, no less).
- notable_traits must be concrete visual properties (color, size, accessory).
- mood must reflect the provided tone word.
- negative_style_terms must include at least 2 items that prevent scary or \
  adult visual elements.
- Do NOT invent a name — that is provided separately.
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_prompt(brief: StoryBrief) -> str:
    """Construct the user-turn prompt from the confirmed StoryBrief."""
    tone_val = brief.tone if isinstance(brief.tone, str) else brief.tone.value
    return (
        f"PROTAGONIST DESCRIPTION:\n{brief.protagonist_description}\n"
        f"\n"
        f"NARRATIVE TONE: {tone_val}\n"
        f"\n"
        f"Derive the protagonist visual profile and illustration style."
    )


def _parse_bible_data(
    data: dict[str, Any],
    protagonist_name: str,
) -> CharacterBible:
    """
    Parse and validate the Gemini response dict into a CharacterBible.

    Raises ValueError (caught by the caller) on any structural or
    Pydantic validation failure.
    """
    protagonist_data = data.get("protagonist")
    style_data = data.get("style_bible")

    if not isinstance(protagonist_data, dict):
        raise ValueError(
            f"'protagonist' must be a dict, got {type(protagonist_data).__name__!r}"
        )
    if not isinstance(style_data, dict):
        raise ValueError(
            f"'style_bible' must be a dict, got {type(style_data).__name__!r}"
        )

    traits = protagonist_data.get("notable_traits", [])
    if not isinstance(traits, list) or not (2 <= len(traits) <= 4):
        raise ValueError(
            f"notable_traits must be a list of 2–4 items, got: {traits!r}"
        )

    neg_terms = style_data.get("negative_style_terms", [])
    if not isinstance(neg_terms, list):
        raise ValueError(
            f"negative_style_terms must be a list, got {type(neg_terms).__name__!r}"
        )

    # Build Pydantic models — validation errors propagate as ValueError
    protagonist = ProtagonistProfile(
        name=protagonist_name,
        species_or_type=str(protagonist_data.get("species_or_type", "")).strip(),
        color=str(protagonist_data.get("color", "")).strip(),
        attire=protagonist_data.get("attire") or None,
        notable_traits=[str(t).strip() for t in traits],
    )
    style_bible = StyleBible(
        art_style=str(style_data.get("art_style", "")).strip(),
        color_palette=str(style_data.get("color_palette", "")).strip(),
        mood=str(style_data.get("mood", "")).strip(),
        negative_style_terms=[str(t).strip() for t in neg_terms],
    )
    content_policy = ContentPolicy(exclusions=list(BASE_EXCLUSIONS))

    return CharacterBible(
        protagonist=protagonist,
        style_bible=style_bible,
        content_policy=content_policy,
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class CharacterBibleService:
    """
    Derives a CharacterBible from a confirmed StoryBrief and persists it.

    Usage:
        svc = CharacterBibleService()
        bible = await svc.initialise(session_id, brief)
    """

    def __init__(
        self,
        client: Optional[genai.Client] = None,
        store: Optional[SessionStore] = None,
    ) -> None:
        # Both are lazily initialised if not injected, enabling test isolation.
        self._client = client
        self._store = store

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> genai.Client:
        if self._client is None:
            project_id = settings.require_gcp("CharacterBibleService")
            self._client = genai.Client(
                vertexai=True,
                project=project_id,
                location=settings.GCP_REGION,
            )
        return self._client

    def _get_store(self) -> SessionStore:
        if self._store is None:
            self._store = SessionStore()
        return self._store

    async def _call_gemini(self, prompt: str) -> dict[str, Any]:
        """
        Issue one Gemini Flash generate_content call and return the parsed
        JSON dict.  Raises on API error or JSON parse failure.
        """
        client = self._get_client()
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_FLASH_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )
        return json.loads(response.text)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def initialise(
        self, session_id: str, brief: StoryBrief
    ) -> CharacterBible:
        """
        Derive a CharacterBible from the confirmed StoryBrief and persist it.

        Steps:
            1. Call Gemini Flash with protagonist description + tone.
            2. Parse protagonist visual profile and style bible from response.
            3. Attach base ContentPolicy exclusions (hardcoded, always safe).
            4. Persist CharacterBible + StyleBible atomically via SessionStore.
            5. Return the constructed CharacterBible.

        Raises:
            CharacterBibleServiceError: on Gemini API failure, JSON parse
            error, or response failing structural validation.
        """
        prompt = _build_prompt(brief)

        try:
            data = await self._call_gemini(prompt)
        except Exception as exc:
            logger.error(
                "CharacterBibleService: Gemini call failed "
                "(session=%s, error_type=%s)",
                session_id,
                type(exc).__name__,
            )
            raise CharacterBibleServiceError(
                "Gemini Flash call failed in CharacterBibleService.initialise",
                cause=exc,
            ) from exc

        try:
            bible = _parse_bible_data(data, protagonist_name=brief.protagonist_name)
        except Exception as exc:
            logger.error(
                "CharacterBibleService: response parsing failed "
                "(session=%s, error_type=%s)",
                session_id,
                type(exc).__name__,
            )
            raise CharacterBibleServiceError(
                "Failed to parse Gemini response into CharacterBible",
                cause=exc,
            ) from exc

        try:
            await self._get_store().save_character_bible(session_id, bible)
        except Exception as exc:
            logger.error(
                "CharacterBibleService: persistence failed "
                "(session=%s, error_type=%s)",
                session_id,
                type(exc).__name__,
            )
            raise CharacterBibleServiceError(
                "Failed to persist CharacterBible to SessionStore",
                cause=exc,
            ) from exc

        logger.info(
            "CharacterBibleService: initialised (session=%s, protagonist=%s)",
            session_id,
            brief.protagonist_name,
        )
        return bible
