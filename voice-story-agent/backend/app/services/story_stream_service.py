"""
StoryStreamService — interleaved text + image generation via Gemini 2.5 Flash.

Uses ``generate_content_stream`` with ``response_modalities=["TEXT", "IMAGE"]``
to produce story page text and an illustration in a single streaming call.

Public interface:
    async def generate_page_stream(
        beat: str,
        page_history: list[str],
        bible: CharacterBible,
    ) -> AsyncIterator[TextChunk | ImageChunk]

The caller iterates the async generator and receives ``TextChunk`` objects
(incremental text fragments) and ``ImageChunk`` objects (inline image data)
as they arrive from the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, Union

from google import genai
from google.genai import types as genai_types

from app.config import settings, get_genai_client
from app.models.character_bible import CharacterBible

logger = logging.getLogger(__name__)


@dataclass
class TextChunk:
    """Incremental text fragment from the visual stream."""
    text: str


@dataclass
class ImageChunk:
    """Inline image blob from the visual stream."""
    data: bytes
    mime_type: str


StreamChunk = Union[TextChunk, ImageChunk]

_SYSTEM_PROMPT = """\
You are an expert children's story author and illustrator creating one page \
of an illustrated bedtime story for children aged 4–10.

YOUR TASK:
  Write the story text for this page AND generate a single beautiful \
illustration for it.

TEXT REQUIREMENTS:
  • 60–120 words of warm, vivid, age-appropriate prose.
  • Must directly advance the CURRENT BEAT provided.
  • Must maintain narrative continuity with PAGE HISTORY (if any).
  • Must reference the characters by the names given in the CURRENT BEAT.
  • Must NOT include any content from CONTENT EXCLUSIONS.

ILLUSTRATION REQUIREMENTS:
  • Generate exactly ONE illustration that captures the key moment of this page.
  • Use a warm, child-friendly art style matching the STYLE DESCRIPTION.
  • Feature the protagonist prominently with the visual traits described.
  • Match the mood and setting described.

Write the story text first, then generate the illustration.
"""


def _build_prompt(
    beat: str,
    page_history: list[str],
    bible: CharacterBible,
) -> str:
    protagonist = bible.protagonist
    exclusions = bible.content_policy.exclusions
    style = bible.style_bible

    history_block = (
        "\n".join(
            f"  Page {i + 1}: {summary}"
            for i, summary in enumerate(page_history)
        )
        if page_history
        else "  (this is the first page)"
    )
    exclusion_block = (
        "\n".join(f"  • {ex}" for ex in exclusions)
        if exclusions
        else "  (none)"
    )

    return (
        f"PROTAGONIST\n"
        f"  Name:        {protagonist.name}\n"
        f"  Description: {protagonist.species_or_type}, {protagonist.color}"
        + (f", {protagonist.attire}" if protagonist.attire else "")
        + "\n"
        f"  Traits:      {', '.join(protagonist.notable_traits)}\n"
        f"\n"
        f"STYLE DESCRIPTION\n"
        f"  Art style:      {style.art_style}\n"
        f"  Color palette:  {style.color_palette}\n"
        f"  Mood:           {style.mood}\n"
        f"\n"
        f"CURRENT BEAT (what happens on this page):\n"
        f"  {beat}\n"
        f"\n"
        f"PAGE HISTORY (narrative context from prior pages):\n"
        f"{history_block}\n"
        f"\n"
        f"CONTENT EXCLUSIONS (must not appear in the text or illustration):\n"
        f"{exclusion_block}\n"
    )


class StoryStreamService:
    """
    Streams interleaved text + image for a single story page using
    Gemini 2.5 Flash with native image generation.

    Usage:
        svc = StoryStreamService()
        async for chunk in svc.generate_page_stream(beat, history, bible):
            if isinstance(chunk, TextChunk):
                ...  # incremental text
            elif isinstance(chunk, ImageChunk):
                ...  # illustration bytes
    """

    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = get_genai_client("StoryStreamService")
        return self._client

    async def generate_page_stream(
        self,
        beat: str,
        page_history: list[str],
        bible: CharacterBible,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream text chunks and image blobs for a single story page.

        Yields ``TextChunk`` and ``ImageChunk`` objects as the model produces
        them.  The caller is responsible for accumulating text and handling
        image bytes (e.g. persisting to GCS).
        """
        client = self._get_client()
        prompt = _build_prompt(beat, page_history, bible)

        response = await client.aio.models.generate_content_stream(
            model=settings.GEMINI_FLASH_IMAGE_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_modalities=["TEXT", "IMAGE"],
                temperature=0.7,
            ),
        )

        async for chunk in response:
            candidates = getattr(chunk, "candidates", None)
            if not candidates:
                continue
            content = getattr(candidates[0], "content", None)
            if content is None:
                continue
            for part in getattr(content, "parts", []):
                part_text = getattr(part, "text", None)
                if part_text:
                    yield TextChunk(text=part_text)

                inline_data = getattr(part, "inline_data", None)
                if inline_data is not None:
                    data = getattr(inline_data, "data", None)
                    mime = getattr(inline_data, "mime_type", "image/png")
                    if data:
                        yield ImageChunk(data=data, mime_type=mime or "image/png")
