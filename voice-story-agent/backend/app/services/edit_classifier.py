"""
EditClassifierService — classifies a user edit instruction into an EditDecision.

Fetches the full story context (all 5 pages + CharacterBible) and uses a single
Gemini Flash call to determine what kind of edit is needed, which pages are
affected, and what specific changes should be made.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from google import genai
from google.genai import types as genai_types

from app.config import settings, get_genai_client
from app.models.edit import EditDecision, EditScope
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)


_CLASSIFIER_SYSTEM_PROMPT = """\
You are a story-editing assistant for a children's bedtime story app. You are \
given the full 5-page story, the CharacterBible (protagonist visual identity \
and style), and a user's edit instruction.

Your job is to classify what kind of edit the user wants and determine which \
pages are affected.

EDIT SCOPES:
  - "global_character": The user wants to change a character attribute (color, \
    attire, species) that affects visual identity across the whole story. \
    Text, images, and audio are ALL regenerated for every page to reflect \
    the change. Return bible_patch with dot-notation keys \
    (e.g. {"protagonist.color": "black"}) AND a page_instructions entry \
    that describes the text change to apply across all pages \
    (e.g. {1: "The cat is now black instead of white. Update all references."}).
  - "single_page": The user wants to change something on one specific page \
    only (emotion, action, dialogue) without affecting subsequent pages. \
    Both text and image for that page are regenerated.
  - "cascade": The user wants a narrative/plot change starting from a specific \
    page that must flow through to the end. Text and images are regenerated \
    from that page onward.

RULES:
  - affected_pages must be a list of 1-based page numbers (1–5).
  - For global_character: affected_pages = [1,2,3,4,5]. bible_patch must be \
    a dict of dot-notation field paths → new values. page_instructions must \
    have at least one entry with an instruction describing the attribute \
    change so page text can be updated (the same instruction is applied to \
    every affected page).
  - For single_page: affected_pages = [N]. page_instructions must have key N \
    with the specific rewrite instruction for that page.
  - For cascade: affected_pages = [N, N+1, ..., 5]. page_instructions must \
    have key N with the edit instruction (subsequent pages inherit coherence \
    from the updated page N).
  - If hint_page is provided, strongly prefer it as the target page for \
    single_page and cascade scopes.
  - reasoning should be a brief (1–2 sentence) explanation of your choice.

OUTPUT FORMAT — respond ONLY with valid JSON, no prose, no markdown:
{
  "scope": "<global_character | single_page | cascade>",
  "affected_pages": [<page numbers>],
  "bible_patch": <dict or null>,
  "page_instructions": {<page_number as int>: "<instruction string>"},
  "reasoning": "<brief explanation>"
}
"""


def _build_classifier_prompt(
    pages_text: dict[int, str],
    protagonist_summary: str,
    style_summary: str,
    instruction: str,
    hint_page: int | None,
) -> str:
    """Build the user-turn prompt with full story context."""
    pages_block = "\n".join(
        f"  Page {n}: {text}" for n, text in sorted(pages_text.items())
    )
    hint_block = f"\nHINT PAGE (user was viewing this page): {hint_page}" if hint_page else ""

    return (
        f"FULL STORY:\n{pages_block}\n"
        f"\n"
        f"CHARACTER BIBLE:\n"
        f"  Protagonist: {protagonist_summary}\n"
        f"  Style: {style_summary}\n"
        f"{hint_block}\n"
        f"\n"
        f"USER EDIT INSTRUCTION:\n"
        f"  {instruction}\n"
        f"\n"
        f"Classify this edit and return the JSON decision."
    )


class EditClassifierService:
    """
    Classifies a user's post-generation edit instruction into a structured
    EditDecision using Gemini Flash.
    """

    def __init__(
        self,
        client: genai.Client | None = None,
        store: SessionStore | None = None,
    ) -> None:
        self._client = client
        self._store = store

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = get_genai_client("EditClassifierService")
        return self._client

    def _get_store(self) -> SessionStore:
        if self._store is None:
            self._store = SessionStore()
        return self._store

    async def classify(
        self,
        session_id: str,
        instruction: str,
        hint_page: Optional[int] = None,
    ) -> EditDecision:
        """
        Classify the edit instruction against the full story context.

        Fetches all 5 page texts and the CharacterBible in parallel,
        then makes a single Gemini Flash call to produce an EditDecision.

        Raises:
            ValueError: on Gemini API failure or malformed response.
        """
        store = self._get_store()

        pages_task = store.list_pages(session_id)
        bible_task = store.get_character_bible(session_id)
        pages_list, bible = await asyncio.gather(pages_task, bible_task)

        pages_text: dict[int, str] = {}
        for p in pages_list:
            if p.text:
                pages_text[p.page_number] = p.text

        if not pages_text:
            raise ValueError("No story pages found for this session")
        if bible is None:
            raise ValueError("CharacterBible not found for this session")

        protagonist = bible.protagonist
        protagonist_summary = (
            f"{protagonist.name}, a {protagonist.color} {protagonist.species_or_type}"
            + (f", wearing {protagonist.attire}" if protagonist.attire else "")
            + f", traits: {', '.join(protagonist.notable_traits)}"
        )
        style_summary = (
            f"art_style={bible.style_bible.art_style}, "
            f"palette={bible.style_bible.color_palette}, "
            f"mood={bible.style_bible.mood}"
        )

        prompt = _build_classifier_prompt(
            pages_text=pages_text,
            protagonist_summary=protagonist_summary,
            style_summary=style_summary,
            instruction=instruction,
            hint_page=hint_page,
        )

        client = self._get_client()
        try:
            response = await client.aio.models.generate_content(
                model=settings.GEMINI_FLASH_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_CLASSIFIER_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            raw_text = response.text or ""
            # Gemini occasionally emits trailing commas; strip them.
            cleaned = re.sub(r",\s*([}\]])", r"\1", raw_text)
            data: dict[str, Any] = json.loads(cleaned)
        except Exception as exc:
            logger.error(
                "EditClassifierService: Gemini call failed (session=%s): %s",
                session_id,
                exc,
            )
            raise ValueError(
                f"Edit classification failed: {exc}"
            ) from exc

        try:
            # Gemini may return page_instructions keys as strings; normalise to int
            raw_instructions = data.get("page_instructions", {})
            normalised_instructions = {
                int(k): v for k, v in raw_instructions.items()
            }
            data["page_instructions"] = normalised_instructions

            decision = EditDecision(
                scope=EditScope(data["scope"]),
                affected_pages=data["affected_pages"],
                bible_patch=data.get("bible_patch"),
                page_instructions=normalised_instructions,
                reasoning=data.get("reasoning", ""),
            )
        except Exception as exc:
            logger.error(
                "EditClassifierService: response parsing failed (session=%s): %s",
                session_id,
                exc,
            )
            raise ValueError(
                f"Failed to parse edit classification response: {exc}"
            ) from exc

        logger.info(
            "EditClassifierService: classified (session=%s, scope=%s, pages=%s)",
            session_id,
            decision.scope,
            decision.affected_pages,
        )
        return decision
