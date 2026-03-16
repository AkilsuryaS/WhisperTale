"""
EditHandlerService — executes a classified EditDecision by routing to the
native streaming regeneration path.

Supports three scopes:
    global_character — update CharacterBible, regenerate all affected pages
    single_page      — rewrite one page
    cascade          — rewrite from page N through 5

Emits WebSocket events via the ``emit`` callable passed by the caller
(the story_ws handler).
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import WebSocket

from app.models.edit import EditDecision, EditScope
from app.services.character_bible_service import CharacterBibleService
from app.services.media_persistence import MediaPersistenceService
from app.services.session_store import SessionStore
from app.services.story_stream_service import StoryStreamService
from app.services.adk_voice_service import VoiceSessionService
from app.websocket.page_orchestrator import run_page_streamed

logger = logging.getLogger(__name__)


class EditHandlerService:
    """
    Executes an EditDecision against a session's story data.

    Uses the same interleaved text/image stream and live narrator pipeline
    as the main story-generation loop so edits stay consistent with creation.
    """

    def __init__(
        self,
        store: SessionStore,
        character_bible_svc: CharacterBibleService,
        story_stream_svc: StoryStreamService,
        voice_svc: VoiceSessionService | None,
        media_svc: MediaPersistenceService,
        ws: WebSocket,
    ) -> None:
        self._store = store
        self._bible_svc = character_bible_svc
        self._story_stream_svc = story_stream_svc
        self._voice_svc = voice_svc
        self._media_svc = media_svc
        self._ws = ws

    async def run_edit(
        self,
        session_id: str,
        decision: EditDecision,
        emit: Callable,
    ) -> None:
        """
        Execute the edit decision, emitting progress events to the frontend.

        Events emitted:
            edit_started        — scope + affected pages
            page_regenerating   — per affected page, before regeneration
            page_text_updated   — per page where text was rewritten
            page_image_updated  — per page where image was regenerated
            edit_complete       — on success
            edit_failed         — on unrecoverable error
        """
        try:
            await emit(
                "edit_started",
                scope=decision.scope,
                affected_pages=decision.affected_pages,
            )

            if decision.scope == EditScope.global_character:
                await self._handle_global_character(session_id, decision, emit)
            elif decision.scope == EditScope.single_page:
                await self._handle_single_page(session_id, decision, emit)
            elif decision.scope == EditScope.cascade:
                await self._handle_cascade(session_id, decision, emit)
            else:
                raise ValueError(f"Unknown edit scope: {decision.scope}")

            await emit("edit_complete")
            logger.info(
                "EditHandlerService: edit complete (session=%s, scope=%s)",
                session_id,
                decision.scope,
            )

        except Exception as exc:
            logger.error(
                "EditHandlerService: edit failed (session=%s, scope=%s): %s",
                session_id,
                decision.scope,
                exc,
            )
            try:
                await emit("edit_failed", error=str(exc))
            except Exception:
                pass

    async def _handle_global_character(
        self,
        session_id: str,
        decision: EditDecision,
        emit: Callable,
    ) -> None:
        """Update CharacterBible, then regenerate text + image + audio for all pages."""
        if decision.bible_patch:
            await self._bible_svc.apply_bible_patch(session_id, decision.bible_patch)

        # Build a fallback text instruction from the bible_patch when the
        # classifier didn't provide page_instructions.
        text_instruction: str | None = None
        if decision.page_instructions:
            first_key = next(iter(decision.page_instructions))
            text_instruction = decision.page_instructions[first_key]
        elif decision.bible_patch:
            patch_desc = ", ".join(
                f"{k} is now {v}" for k, v in decision.bible_patch.items()
            )
            text_instruction = (
                f"A character attribute has changed: {patch_desc}. "
                "Update every mention in the text to reflect this change. "
                "Keep the plot, structure, and tone identical."
            )

        sorted_pages = sorted(decision.affected_pages)
        page_history: list[str] = []

        first_affected = sorted_pages[0]
        for pn in range(1, first_affected):
            existing = await self._store.get_page(session_id, pn)
            if existing and existing.text:
                page_history.append(existing.text)

        for page_num in sorted_pages:
            await self._regenerate_page(
                session_id, page_num, text_instruction, emit,
                page_history=page_history,
            )
            updated = await self._store.get_page(session_id, page_num)
            if updated and updated.text:
                page_history.append(updated.text)

    async def _handle_single_page(
        self,
        session_id: str,
        decision: EditDecision,
        emit: Callable,
    ) -> None:
        """Rewrite text and image for a single page."""
        page_num = decision.affected_pages[0]
        instruction = decision.page_instructions.get(page_num, "")

        await self._regenerate_page(
            session_id, page_num, instruction, emit,
        )

    async def _handle_cascade(
        self,
        session_id: str,
        decision: EditDecision,
        emit: Callable,
    ) -> None:
        """Rewrite text and images from the starting page through the end."""
        sorted_pages = sorted(decision.affected_pages)
        page_history: list[str] = []

        # Build page history from pages before the first affected page
        first_affected = sorted_pages[0]
        for pn in range(1, first_affected):
            existing_page = await self._store.get_page(session_id, pn)
            if existing_page and existing_page.text:
                page_history.append(existing_page.text)

        for page_num in sorted_pages:
            instruction = decision.page_instructions.get(page_num)

            await self._regenerate_page(
                session_id, page_num, instruction, emit,
                page_history=page_history,
            )

            # Update page history for cascade coherence
            updated_page = await self._store.get_page(session_id, page_num)
            if updated_page and updated_page.text:
                page_history.append(updated_page.text)

    async def _regenerate_page(
        self,
        session_id: str,
        page_num: int,
        instruction: str | None,
        emit: Callable,
        page_history: list[str] | None = None,
    ) -> None:
        """Rewrite text, image, and narration for one page via native streaming."""
        await emit("page_regenerating", page=page_num)

        session = await self._store.get_session(session_id)
        bible = await self._store.get_character_bible(session_id)
        if bible is None:
            raise ValueError("CharacterBible not found")

        if not session.story_arc or len(session.story_arc) < page_num:
            raise ValueError(f"Story arc too short for page {page_num}")

        beat = session.story_arc[page_num - 1]

        # Build page history from session if not provided (single_page scope)
        if page_history is None:
            page_history = []
            for pn in range(1, page_num):
                existing = await self._store.get_page(session_id, pn)
                if existing and existing.text:
                    page_history.append(existing.text)

        accumulated_text = ""

        async def native_emit(event_type: str, **fields: object) -> None:
            nonlocal accumulated_text

            if event_type == "page_text_chunk":
                delta = str(fields.get("delta", ""))
                accumulated_text += delta
                await emit(
                    "page_text_updated",
                    page=page_num,
                    text=accumulated_text,
                    narration_script=accumulated_text,
                )
                return

            if event_type == "page_text_ready":
                text = str(fields.get("text", accumulated_text))
                accumulated_text = text
                await emit(
                    "page_text_updated",
                    page=page_num,
                    text=text,
                    narration_script=text,
                )
                return

            if event_type == "page_image_ready":
                await emit(
                    "page_image_updated",
                    page=page_num,
                    image_url=fields.get("image_url"),
                    gcs_uri=fields.get("gcs_uri"),
                )
                return

            if event_type == "page_audio_ready":
                await emit(
                    "page_audio_updated",
                    page=page_num,
                    audio_url=fields.get("audio_url"),
                    gcs_uri=fields.get("gcs_uri"),
                )
                return

            if event_type == "page_asset_failed":
                await emit(event_type, **fields)

        await run_page_streamed(
            session_id=session_id,
            page_number=page_num,
            beat=beat,
            page_history=page_history,
            bible=bible,
            edit_instruction=instruction,
            emit=native_emit,
            ws=self._ws,
            story_stream_svc=self._story_stream_svc,
            voice_svc=self._voice_svc,
            character_bible_svc=self._bible_svc,
            media_svc=self._media_svc,
            session_store=self._store,
        )
