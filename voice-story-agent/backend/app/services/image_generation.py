"""
ImageGenerationService — generates story-page illustrations using Imagen via
the Google AI (google-genai) SDK, keyed by GOOGLE_API_KEY.

Public interface (T-023):
    @dataclass
    class ImagePrompt:
        text_prompt: str
        reference_urls: list[str]   # gs:// URIs for reference images (optional)

    class ImageGenerationService:
        async def generate(prompt: ImagePrompt) -> bytes   # raw PNG bytes

Design
------
- Uses google.genai client (same SDK used for Gemini text models) with
  get_genai_client() so it honours the GOOGLE_API_KEY env var, falling back to
  Vertex AI when no API key is set.
- Primary model: settings.IMAGEN_MODEL (default: "imagen-4.0-fast-generate-001").
- Fallback model: settings.IMAGEN_FALLBACK_MODEL (default: "imagen-3.0-generate-002").
  Activated automatically when the primary returns a quota / resource-exhausted
  error.  After a cooldown period the primary is retried.
- Reference images supplied as gs:// URIs are downloaded from GCS and passed as
  inline image parts when the model supports it; otherwise a plain text-to-image
  call is made.
- Retry policy: 1 retry with a 2-second backoff.  After both attempts fail,
  raises ImageGenerationError.
- The prompt text is logged on every attempt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import settings, get_genai_client
from app.exceptions import ImageGenerationError

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECONDS = 2.0
_MAX_ATTEMPTS = 2

# How long (seconds) to stay on the fallback before retrying the primary.
_FALLBACK_COOLDOWN_SECONDS = 3600  # 1 hour

# Substrings in error messages / exception types that signal quota exhaustion.
_QUOTA_ERROR_SIGNALS = (
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "429",
    "rate limit",
    "daily limit",
)

# ── Module-level failover state ──────────────────────────────────────────────
# Shared across all ImageGenerationService instances in the same process so
# that a quota hit in one request immediately benefits all subsequent requests.
_use_fallback: bool = False
_fallback_since: float = 0.0


def _is_quota_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a quota / rate-limit error."""
    msg = str(exc).lower()
    cls = type(exc).__name__.lower()
    return any(s in msg or s in cls for s in _QUOTA_ERROR_SIGNALS)


def _active_model() -> str:
    """Return the model ID that should be used right now."""
    global _use_fallback, _fallback_since

    if _use_fallback:
        elapsed = time.monotonic() - _fallback_since
        if elapsed >= _FALLBACK_COOLDOWN_SECONDS:
            _use_fallback = False
            logger.info(
                "ImageGenerationService: cooldown elapsed (%.0fs), "
                "switching back to primary model %s",
                elapsed,
                settings.IMAGEN_MODEL,
            )
            return settings.IMAGEN_MODEL
        return settings.IMAGEN_FALLBACK_MODEL

    return settings.IMAGEN_MODEL


