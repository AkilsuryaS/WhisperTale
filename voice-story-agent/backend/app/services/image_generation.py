"""
ImageGenerationService — generates story-page illustrations using Imagen 3 on Vertex AI.

Public interface (T-023):
    @dataclass
    class ImagePrompt:
        text_prompt: str
        reference_urls: list[str]   # gs:// URIs for reference images (optional)

    class ImageGenerationService:
        async def generate(prompt: ImagePrompt) -> bytes   # raw PNG bytes

Design
------
- Uses vertexai.preview.vision_models.ImageGenerationModel (google-cloud-aiplatform).
- Model is configurable via settings.IMAGEN_MODEL (default: "imagen-3.0-generate-001").
- Reference images supplied as gs:// URIs are downloaded from GCS and passed
  as Image objects to the model's reference-image customisation API when present.
  When reference_urls is empty, a plain text-to-image call is made instead.
- Retry policy: 1 retry with a 2-second backoff.  After both attempts fail,
  raises ImageGenerationError.
- The prompt text (without reference URLs) is logged to the application logger
  on every generation attempt so it can be captured by Cloud Logging in production.
- vertexai and ImageGenerationModel are injectable via the constructor for full
  test isolation — production code initialises them lazily on first use.

vertexai SDK usage (google-cloud-aiplatform):
    vertexai.init(project=project_id, location=region)
    model = ImageGenerationModel.from_pretrained(model_name)
    images = model.generate_images(prompt=..., number_of_images=1, ...)
    raw_bytes = images[0]._image_bytes          # PNG bytes
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
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
    Generates story-page illustrations with Imagen 3 on Vertex AI.

    Usage:
        svc = ImageGenerationService()
        png_bytes = await svc.generate(
            ImagePrompt(
                text_prompt="A small blue rabbit exploring a sunlit meadow …",
                reference_urls=["gs://my-bucket/sessions/abc/protagonist.png"],
            )
        )

    The vertexai module and ImageGenerationModel class are injectable via the
    constructor to allow full unit-test isolation without real GCP calls.
    """

    def __init__(
        self,
        vertexai_module: Any | None = None,
        model_class: Any | None = None,
    ) -> None:
        self._vertexai = vertexai_module
        self._model_class = model_class
        self._model: Any | None = None
        self._initialised = False

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    def _ensure_initialised(self) -> None:
        """
        Initialise the Vertex AI SDK and load the Imagen model on first use.

        Separated from __init__ so importing the module never triggers GCP calls.
        """
        if self._initialised:
            return

        if self._vertexai is None:
            import vertexai as _vertexai  # type: ignore[import-not-found]

            self._vertexai = _vertexai

        if self._model_class is None:
            from vertexai.preview.vision_models import (  # type: ignore[import-not-found]
                ImageGenerationModel,
            )

            self._model_class = ImageGenerationModel

        project_id = settings.require_gcp("ImageGenerationService")
        self._vertexai.init(project=project_id, location=settings.GCP_REGION)
        self._model = self._model_class.from_pretrained(settings.IMAGEN_MODEL)
        self._initialised = True

    # ------------------------------------------------------------------
    # Reference-image helpers
    # ------------------------------------------------------------------

    def _load_reference_images(self, reference_urls: list[str]) -> list[Any]:
        """
        Download GCS objects and return a list of Image objects.

        Each URL must start with "gs://".  The bytes are fetched using
        google-cloud-storage and wrapped in an Image object from the Vertex AI
        vision_models SDK.

        Returns an empty list when ``reference_urls`` is empty.
        """
        if not reference_urls:
            return []

        from google.cloud import storage as gcs  # type: ignore[import-not-found]
        from vertexai.preview.vision_models import Image  # type: ignore[import-not-found]

        client = gcs.Client()
        images: list[Any] = []
        for uri in reference_urls:
            if not uri.startswith("gs://"):
                logger.warning(
                    "ImageGenerationService: skipping non-gs:// reference URL: %s", uri
                )
                continue
            # Parse bucket and blob path from "gs://bucket/path/to/object"
            without_prefix = uri[len("gs://"):]
            bucket_name, _, blob_path = without_prefix.partition("/")
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            image_bytes = blob.download_as_bytes()
            images.append(Image(image_bytes=image_bytes))

        return images

    # ------------------------------------------------------------------
    # Core generation (single attempt)
    # ------------------------------------------------------------------

    def _call_imagen(self, prompt: ImagePrompt) -> bytes:
        """
        Issue one synchronous Imagen call and return raw PNG bytes.

        Raises any exception from the Vertex AI SDK so the caller can retry.
        """
        self._ensure_initialised()

        logger.info(
            "ImageGenerationService: generating image — prompt=%r (reference_urls omitted)",
            prompt.text_prompt,
        )

        reference_images = self._load_reference_images(prompt.reference_urls)

        if reference_images:
            # Subject-style customisation: pass reference images to guide generation
            images = self._model.generate_images(  # type: ignore[union-attr]
                prompt=prompt.text_prompt,
                number_of_images=1,
                reference_images=reference_images,
                aspect_ratio="1:1",
                person_generation="allow_all",
                safety_filter_level="block_medium_and_above",
            )
        else:
            images = self._model.generate_images(  # type: ignore[union-attr]
                prompt=prompt.text_prompt,
                number_of_images=1,
                aspect_ratio="1:1",
                person_generation="allow_all",
                safety_filter_level="block_medium_and_above",
            )

        if not images or len(images) == 0:
            raise ImageGenerationError(
                "Imagen returned zero images for prompt; "
                "the request may have been filtered by safety settings."
            )

        return images[0]._image_bytes  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def generate(self, prompt: ImagePrompt) -> bytes:
        """
        Generate an illustration for the given prompt and return raw PNG bytes.

        Retry policy: up to 2 attempts with a 2-second backoff between them.
        Logs the prompt text on every attempt (reference URLs are omitted from logs).

        Args:
            prompt: An ImagePrompt with text_prompt and optional reference_urls.

        Returns:
            Raw PNG bytes of the generated image (> 0 bytes on success).

        Raises:
            ImageGenerationError: when both attempts fail.
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                # _call_imagen is synchronous (Vertex AI SDK does not expose async).
                # Run it in the default thread-pool executor to avoid blocking the
                # event loop.
                loop = asyncio.get_event_loop()
                png_bytes: bytes = await loop.run_in_executor(
                    None, self._call_imagen, prompt
                )
                logger.info(
                    "ImageGenerationService: image generated on attempt %d/%d (%d bytes)",
                    attempt,
                    _MAX_ATTEMPTS,
                    len(png_bytes),
                )
                return png_bytes

            except ImageGenerationError:
                # Propagate safety-filter errors immediately — no point retrying.
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
