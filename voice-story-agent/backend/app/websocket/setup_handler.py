"""
SetupHandler — multi-turn story parameter extraction for the setup phase.

Public interface (T-020):
    SetupState    dataclass  — per-session mutable state shared between turns
    SetupHandler  class      — handle(ws, turn, session_id, voice_svc, state, store)

Flow per user turn
------------------
1. Increment turn_count; append transcript to raw_transcripts.
2. Call _extract_params() — single Gemini Flash call that returns
   protagonist_name, protagonist_description, setting, tone (any or all can be
   null) plus an optional follow_up_question.
3. Merge newly confirmed fields into SetupState; emit `story_brief_updated` for
   each field that moves from None → a value.
4. If all three parameters (protagonist, setting, tone) are confirmed, OR the
   session has consumed MAX_SETUP_TURNS, call _complete_setup():
      a. Persist StoryBrief via SessionStore.
      b. Generate a 5-beat arc with StoryPlannerService (using a minimal
         CharacterBible that carries just the base content-policy exclusions).
      c. Persist arc beats.
      d. Emit `story_brief_confirmed` with the full brief + agent_summary.
      e. Initialise full visual CharacterBible via CharacterBibleService.
      f. Emit `character_bible_ready`.
      g. Update Session.status → generating.
5. Otherwise, speak the follow_up_question returned by Gemini (or a default).

Design
------
- genai.Client, StoryPlannerService, and CharacterBibleService are all
  injectable (constructor params default to None → lazy-init in production).
- _extract_params catches *all* Gemini/parse exceptions and falls back to an
  empty ExtractedParams so a single bad response never crashes the session.
- _complete_setup wraps each sub-step in try/except and logs failures; the
  pipeline continues to the next step even if one step fails, so the client
  always gets story_brief_confirmed and character_bible_ready.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket

from app.config import settings
from app.exceptions import VoiceSessionError
from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.session import SessionStatus, StoryBrief, Tone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction system prompt
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = (
    "You are a children's story setup assistant. "
    "Extract story parameters from what the child or parent says.\n\n"
    "PARAMETERS TO EXTRACT:\n"
    "1. protagonist_name  — the character's name (e.g. 'Pip', 'Max', 'Luna')\n"
    "2. protagonist_description — visual description: species, colour, appearance "
    "(e.g. 'a small blue rabbit with floppy ears')\n"
    "3. setting — where the story takes place "
    "(e.g. 'the Meadow', 'an underwater kingdom')\n"
    "4. tone — the story mood; MUST be exactly one of: "
    "\"silly\", \"sleepy\", \"adventurous\", \"warm\", \"curious\"\n\n"
    "INSTRUCTIONS:\n"
    "- Extract ONLY what is clearly stated or strongly implied.\n"
    "- Do NOT repeat parameters that are already collected "
    "(shown in the prompt); leave those as null.\n"
    "- For tone, map: sleepy/bedtime/gentle → \"sleepy\"; "
    "funny/silly/playful → \"silly\"; exciting/adventure → \"adventurous\"; "
    "cosy/heartwarming → \"warm\"; curious/wonder/exploring → \"curious\".\n"
    "- If parameters are still missing, include follow_up_question: ask about "
    "exactly ONE missing parameter at a time in a warm, child-friendly voice.\n"
    "- If all parameters are now confirmed (including already-collected), "
    "set follow_up_question to null.\n\n"
    "OUTPUT FORMAT (JSON only — no prose before or after):\n"
    "{\n"
    '  "protagonist_name": "<name or null>",\n'
    '  "protagonist_description": "<description or null>",\n'
    '  "setting": "<setting or null>",\n'
    '  "tone": "<silly|sleepy|adventurous|warm|curious or null>",\n'
    '  "follow_up_question": "<friendly question or null>"\n'
    "}"
)

_VALID_TONES = {t.value for t in Tone}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ExtractedParams:
    """Parameters extracted from a single user utterance by Gemini."""

    protagonist_name: Optional[str] = None
    protagonist_description: Optional[str] = None
    setting: Optional[str] = None
    tone: Optional[str] = None
    follow_up_question: Optional[str] = None


@dataclass
class SetupState:
    """
    Per-session mutable state for the setup parameter collection phase.

    Shared between _turn_loop and the main WebSocket handler (similar to
    _SafetyGate). asyncio is single-threaded and cooperative, so reads/writes
    inside awaited coroutines are safe without locks.
    """

    protagonist_name: Optional[str] = None
    protagonist_description: Optional[str] = None
    setting: Optional[str] = None
    tone: Optional[str] = None  # Tone enum value as string, or None
    turn_count: int = 0
    raw_transcripts: list[str] = field(default_factory=list)

    @property
    def has_protagonist(self) -> bool:
        return bool(self.protagonist_name and self.protagonist_description)

    @property
    def has_setting(self) -> bool:
        return bool(self.setting)

    @property
    def has_tone(self) -> bool:
        return bool(self.tone)

    @property
    def all_confirmed(self) -> bool:
        return self.has_protagonist and self.has_setting and self.has_tone


# ---------------------------------------------------------------------------
# Emit helper (private — avoids circular import with story_ws)
# ---------------------------------------------------------------------------


async def _emit(ws: WebSocket, event_type: str, **fields: object) -> None:
    await ws.send_json({"type": event_type, **fields})


# ---------------------------------------------------------------------------
# Minimal CharacterBible factory (used as input to StoryPlannerService)
# ---------------------------------------------------------------------------


def _make_minimal_bible(brief: StoryBrief) -> CharacterBible:
    """
    Build an in-memory CharacterBible carrying only the base content-policy
    exclusions. This satisfies StoryPlannerService.create_arc's signature
    without requiring a full Gemini-derived visual profile.
    """
    from app.services.character_bible_service import BASE_EXCLUSIONS

    tone_str = brief.tone if isinstance(brief.tone, str) else brief.tone.value
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name=brief.protagonist_name,
            species_or_type="character",
            color="colourful",
            notable_traits=["curious", "brave"],
        ),
        style_bible=StyleBible(
            art_style="children's book illustration",
            color_palette="warm pastels",
            mood=tone_str,
            negative_style_terms=["dark", "scary"],
        ),
        content_policy=ContentPolicy(exclusions=list(BASE_EXCLUSIONS)),
    )


# ---------------------------------------------------------------------------
# Extraction prompt builder
# ---------------------------------------------------------------------------


def _build_extraction_prompt(transcript: str, state: SetupState) -> str:
    """Build the user-turn prompt for the Gemini extraction call."""
    collected: list[str] = []
    if state.protagonist_name:
        collected.append(f"  - Protagonist name: {state.protagonist_name}")
    if state.protagonist_description:
        collected.append(
            f"  - Protagonist description: {state.protagonist_description}"
        )
    if state.setting:
        collected.append(f"  - Setting: {state.setting}")
    if state.tone:
        collected.append(f"  - Tone: {state.tone}")

    collected_str = "\n".join(collected) if collected else "  (none yet)"

    missing: list[str] = []
    if not state.has_protagonist:
        missing.append("protagonist (name + description)")
    if not state.has_setting:
        missing.append("setting")
    if not state.has_tone:
        missing.append("tone")
    missing_str = ", ".join(missing) if missing else "none"

    return (
        f"ALREADY COLLECTED:\n{collected_str}\n\n"
        f"STILL NEEDED: {missing_str}\n\n"
        f"NEW UTTERANCE:\n{transcript}"
    )


# ---------------------------------------------------------------------------
# SetupHandler
# ---------------------------------------------------------------------------


class SetupHandler:
    """
    Stateless service that processes one user turn at a time, given a mutable
    SetupState.  Dependencies (genai.Client, StoryPlannerService,
    CharacterBibleService) are injectable for test isolation.
    """

    MAX_SETUP_TURNS: int = 3

    def __init__(
        self,
        client: object | None = None,
        story_planner: object | None = None,
        bible_svc: object | None = None,
    ) -> None:
        self._client = client
        self._story_planner = story_planner
        self._bible_svc = bible_svc

    # ── Lazy dependency accessors ─────────────────────────────────────────

    def _get_client(self) -> object:
        if self._client is None:
            from google import genai

            project_id = settings.require_gcp("SetupHandler")
            self._client = genai.Client(
                vertexai=True,
                project=project_id,
                location=settings.GCP_REGION,
            )
        return self._client

    def _get_story_planner(self) -> object:
        if self._story_planner is None:
            from app.services.story_planner import StoryPlannerService

            self._story_planner = StoryPlannerService()
        return self._story_planner

    def _get_bible_svc(self) -> object:
        if self._bible_svc is None:
            from app.services.character_bible_service import CharacterBibleService

            self._bible_svc = CharacterBibleService()
        return self._bible_svc

    # ── Main entry point ──────────────────────────────────────────────────

    async def handle(
        self,
        ws: WebSocket,
        turn: object,  # VoiceTurn — typed as object to avoid circular imports
        session_id: str,
        voice_svc: object,  # VoiceSessionService
        state: SetupState,
        store: object,  # SessionStore
    ) -> None:
        """
        Process one safety-cleared, final user turn during the setup phase.

        Mutates *state* in-place (turn_count, raw_transcripts, confirmed fields).
        """
        transcript: str = getattr(turn, "transcript", "")
        state.turn_count += 1
        state.raw_transcripts.append(transcript)

        # ── Extract parameters from this utterance ────────────────────────
        extracted = await self._extract_params(transcript, state)

        # ── Merge newly confirmed params; emit story_brief_updated events ─
        newly_confirmed: list[tuple[str, str]] = []

        if extracted.protagonist_name and not state.protagonist_name:
            state.protagonist_name = extracted.protagonist_name
            newly_confirmed.append(("protagonist_name", extracted.protagonist_name))
        if extracted.protagonist_description and not state.protagonist_description:
            state.protagonist_description = extracted.protagonist_description
            newly_confirmed.append(
                ("protagonist_description", extracted.protagonist_description)
            )
        if extracted.setting and not state.setting:
            state.setting = extracted.setting
            newly_confirmed.append(("setting", extracted.setting))
        if extracted.tone and not state.tone and extracted.tone in _VALID_TONES:
            state.tone = extracted.tone
            newly_confirmed.append(("tone", extracted.tone))

        for param, value in newly_confirmed:
            await _emit(ws, "story_brief_updated", parameter=param, value=value)

        # ── Complete setup if all params collected (or at turn limit) ─────
        if state.all_confirmed or state.turn_count >= self.MAX_SETUP_TURNS:
            await self._complete_setup(ws, session_id, voice_svc, state, store)
            return

        # ── Otherwise ask for the next missing parameter ──────────────────
        question = extracted.follow_up_question or self._default_follow_up(state)
        try:
            await voice_svc.speak(session_id, question)  # type: ignore[union-attr]
        except VoiceSessionError as exc:
            logger.warning(
                "SetupHandler: speak failed for follow-up (session=%s): %s",
                session_id,
                exc,
            )

    # ── Parameter extraction ──────────────────────────────────────────────

    async def _extract_params(
        self, transcript: str, state: SetupState
    ) -> ExtractedParams:
        """
        Call Gemini Flash to extract story parameters from *transcript*.

        On any failure (API error, JSON parse error, unexpected structure)
        returns an empty ExtractedParams so the session continues gracefully.
        """
        try:
            from google.genai import types as genai_types

            client = self._get_client()
            prompt = _build_extraction_prompt(transcript, state)
            response = await client.aio.models.generate_content(  # type: ignore[union-attr]
                model=settings.GEMINI_FLASH_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_EXTRACTION_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            data = json.loads(response.text)
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object from Gemini")

            def _str_or_none(v: object) -> Optional[str]:
                s = str(v).strip() if v not in (None, "", "null") else None
                return s if s else None

            return ExtractedParams(
                protagonist_name=_str_or_none(data.get("protagonist_name")),
                protagonist_description=_str_or_none(
                    data.get("protagonist_description")
                ),
                setting=_str_or_none(data.get("setting")),
                tone=_str_or_none(data.get("tone")),
                follow_up_question=_str_or_none(data.get("follow_up_question")),
            )
        except Exception as exc:
            logger.warning(
                "SetupHandler._extract_params failed (%s) — returning empty",
                type(exc).__name__,
            )
            return ExtractedParams()

    # ── Default follow-up question ────────────────────────────────────────

    def _default_follow_up(self, state: SetupState) -> str:
        if not state.protagonist_name:
            return "What is the name of our story's main character?"
        if not state.protagonist_description:
            return f"Can you tell me what {state.protagonist_name} looks like?"
        if not state.setting:
            return f"Where does {state.protagonist_name}'s adventure take place?"
        return (
            "What kind of story would you like? "
            "Silly, sleepy, adventurous, warm, or curious?"
        )

    # ── Complete setup pipeline ───────────────────────────────────────────

    async def _complete_setup(
        self,
        ws: WebSocket,
        session_id: str,
        voice_svc: object,
        state: SetupState,
        store: object,
    ) -> None:
        """
        Build and persist StoryBrief, generate arc, initialise CharacterBible,
        and emit story_brief_confirmed + character_bible_ready.
        """
        # Resolve tone (fill fallback if still missing at turn limit)
        try:
            tone_enum = Tone(state.tone) if state.tone else Tone.warm
        except ValueError:
            tone_enum = Tone.warm

        brief = StoryBrief(
            protagonist_name=state.protagonist_name or "the hero",
            protagonist_description=(
                state.protagonist_description or "a brave and curious character"
            ),
            setting=state.setting or "a magical land",
            tone=tone_enum,
            raw_setup_transcript=" ".join(state.raw_transcripts),
            confirmed_at=datetime.now(timezone.utc),
            confirmed_by_agent=True,
        )

        # Persist StoryBrief
        try:
            await store.save_story_brief(session_id, brief)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SetupHandler: save_story_brief failed (session=%s): %s",
                session_id,
                exc,
            )

        # Generate story arc (uses a minimal bible so we don't need the full
        # visual profile before generating the narrative structure)
        beats: list[str] = []
        minimal_bible = _make_minimal_bible(brief)
        try:
            beats = await self._get_story_planner().create_arc(brief, minimal_bible)  # type: ignore[union-attr]
            await store.update_story_arc(session_id, beats)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SetupHandler: create_arc failed (session=%s): %s", session_id, exc
            )

        # Build agent summary
        tone_str = brief.tone if isinstance(brief.tone, str) else brief.tone.value
        agent_summary = (
            f"A {tone_str} story about {brief.protagonist_name} "
            f"in {brief.setting}."
        )

        await _emit(
            ws,
            "story_brief_confirmed",
            brief=brief.model_dump(mode="json"),
            agent_summary=agent_summary,
        )

        # Initialise full visual CharacterBible
        try:
            await self._get_bible_svc().initialise(session_id, brief)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SetupHandler: CharacterBibleService.initialise failed "
                "(session=%s): %s",
                session_id,
                exc,
            )

        await _emit(ws, "character_bible_ready", session_id=session_id)

        # Update session status
        try:
            await store.update_session_status(session_id, SessionStatus.generating)  # type: ignore[union-attr]
        except Exception as exc:
            logger.error(
                "SetupHandler: update_session_status failed (session=%s): %s",
                session_id,
                exc,
            )
