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
from uuid import UUID

from google import genai
from google.genai import types as genai_types

from app.config import settings, get_genai_client
from app.exceptions import CharacterBibleServiceError
from app.models.character_bible import (
    CharacterBible,
    CharacterRef,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.session import StoryBrief
from app.services.image_generation import ImagePrompt
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
            self._client = get_genai_client("CharacterBibleService")
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

    def build_image_prompt(
        self, bible: CharacterBible, page_scene: str, page_number: int
    ) -> ImagePrompt:
        """
        Build an ImagePrompt for Imagen from the CharacterBible and page scene.

        Pure function — performs no I/O.

        Rules:
        - text_prompt always includes: art style, color palette, mood,
          negative style terms, protagonist name/species/color/notable
          traits/attire, and the page_scene as the action description.
        - negative_style_terms are prefixed with "no " and joined as a
          negative clause.
        - reference_urls is empty for page 1; for pages 2–5 includes
          protagonist.reference_image_gcs_uri (if set).
        - For any CharacterRef whose name appears in page_scene, that
          character's reference_image_gcs_uri (if set) is appended to
          reference_urls.

        Args:
            bible:       The session's CharacterBible.
            page_scene:  The display text or action description for the page.
            page_number: The current page number (1–5).

        Returns:
            An ImagePrompt ready to pass to ImageGenerationService.generate.
        """
        p = bible.protagonist
        sb = bible.style_bible

        # Build protagonist description block
        attire_clause = f", wearing {p.attire}" if p.attire else ""
        traits_clause = ", ".join(p.notable_traits) if p.notable_traits else ""
        protagonist_desc = (
            f"a {p.color} {p.species_or_type} named {p.name}"
            f"{attire_clause}"
            + (f" with {traits_clause}" if traits_clause else "")
        )

        # Negative style terms prefixed with "no "
        neg_style = (
            ", ".join(
                t if t.startswith("no ") else f"no {t}"
                for t in sb.negative_style_terms
            )
            if sb.negative_style_terms
            else ""
        )

        # Negative constraints from content policy
        negatives = (
            ", ".join(bible.content_policy.exclusions)
            if bible.content_policy.exclusions
            else "no inappropriate content"
        )
        if neg_style:
            negatives = f"{negatives}, {neg_style}"

        text_prompt = (
            f"{sb.art_style} illustration for a children's bedtime story. "
            f"Scene: {page_scene.strip()} "
            f"Main character: {protagonist_desc}. "
            f"Art style: {sb.art_style}, color palette: {sb.color_palette}, "
            f"mood: {sb.mood}. "
            f"Avoid: {negatives}."
        )

        # Build reference_urls — empty for page 1
        reference_urls: list[str] = []
        if page_number > 1:
            if p.reference_image_gcs_uri:
                reference_urls.append(p.reference_image_gcs_uri)

            # Append secondary character references when they appear in the scene
            for char_ref in bible.character_refs:
                if char_ref.name in page_scene and char_ref.reference_image_gcs_uri:
                    reference_urls.append(char_ref.reference_image_gcs_uri)

        return ImagePrompt(text_prompt=text_prompt, reference_urls=reference_urls)

    async def set_reference_image(
        self, session_id: str, gcs_uri: str
    ) -> None:
        """
        Persist the page-1 illustration GCS URI as the protagonist reference image.

        Updates `protagonist.reference_image_gcs_uri` in Firestore and in the
        local CharacterBible document so subsequent build_image_prompt calls
        can include it.

        Args:
            session_id: The session whose CharacterBible to update.
            gcs_uri:    The gs:// URI of the page-1 illustration.
        """
        try:
            await self._get_store().update_character_bible_field(
                session_id, "protagonist.reference_image_gcs_uri", gcs_uri
            )
        except Exception as exc:
            logger.error(
                "CharacterBibleService: failed to set reference image "
                "(session=%s, error_type=%s)",
                session_id,
                type(exc).__name__,
            )
            raise CharacterBibleServiceError(
                "Failed to persist protagonist reference image URI",
                cause=exc,
            ) from exc

        logger.info(
            "CharacterBibleService: reference image set (session=%s, uri=%s)",
            session_id,
            gcs_uri,
        )

    async def add_secondary_character(
        self,
        session_id: str,
        char_ref: CharacterRef,
    ) -> None:
        """
        Append a secondary CharacterRef to the session's CharacterBible.

        Fetches the current CharacterBible, appends the new CharacterRef to
        ``character_refs``, and persists the updated document.

        Args:
            session_id: The session whose CharacterBible to update.
            char_ref:   The CharacterRef to append.

        Raises:
            CharacterBibleServiceError: on Firestore read or write failure.
        """
        try:
            store = self._get_store()
            bible = await store.get_character_bible(session_id)
            if bible is None:
                raise CharacterBibleServiceError(
                    f"CharacterBible not found for session {session_id}",
                    cause=None,
                )
            updated_refs = list(bible.character_refs) + [char_ref]
            serialised = [r.model_dump(mode="json") for r in updated_refs]
            await store.update_character_bible_field(
                session_id, "character_refs", serialised
            )
        except CharacterBibleServiceError:
            raise
        except Exception as exc:
            logger.error(
                "CharacterBibleService: failed to add secondary character "
                "(session=%s, char_id=%s, error_type=%s)",
                session_id,
                char_ref.char_id,
                type(exc).__name__,
            )
            raise CharacterBibleServiceError(
                "Failed to add secondary character to CharacterBible",
                cause=exc,
            ) from exc

        logger.info(
            "CharacterBibleService: secondary character added "
            "(session=%s, char_id=%s, name=%s)",
            session_id,
            char_ref.char_id,
            char_ref.name,
        )

    async def update_mood(
        self,
        session_id: str,
        new_mood: str,
        command_id: UUID | None = None,
    ) -> None:
        """
        Update StyleBible.mood (and last_updated_by_command_id) for a session.

        Called when a ``tone_change`` VoiceCommand is accepted (T-033).

        Steps:
            1. Fetch the current CharacterBible from Firestore.
            2. Update ``style_bible.mood`` and ``style_bible.last_updated_by_command_id``.
            3. Persist both the standalone StyleBible document and the embedded
               ``style_bible`` field in the CharacterBible document atomically.

        Args:
            session_id:  The session whose StyleBible to update.
            new_mood:    The new mood string from the tone_change command.
            command_id:  The VoiceCommand.command_id that triggered the change
                         (stored in ``last_updated_by_command_id`` for auditability).

        Raises:
            CharacterBibleServiceError: on Firestore read or write failure.
        """
        try:
            store = self._get_store()
            bible = await store.get_character_bible(session_id)
            if bible is None:
                raise CharacterBibleServiceError(
                    f"CharacterBible not found for session {session_id}",
                    cause=None,
                )
            updated_style = StyleBible(
                art_style=bible.style_bible.art_style,
                color_palette=bible.style_bible.color_palette,
                mood=new_mood,
                negative_style_terms=list(bible.style_bible.negative_style_terms),
                last_updated_by_command_id=command_id,
            )
            # Persist standalone StyleBible document
            await store.save_style_bible(session_id, updated_style)
            # Sync embedded style_bible in CharacterBible document
            await store.update_character_bible_field(
                session_id, "style_bible", updated_style.model_dump(mode="json")
            )
        except CharacterBibleServiceError:
            raise
        except Exception as exc:
            logger.error(
                "CharacterBibleService: update_mood failed "
                "(session=%s, error_type=%s)",
                session_id,
                type(exc).__name__,
            )
            raise CharacterBibleServiceError(
                "Failed to update StyleBible.mood",
                cause=exc,
            ) from exc

        logger.info(
            "CharacterBibleService: mood updated (session=%s, mood=%s)",
            session_id,
            new_mood,
        )
