"""
PageOrchestrator — drives the generation of a single story page.

Public interface (T-025):
    async def run_page(
        session_id: str,
        page_number: int,
        beat: str,
        page_history: list[str],
        emit: Callable,                   # async (event_type, **fields) → None
        story_planner: StoryPlannerService,
        character_bible_svc: CharacterBibleService,
        image_svc: ImageGenerationService,
        tts_svc: TTSService,
        media_svc: MediaPersistenceService,
        session_store: SessionStore,
    ) -> None

Sequence
--------
1.  Emit ``page_generating``; persist Page(status="pending") to SessionStore.
2.  Call ``story_planner.expand_page`` → (text, narration_script).
    Emit ``page_text_ready``; update Page.status = text_ready.
3.  Retrieve CharacterBible; call ``character_bible_svc.build_image_prompt``.
4.  Launch image and TTS generation in parallel via ``asyncio.gather``:
    - Image: generate → store → signed URL → emit ``page_image_ready``
             OR emit ``page_asset_failed(asset_type="illustration")`` on error.
    - TTS:   synthesise → store → signed URL → emit ``page_audio_ready``
             OR emit ``page_asset_failed(asset_type="narration")`` on error.
5.  If page 1 and image succeeded: call ``character_bible_svc.set_reference_image``.
6.  Persist final Page (with status + failure flags) via SessionStore.
7.  Emit ``page_complete`` — ALWAYS fires, even when both assets fail.

Asset failures MUST NOT propagate; they are caught, logged, and reflected via
``page_asset_failed`` events and ``illustration_failed`` / ``audio_failed`` flags.
``page_complete`` is guaranteed to fire as the final event.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable

from app.models.page import Page, PageStatus
from app.services.character_bible_service import CharacterBibleService
from app.services.image_generation import ImageGenerationService
from app.services.media_persistence import MediaPersistenceService
from app.services.session_store import SessionStore
from app.services.story_planner import StoryPlannerService
from app.services.tts_service import TTSService, default_voice_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _generate_image_asset(
    *,
    session_id: str,
    page_number: int,
    bible,
    page_text: str,
    character_bible_svc: CharacterBibleService,
    image_svc: ImageGenerationService,
    media_svc: MediaPersistenceService,
    emit: Callable,
) -> tuple[bool, str | None]:
    """
    Generate and persist the illustration for one page.

    Returns:
        (success: bool, gcs_uri: str | None)
    """
    try:
        prompt = character_bible_svc.build_image_prompt(bible, page_text, page_number)
        png_bytes = await image_svc.generate(prompt)
        gcs_uri = await media_svc.store_illustration(session_id, page_number, png_bytes)
        signed_url = await media_svc.get_signed_url(gcs_uri)
        await emit(
            "page_image_ready",
            page=page_number,
            image_url=signed_url,
            gcs_uri=gcs_uri,
        )
        logger.info(
            "page illustration generated",
            extra={
                "event_type": "page_asset_ready",
                "session_id": session_id,
                "page_number": page_number,
                "asset_type": "illustration",
            },
        )
        return True, gcs_uri
    except Exception as exc:
        logger.warning(
            "PageOrchestrator: illustration failed (session=%s, page=%d, error_type=%s): %s",
            session_id,
            page_number,
            type(exc).__name__,
            exc,
        )
        await emit(
            "page_asset_failed",
            page=page_number,
            asset_type="illustration",
            error=str(exc),
        )
        logger.warning(
            "page illustration asset failed",
            extra={
                "event_type": "page_asset_failed",
                "session_id": session_id,
                "page_number": page_number,
                "asset_type": "illustration",
                "error_type": type(exc).__name__,
            },
        )
        return False, None


async def _generate_audio_asset(
    *,
    session_id: str,
    page_number: int,
    narration_script: str,
    tts_svc: TTSService,
    media_svc: MediaPersistenceService,
    emit: Callable,
) -> bool:
    """
    Synthesise and persist the narration audio for one page.

    Returns:
        success: bool
    """
    try:
        voice_config = default_voice_config()
        mp3_bytes = await tts_svc.synthesize(narration_script, voice_config)
        gcs_uri = await media_svc.store_narration(session_id, page_number, mp3_bytes)
        signed_url = await media_svc.get_signed_url(gcs_uri)
        await emit(
            "page_audio_ready",
            page=page_number,
            audio_url=signed_url,
            gcs_uri=gcs_uri,
        )
        logger.info(
            "page narration generated",
            extra={
                "event_type": "page_asset_ready",
                "session_id": session_id,
                "page_number": page_number,
                "asset_type": "narration",
            },
        )
        return True
    except Exception as exc:
        logger.warning(
            "PageOrchestrator: narration failed (session=%s, page=%d, error_type=%s): %s",
            session_id,
            page_number,
            type(exc).__name__,
            exc,
        )
        await emit(
            "page_asset_failed",
            page=page_number,
            asset_type="narration",
            error=str(exc),
        )
        logger.warning(
            "page narration asset failed",
            extra={
                "event_type": "page_asset_failed",
                "session_id": session_id,
                "page_number": page_number,
                "asset_type": "narration",
                "error_type": type(exc).__name__,
            },
        )
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_page(
    session_id: str,
    page_number: int,
    beat: str,
    page_history: list[str],
    emit: Callable,
    story_planner: StoryPlannerService,
    character_bible_svc: CharacterBibleService,
    image_svc: ImageGenerationService,
    tts_svc: TTSService,
    media_svc: MediaPersistenceService,
    session_store: SessionStore,
) -> None:
    """
    Orchestrate the full generation pipeline for a single story page.

    All service errors in the asset generation phase are caught internally.
    ``page_complete`` is always the final event emitted.

    Args:
        session_id:          The active session identifier.
        page_number:         1-based page index (1–5).
        beat:                The story beat for this page.
        page_history:        One-sentence summaries of all prior pages.
        emit:                Async callable — ``await emit(event_type, **fields)``.
        story_planner:       Service to expand the beat into display text + narration.
        character_bible_svc: Service holding visual consistency data.
        image_svc:           Service to generate PNG illustrations via Imagen.
        tts_svc:             Service to synthesise MP3 narration via Cloud TTS.
        media_svc:           Service to persist assets in GCS.
        session_store:       Firestore-backed persistence layer for Page documents.
    """
    illustration_failed = False
    audio_failed = False

    # ------------------------------------------------------------------
    # Step 1 — emit page_generating, persist pending Page
    # ------------------------------------------------------------------
    page = Page(page_number=page_number, beat=beat, status=PageStatus.pending)
    await emit("page_generating", page=page_number, beat=beat)
    await session_store.save_page(session_id, page)
    logger.info(
        "page generation started",
        extra={
            "event_type": "page_generation_started",
            "session_id": session_id,
            "page_number": page_number,
        },
    )
    _page_start_time = time.perf_counter()

    # ------------------------------------------------------------------
    # Step 2 — expand beat into text + narration_script
    # ------------------------------------------------------------------
    bible = await session_store.get_character_bible(session_id)
    _gemini_start = time.perf_counter()
    text, narration_script = await story_planner.expand_page(beat, page_history, bible)
    _gemini_elapsed_ms = round((time.perf_counter() - _gemini_start) * 1000)
    logger.info(
        "Gemini expand_page completed",
        extra={
            "event_type": "gemini_call_latency",
            "session_id": session_id,
            "page_number": page_number,
            "operation": "expand_page",
            "duration_ms": _gemini_elapsed_ms,
        },
    )

    page.text = text
    page.narration_script = narration_script
    page.status = PageStatus.text_ready
    await session_store.save_page(session_id, page)
    await emit("page_text_ready", page=page_number, text=text)

    # ------------------------------------------------------------------
    # Step 4 — image + TTS in parallel
    # ------------------------------------------------------------------
    image_task = _generate_image_asset(
        session_id=session_id,
        page_number=page_number,
        bible=bible,
        page_text=text,
        character_bible_svc=character_bible_svc,
        image_svc=image_svc,
        media_svc=media_svc,
        emit=emit,
    )
    audio_task = _generate_audio_asset(
        session_id=session_id,
        page_number=page_number,
        narration_script=narration_script,
        tts_svc=tts_svc,
        media_svc=media_svc,
        emit=emit,
    )

    results = await asyncio.gather(image_task, audio_task, return_exceptions=True)

    # Unpack results — each helper returns a value or an exception caught by gather
    image_result = results[0]
    audio_result = results[1]

    if isinstance(image_result, Exception):
        illustration_failed = True
        logger.error(
            "PageOrchestrator: unexpected image task exception (session=%s, page=%d): %s",
            session_id,
            page_number,
            image_result,
        )
        await emit(
            "page_asset_failed",
            page=page_number,
            asset_type="illustration",
            error=str(image_result),
        )
        image_gcs_uri = None
        image_succeeded = False
    else:
        image_succeeded, image_gcs_uri = image_result
        if not image_succeeded:
            illustration_failed = True

    if isinstance(audio_result, Exception):
        audio_failed = True
        logger.error(
            "PageOrchestrator: unexpected audio task exception (session=%s, page=%d): %s",
            session_id,
            page_number,
            audio_result,
        )
        await emit(
            "page_asset_failed",
            page=page_number,
            asset_type="narration",
            error=str(audio_result),
        )
    else:
        if not audio_result:
            audio_failed = True

    # ------------------------------------------------------------------
    # Step 5 — set reference image on page 1 success
    # ------------------------------------------------------------------
    if page_number == 1 and image_succeeded and image_gcs_uri:
        try:
            await character_bible_svc.set_reference_image(session_id, image_gcs_uri)
        except Exception as exc:
            logger.warning(
                "PageOrchestrator: set_reference_image failed (session=%s): %s",
                session_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Step 6 — persist final Page
    # ------------------------------------------------------------------
    page.illustration_failed = illustration_failed
    page.audio_failed = audio_failed
    page.status = PageStatus.complete
    page.generated_at = datetime.now(timezone.utc)
    await session_store.save_page(session_id, page)

    # ------------------------------------------------------------------
    # Step 7 — emit page_complete (ALWAYS)
    # ------------------------------------------------------------------
    await emit(
        "page_complete",
        page=page_number,
        illustration_failed=illustration_failed,
        audio_failed=audio_failed,
    )
    _page_elapsed_ms = round((time.perf_counter() - _page_start_time) * 1000)
    logger.info(
        "page generation complete",
        extra={
            "event_type": "page_generation_complete",
            "session_id": session_id,
            "page_number": page_number,
            "illustration_failed": illustration_failed,
            "audio_failed": audio_failed,
            "duration_ms": _page_elapsed_ms,
        },
    )
