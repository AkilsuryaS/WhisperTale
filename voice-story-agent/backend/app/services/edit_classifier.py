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

from app.config import settings, get_genai_live_client
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


def _parse_gemini_json(raw: str) -> dict[str, Any]:
    """Best-effort parse of Gemini's JSON output, handling common quirks."""
    text = raw.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    # Extract the outermost JSON object if surrounded by prose
    brace_start = text.find("{")
    if brace_start == -1:
        raise ValueError("No JSON object found in response")
    depth = 0
    brace_end = -1
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i
                break
    if brace_end == -1:
        raise ValueError("Unbalanced braces in response")
    text = text[brace_start : brace_end + 1]

    # Remove trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)

    # Remove single-line comments
    text = re.sub(r"//[^\n]*", "", text)

    return json.loads(text)


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
            self._client = get_genai_live_client("EditClassifierService")
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
            async with client.aio.live.connect(
                model=settings.GEMINI_LIVE_MODEL,
                config=genai_types.LiveConnectConfig(
                    responseModalities=[genai_types.Modality.TEXT],
                    systemInstruction=genai_types.Content(
                        parts=[genai_types.Part(text=_CLASSIFIER_SYSTEM_PROMPT)],
                        role="user",
                    ),
                ),
            ) as session:
                await session.send_client_content(
                    turns=[
                        genai_types.Content(
                            parts=[genai_types.Part(text=prompt)],
                            role="user",
                        )
                    ],
                    turn_complete=True,
                )

                response_parts: list[str] = []
                async for response in session.receive():
                    server_content = getattr(response, "server_content", None)
                    if server_content is None:
                        continue

                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn is not None:
                        for part in getattr(model_turn, "parts", []):
                            part_text = getattr(part, "text", None)
                            if part_text:
                                response_parts.append(part_text)

                    if getattr(server_content, "turn_complete", False):
                        break

            raw_text = "".join(response_parts)
            data: dict[str, Any] = _parse_gemini_json(raw_text)
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