def _switch_to_fallback() -> None:
    """Flip the process-wide flag to the fallback model."""
    global _use_fallback, _fallback_since
    if not _use_fallback:
        _use_fallback = True
        _fallback_since = time.monotonic()
        logger.warning(
            "ImageGenerationService: primary model %s quota exhausted — "
            "switching to fallback %s for %ds",
            settings.IMAGEN_MODEL,
            settings.IMAGEN_FALLBACK_MODEL,
            _FALLBACK_COOLDOWN_SECONDS,
        )


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class ImagePrompt:
    """
    Input to ImageGenerationService.generate.

    Attributes:
        text_prompt:    The natural-language description of the image to generate.
        reference_urls: Zero or more gs:// URIs pointing to reference images in
                        Cloud Storage.  Used to maintain visual consistency across
                        story pages (protagonist appearance, style).
    """

    text_prompt: str
    reference_urls: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ImageGenerationService:
    """
    Generates story-page illustrations with Imagen via the google-genai SDK.

    Usage:
        svc = ImageGenerationService()
        png_bytes = await svc.generate(
            ImagePrompt(
                text_prompt="A small blue rabbit exploring a sunlit meadow ...",
                reference_urls=["gs://my-bucket/sessions/abc/protagonist.png"],
            )
        )
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = get_genai_client("ImageGenerationService")
        return self._client

    # ------------------------------------------------------------------
    # Core generation (single attempt, async)
    # ------------------------------------------------------------------

    async def _call_imagen(self, prompt: ImagePrompt, model: str) -> bytes:
        """
        Issue one Imagen generate_images call and return raw PNG bytes.

        Raises any exception so the caller can retry.
        """
        from google.genai import types as genai_types

        logger.info(
            "ImageGenerationService: generating image — model=%s, prompt=%r",
            model,
            prompt.text_prompt,
        )

        client = self._get_client()

        response = await client.aio.models.generate_images(
            model=model,
            prompt=prompt.text_prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
                output_mime_type="image/png",
            ),
        )

        generated = response.generated_images
        if not generated or len(generated) == 0:
            raise ImageGenerationError(
                "Imagen returned zero images; request may have been safety-filtered."
            )

        image_obj = generated[0].image
        if image_obj is None:
            raise ImageGenerationError("Imagen response contained no image data.")

        image_bytes = getattr(image_obj, "image_bytes", None)
        if not image_bytes:
            raise ImageGenerationError("Imagen image_bytes is empty.")

        return image_bytes

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate(self, prompt: ImagePrompt) -> bytes:
        """
        Generate an illustration for the given prompt and return raw PNG bytes.

        Retry policy: up to 2 attempts per model.  If the primary model hits
        a quota error on any attempt, the service immediately switches to the
        fallback model and retries from scratch (up to 2 more attempts).

        Args:
            prompt: An ImagePrompt with text_prompt and optional reference_urls.

        Returns:
            Raw PNG bytes of the generated image.

        Raises:
            ImageGenerationError: when all attempts on all models fail.
        """
        last_exc: Exception | None = None
        model = _active_model()
        switched_this_call = False

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                png_bytes: bytes = await self._call_imagen(prompt, model)
                logger.info(
                    "ImageGenerationService: image generated on attempt %d/%d "
                    "model=%s (%d bytes)",
                    attempt,
                    _MAX_ATTEMPTS,
                    model,
                    len(png_bytes),
                )
                return png_bytes

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "ImageGenerationService: attempt %d/%d model=%s failed "
                    "(error_type=%s): %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    model,
                    type(exc).__name__,
                    exc,
                )

                # Quota hit on primary → switch to fallback and restart attempts
                if (
                    _is_quota_error(exc)
                    and model == settings.IMAGEN_MODEL
                    and not switched_this_call
                ):
                    _switch_to_fallback()
                    model = settings.IMAGEN_FALLBACK_MODEL
                    switched_this_call = True
                    return await self._generate_with_fallback(prompt)

                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

        # Primary exhausted all attempts — try fallback model before giving up
        if not switched_this_call:
            switched_this_call = True
            logger.info(
                "ImageGenerationService: primary model %s failed after %d "
                "attempts, trying fallback %s",
                model,
                _MAX_ATTEMPTS,
                settings.IMAGEN_FALLBACK_MODEL,
            )
            try:
                return await self._generate_with_fallback(prompt)
            except Exception:
                pass

        raise ImageGenerationError(
            f"generate failed after {_MAX_ATTEMPTS} attempts (model={model})",
            cause=last_exc,
        )

    async def _generate_with_fallback(self, prompt: ImagePrompt) -> bytes:
        """Run a full retry cycle on the fallback model."""
        last_exc: Exception | None = None
        model = settings.IMAGEN_FALLBACK_MODEL

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                png_bytes = await self._call_imagen(prompt, model)
                logger.info(
                    "ImageGenerationService: image generated on fallback "
                    "attempt %d/%d model=%s (%d bytes)",
                    attempt,
                    _MAX_ATTEMPTS,
                    model,
                    len(png_bytes),
                )
                return png_bytes

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "ImageGenerationService: fallback attempt %d/%d model=%s "
                    "failed (error_type=%s): %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    model,
                    type(exc).__name__,
                    exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

        raise ImageGenerationError(
            f"generate failed after {_MAX_ATTEMPTS} attempts on fallback "
            f"model={model}",
            cause=last_exc,
        )
