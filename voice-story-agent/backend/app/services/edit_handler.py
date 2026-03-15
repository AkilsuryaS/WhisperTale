"""
EditHandlerService — executes a classified EditDecision by routing to the
appropriate regeneration path using existing services.

Supports three scopes:
    global_character — update CharacterBible, regenerate images for all pages
    single_page      — rewrite text + image for one page
    cascade          — rewrite text + image from page N through 5

Emits WebSocket events via the ``emit`` callable passed by the caller
(the story_ws handler).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from app.models.edit import EditDecision, EditScope
from app.models.page import Page, PageStatus
from app.services.character_bible_service import CharacterBibleService
from app.services.image_generation import ImageGenerationService
from app.services.media_persistence import MediaPersistenceService
from app.services.session_store import SessionStore
from app.services.story_planner import StoryPlannerService
from app.services.tts_service import TTSService, default_voice_config

logger = logging.getLogger(__name__)


class EditHandlerService:
    """
    Executes an EditDecision against a session's story data.

    Reuses existing services for all heavy lifting — no new Gemini or
    Imagen integration is introduced.
    """

    def __init__(
        self,
        store: SessionStore,
        story_planner: StoryPlannerService,
        character_bible_svc: CharacterBibleService,
        image_svc: ImageGenerationService,
        tts_svc: TTSService,
        media_svc: MediaPersistenceService,
    ) -> None:
        self._store = store
        self._story_planner = story_planner
        self._bible_svc = character_bible_svc
        self._image_svc = image_svc
        self._tts_svc = tts_svc
        self._media_svc = media_svc

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
        """Update CharacterBible and regenerate images for all affected pages."""
        if decision.bible_patch:
            await self._bible_svc.apply_bible_patch(session_id, decision.bible_patch)

        bible = await self._store.get_character_bible(session_id)
        if bible is None:
            raise ValueError("CharacterBible not found after patch")

        tasks = []
        for page_num in decision.affected_pages:
            tasks.append(
                self._regenerate_image(session_id, page_num, bible, emit)
            )

        await asyncio.gather(*tasks, return_exceptions=True)

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
                snippet = " ".join(existing_page.text.split()[:25])
                page_history.append(snippet)

        for page_num in sorted_pages:
            instruction = decision.page_instructions.get(page_num)

            await self._regenerate_page(
                session_id, page_num, instruction, emit,
                page_history=page_history,
            )

            # Update page history for cascade coherence
            updated_page = await self._store.get_page(session_id, page_num)
            if updated_page and updated_page.text:
                snippet = " ".join(updated_page.text.split()[:25])
                page_history.append(snippet)

    async def _regenerate_page(
        self,
        session_id: str,
        page_num: int,
        instruction: str | None,
        emit: Callable,
        page_history: list[str] | None = None,
    ) -> None:
        """Rewrite text, regenerate image and audio for one page."""
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
                    snippet = " ".join(existing.text.split()[:25])
                    page_history.append(snippet)

        text, narration_script = await self._story_planner.expand_page(
            beat=beat,
            page_history=page_history,
            bible=bible,
            edit_instruction=instruction,
        )

        # Persist updated page
        page = Page(
            page_number=page_num,
            beat=beat,
            text=text,
            narration_script=narration_script,
            status=PageStatus.text_ready,
        )
        await self._store.save_page(session_id, page)

        await emit(
            "page_text_updated",
            page=page_num,
            text=text,
            narration_script=narration_script,
        )

        # Regenerate image and audio in parallel
        image_task = self._regenerate_image(session_id, page_num, bible, emit, text)
        audio_task = self._regenerate_audio(session_id, page_num, narration_script, emit)
        await asyncio.gather(image_task, audio_task, return_exceptions=True)

        # Mark page as complete
        page.status = PageStatus.complete
        page.generated_at = datetime.now(timezone.utc)
        await self._store.save_page(session_id, page)

    async def _regenerate_image(
        self,
        session_id: str,
        page_num: int,
        bible,
        emit: Callable,
        page_text: str | None = None,
    ) -> None:
        """Regenerate the illustration for a single page."""
        try:
            if page_text is None:
                existing_page = await self._store.get_page(session_id, page_num)
                if existing_page and existing_page.text:
                    page_text = existing_page.text
                else:
                    logger.warning(
                        "EditHandlerService: no page text for image regen "
                        "(session=%s, page=%d)",
                        session_id, page_num,
                    )
                    return

            prompt = self._bible_svc.build_image_prompt(bible, page_text, page_num)
            png_bytes = await self._image_svc.generate(prompt)
            gcs_uri = await self._media_svc.store_illustration(
                session_id, page_num, png_bytes
            )
            signed_url = await self._media_svc.get_signed_url(gcs_uri)

            await emit(
                "page_image_updated",
                page=page_num,
                image_url=signed_url,
                gcs_uri=gcs_uri,
            )
        except Exception as exc:
            logger.warning(
                "EditHandlerService: image regeneration failed "
                "(session=%s, page=%d): %s",
                session_id, page_num, exc,
            )
            await emit(
                "page_asset_failed",
                page=page_num,
                asset_type="illustration",
                error=str(exc),
            )

    async def _regenerate_audio(
        self,
        session_id: str,
        page_num: int,
        narration_script: str,
        emit: Callable,
    ) -> None:
        """Regenerate the narration audio for a single page."""
        try:
            voice_config = default_voice_config()
            mp3_bytes = await self._tts_svc.synthesize(narration_script, voice_config)
            gcs_uri = await self._media_svc.store_narration(
                session_id, page_num, mp3_bytes
            )
            signed_url = await self._media_svc.get_signed_url(gcs_uri)

            await emit(
                "page_audio_updated",
                page=page_num,
                audio_url=signed_url,
                gcs_uri=gcs_uri,
            )
        except Exception as exc:
            logger.warning(
                "EditHandlerService: audio regeneration failed "
                "(session=%s, page=%d): %s",
                session_id, page_num, exc,
            )
            await emit(
                "page_asset_failed",
                page=page_num,
                asset_type="narration",
                error=str(exc),
            )
