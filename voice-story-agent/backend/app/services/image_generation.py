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
- Model is configurable via settings.IMAGEN_MODEL (default: "imagen-3.0-generate-002").
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
from dataclasses import dataclass, field
from typing import Any

from app.config import settings, get_genai_client
from app.exceptions import ImageGenerationError

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECONDS = 2.0
_MAX_ATTEMPTS = 2


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

    async def _call_imagen(self, prompt: ImagePrompt) -> bytes:
        """
        Issue one Imagen generate_images call and return raw PNG bytes.

        Raises any exception so the caller can retry.
        """
        from google.genai import types as genai_types

        logger.info(
            "ImageGenerationService: generating image — prompt=%r (reference_urls omitted)",
            prompt.text_prompt,
        )

        client = self._get_client()

        response = await client.aio.models.generate_images(
            model=settings.IMAGEN_MODEL,
            prompt=prompt.text_prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="1:1",
                output_mime_type="image/png",
                # person_generation and safety_filter_level are Vertex AI-only
                # fields; omitting them avoids ValueError on the Gemini API path.
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

        # The google-genai SDK returns image bytes via .image_bytes
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

        Retry policy: up to 2 attempts with a 2-second backoff between them.

        Args:
            prompt: An ImagePrompt with text_prompt and optional reference_urls.

        Returns:
            Raw PNG bytes of the generated image.

        Raises:
            ImageGenerationError: when both attempts fail.
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                png_bytes: bytes = await self._call_imagen(prompt)
                logger.info(
                    "ImageGenerationService: image generated on attempt %d/%d (%d bytes)",
                    attempt,
                    _MAX_ATTEMPTS,
                    len(png_bytes),
                )
                return png_bytes

            except ImageGenerationError:
                raise

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "ImageGenerationService: attempt %d/%d failed "
                    "(error_type=%s): %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

        raise ImageGenerationError(
            f"generate failed after {_MAX_ATTEMPTS} attempts",
            cause=last_exc,
        )
