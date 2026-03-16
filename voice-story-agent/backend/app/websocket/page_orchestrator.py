"""
PageOrchestrator — drives the generation of a single story page.

Legacy interface (used by EditHandlerService):
    async def run_page(...)   # Sequential: StoryPlanner → Imagen → Cloud TTS

Streaming interface (main page loop):
    async def run_page_streamed(...)   # Dual-Gemini: Flash TEXT+IMAGE + Live API narrator

``run_page_streamed`` replaces ``run_page`` in the main 5-page generation loop.
It uses a single Gemini 2.5 Flash call with ``response_modalities=["TEXT","IMAGE"]``
for interleaved text + image, and pipes text chunks into a Gemini Live API
narrator session for real-time PCM audio narration.

Asset failures MUST NOT propagate; they are caught, logged, and reflected via
``page_asset_failed`` events and ``illustration_failed`` / ``audio_failed`` flags.
``page_complete`` is guaranteed to fire as the final event.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import wave
from datetime import datetime, timezone
from typing import Callable

from fastapi import WebSocket

from app.models.page import Page, PageStatus
from app.services.adk_voice_service import VoiceSessionService
from app.services.character_bible_service import CharacterBibleService
from app.services.image_generation import ImageGenerationService
from app.services.media_persistence import MediaPersistenceService
from app.services.session_store import SessionStore
from app.services.story_planner import StoryPlannerService
from app.services.story_stream_service import (
    StoryStreamService,
    TextChunk,
    ImageChunk,
)
from app.services.tts_service import TTSService, default_voice_config

logger = logging.getLogger(__name__)
_LIVE_AUDIO_SAMPLE_RATE = 24000
_FALLBACK_IMAGE_TIMEOUT_SECONDS = 8.0


def _pcm_to_wav_bytes(
    pcm_bytes: bytes,
    *,
    sample_rate: int = _LIVE_AUDIO_SAMPLE_RATE,
    sample_width: int = 2,
    channels: int = 1,
) -> bytes:
    """Wrap raw little-endian PCM bytes in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


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


# ---------------------------------------------------------------------------
# Streaming entry point (dual-Gemini architecture)
# ---------------------------------------------------------------------------


async def run_page_streamed(
    session_id: str,
    page_number: int,
    beat: str,
    page_history: list[str],
    bible,
    edit_instruction: str | None,
    emit: Callable,
    ws: WebSocket,
    story_stream_svc: StoryStreamService,
    voice_svc: VoiceSessionService | None,
    character_bible_svc: CharacterBibleService,
    media_svc: MediaPersistenceService,
    session_store: SessionStore,
) -> None:
    """
    Orchestrate page generation using the dual-Gemini streaming architecture.

    1. Gemini 2.5 Flash with ``response_modalities=["TEXT","IMAGE"]`` streams
       interleaved text chunks and an illustration.
    2. Text chunks are piped into a Gemini Live API narrator session which
       produces PCM audio frames streamed as binary WebSocket payloads.

    Both streams run concurrently via asyncio.  Asset failures are caught
    internally.  ``page_complete`` is always the final event emitted.
    """
    illustration_failed = False
    audio_failed = False
    image_succeeded = False
    image_gcs_uri: str | None = None

    page = Page(page_number=page_number, beat=beat, status=PageStatus.pending)
    await emit("page_generating", page=page_number, beat=beat)
    await session_store.save_page(session_id, page)
    logger.info(
        "page streamed generation started",
        extra={
            "event_type": "page_generation_started",
            "session_id": session_id,
            "page_number": page_number,
        },
    )
    _page_start_time = time.perf_counter()

    # -- Start narrator Live session --
    narrator_available = False
    if voice_svc is not None:
        try:
            await voice_svc.start_narration(session_id)
            narrator_available = True
        except Exception as exc:
            logger.warning(
                "run_page_streamed: failed to start narrator (session=%s): %s",
                session_id,
                exc,
            )
            audio_failed = True

    # -- Shared state between concurrent tasks --
    full_text_parts: list[str] = []
    audio_frames: list[bytes] = []

    async def _visual_consumer() -> None:
        """Consume the visual stream: emit text chunks, pipe to narrator, handle images."""
        nonlocal illustration_failed, image_succeeded, image_gcs_uri

        try:
            async for chunk in story_stream_svc.generate_page_stream(
                beat, page_history, bible, edit_instruction
            ):
                if isinstance(chunk, TextChunk):
                    full_text_parts.append(chunk.text)
                    try:
                        await emit(
                            "page_text_chunk",
                            page=page_number,
                            delta=chunk.text,
                        )
                    except Exception:
                        pass
                    if narrator_available and voice_svc is not None:
                        try:
                            await voice_svc.send_narration_text(
                                session_id, chunk.text
                            )
                        except Exception as narr_exc:
                            logger.warning(
                                "run_page_streamed: send_narration_text failed "
                                "(session=%s, page=%d): %s",
                                session_id,
                                page_number,
                                narr_exc,
                            )

                elif isinstance(chunk, ImageChunk):
                    try:
                        gcs_uri = await media_svc.store_illustration(
                            session_id, page_number, chunk.data
                        )
                        signed_url = await media_svc.get_signed_url(gcs_uri)
                        image_gcs_uri = gcs_uri
                        image_succeeded = True
                        await emit(
                            "page_image_ready",
                            page=page_number,
                            image_url=signed_url,
                            gcs_uri=gcs_uri,
                        )
                        logger.info(
                            "page illustration generated (streamed)",
                            extra={
                                "event_type": "page_asset_ready",
                                "session_id": session_id,
                                "page_number": page_number,
                                "asset_type": "illustration",
                            },
                        )
                    except Exception as img_exc:
                        illustration_failed = True
                        logger.warning(
                            "run_page_streamed: illustration storage failed "
                            "(session=%s, page=%d): %s",
                            session_id,
                            page_number,
                            img_exc,
                        )
                        await emit(
                            "page_asset_failed",
                            page=page_number,
                            asset_type="illustration",
                            error=str(img_exc),
                        )
        except Exception as exc:
            logger.error(
                "run_page_streamed: visual stream failed "
                "(session=%s, page=%d): %s",
                session_id,
                page_number,
                exc,
            )
            illustration_failed = True
        finally:
            if narrator_available and voice_svc is not None:
                try:
                    await voice_svc.end_narration_turn(session_id)
                except Exception:
                    pass

    async def _audio_forwarder() -> None:
        """Forward PCM audio frames from the narrator to the WebSocket."""
        nonlocal audio_failed

        if not narrator_available or voice_svc is None:
            return

        try:
            async for pcm_frame in voice_svc.stream_narration_audio(
                session_id
            ):
                try:
                    audio_frames.append(pcm_frame)
                    await ws.send_bytes(pcm_frame)
                except Exception:
                    break
        except Exception as exc:
            audio_failed = True
            logger.warning(
                "run_page_streamed: narrator audio failed "
                "(session=%s, page=%d): %s",
                session_id,
                page_number,
                exc,
            )
            await emit(
                "page_asset_failed",
                page=page_number,
                asset_type="narration",
                error=str(exc),
            )

    # -- Run both streams concurrently --
    results = await asyncio.gather(
        _visual_consumer(),
        _audio_forwarder(),
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            task_name = "visual_consumer" if i == 0 else "audio_forwarder"
            logger.error(
                "run_page_streamed: %s raised (session=%s, page=%d): %s",
                task_name,
                session_id,
                page_number,
                result,
            )
            if i == 0:
                illustration_failed = True
            else:
                audio_failed = True

    # -- Close narrator session --
    if voice_svc is not None:
        await voice_svc.end_narration(session_id)

    if narrator_available:
        if audio_frames:
            try:
                wav_bytes = _pcm_to_wav_bytes(b"".join(audio_frames))
                gcs_uri = await media_svc.store_live_narration(
                    session_id, page_number, wav_bytes
                )
                signed_url = await media_svc.get_signed_url(gcs_uri)
                await emit(
                    "page_audio_ready",
                    page=page_number,
                    audio_url=signed_url,
                    gcs_uri=gcs_uri,
                )
            except Exception as exc:
                audio_failed = True
                logger.warning(
                    "run_page_streamed: live narration persistence failed "
                    "(session=%s, page=%d): %s",
                    session_id,
                    page_number,
                    exc,
                )
        else:
            audio_failed = True
            await emit(
                "page_asset_failed",
                page=page_number,
                asset_type="narration",
                error="Narrator produced no audio frames",
            )

    # -- If no image was produced by the model, flag it --
    if not image_succeeded and not illustration_failed:
        illustration_failed = True
        logger.warning(
            "run_page_streamed: no image produced by model "
            "(session=%s, page=%d)",
            session_id,
            page_number,
        )
        await emit(
            "page_asset_failed",
            page=page_number,
            asset_type="illustration",
            error="Model did not produce an illustration",
        )

    # -- Emit page_text_ready with full accumulated text --
    full_text = "".join(full_text_parts)
    if full_text:
        page.text = full_text
        page.narration_script = full_text
        page.status = PageStatus.text_ready
        await session_store.save_page(session_id, page)
        await emit("page_text_ready", page=page_number, text=full_text)

    # -- Fallback image request when streaming call produced text but no image --
    # Best-effort only: never let this block page completion indefinitely.
    if not image_succeeded and full_text:
        try:
            fallback = await asyncio.wait_for(
                story_stream_svc.generate_image_only(
                    beat=beat,
                    page_history=page_history,
                    bible=bible,
                    page_text=full_text,
                    edit_instruction=edit_instruction,
                ),
                timeout=_FALLBACK_IMAGE_TIMEOUT_SECONDS,
            )
            if fallback is not None:
                gcs_uri = await media_svc.store_illustration(
                    session_id, page_number, fallback.data
                )
                signed_url = await media_svc.get_signed_url(gcs_uri)
                image_gcs_uri = gcs_uri
                image_succeeded = True
                illustration_failed = False
                await emit(
                    "page_image_ready",
                    page=page_number,
                    image_url=signed_url,
                    gcs_uri=gcs_uri,
                )
                logger.info(
                    "page illustration generated via fallback",
                    extra={
                        "event_type": "page_asset_ready",
                        "session_id": session_id,
                        "page_number": page_number,
                        "asset_type": "illustration",
                    },
                )
        except asyncio.TimeoutError:
            logger.warning(
                "run_page_streamed: fallback image request timed out "
                "(session=%s, page=%d, timeout=%.1fs)",
                session_id,
                page_number,
                _FALLBACK_IMAGE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(
                "run_page_streamed: fallback image request failed "
                "(session=%s, page=%d): %s",
                session_id,
                page_number,
                exc,
            )

    # -- Reference image on page 1 --
    if page_number == 1 and image_succeeded and image_gcs_uri:
        try:
            await character_bible_svc.set_reference_image(
                session_id, image_gcs_uri
            )
            try:
                bible.protagonist.reference_image_gcs_uri = image_gcs_uri
            except Exception:
                pass
        except Exception as exc:
            logger.warning(
                "run_page_streamed: set_reference_image failed "
                "(session=%s): %s",
                session_id,
                exc,
            )

    # -- Persist final Page --
    page.illustration_failed = illustration_failed
    page.audio_failed = audio_failed
    page.status = PageStatus.complete
    page.generated_at = datetime.now(timezone.utc)
    await session_store.save_page(session_id, page)

    # -- page_complete (ALWAYS) --
    await emit(
        "page_complete",
        page=page_number,
        illustration_failed=illustration_failed,
        audio_failed=audio_failed,
    )
    _page_elapsed_ms = round((time.perf_counter() - _page_start_time) * 1000)
    logger.info(
        "page streamed generation complete",
        extra={
            "event_type": "page_generation_complete",
            "session_id": session_id,
            "page_number": page_number,
            "illustration_failed": illustration_failed,
            "audio_failed": audio_failed,
            "duration_ms": _page_elapsed_ms,
        },
    )
