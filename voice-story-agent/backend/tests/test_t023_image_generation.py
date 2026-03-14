"""
Tests for T-023: ImageGenerationService.

Strategy
--------
The Vertex AI SDK (vertexai module and ImageGenerationModel class) and the
GCS client are injected / patched so no real GCP calls are made. _call_imagen
is patched for higher-level tests; unit tests call _load_reference_images and
_call_imagen directly on a service instance whose SDK dependencies are mocked.

Covers:
  generate — success:
    T23-01  returns PNG bytes > 0 on first attempt (no reference images)
    T23-02  returns PNG bytes > 0 on first attempt (with reference images)
    T23-03  _call_imagen is called with the ImagePrompt
    T23-04  Imagen model is called with number_of_images=1

  retry logic:
    T23-05  first attempt exception → retries a second time
    T23-06  success on second attempt (after first failure) → returns bytes
    T23-07  both attempts fail → raises ImageGenerationError
    T23-08  ImageGenerationError.cause is the last exception on total failure
    T23-09  asyncio.sleep(_RETRY_BACKOFF_SECONDS) is called between attempts

  ImageGenerationError propagation (safety filter):
    T23-10  ImageGenerationError raised by _call_imagen is re-raised immediately
            (no retry, no wrapping)

  empty images list:
    T23-11  when Imagen returns zero images → ImageGenerationError is raised

  reference images:
    T23-12  generate with non-empty reference_urls calls model with reference_images arg
    T23-13  generate with empty reference_urls calls model WITHOUT reference_images arg
    T23-14  non-gs:// reference URL is skipped with a warning (no crash)

  logging:
    T23-15  prompt text is logged on each attempt (reference URLs excluded)

  ImagePrompt dataclass:
    T23-16  ImagePrompt default reference_urls is an empty list
    T23-17  ImagePrompt stores text_prompt and reference_urls

  lazy initialisation:
    T23-18  _ensure_initialised is idempotent — called twice, SDK init runs once
    T23-19  vertexai.init is called with the correct project and location
    T23-20  ImageGenerationModel.from_pretrained is called with IMAGEN_MODEL setting
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.exceptions import ImageGenerationError
from app.services.image_generation import (
    ImageGenerationService,
    ImagePrompt,
    _RETRY_BACKOFF_SECONDS,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal valid-ish PNG header bytes

GS_URI_1 = "gs://my-bucket/sessions/abc/protagonist.png"
GS_URI_2 = "gs://my-bucket/sessions/abc/page1.png"


def _make_mock_image(png_bytes: bytes = FAKE_PNG) -> MagicMock:
    """Return a mock that looks like a vertexai Image object."""
    img = MagicMock()
    img._image_bytes = png_bytes
    return img


def _make_mock_model(images: list | None = None, side_effect: Exception | None = None) -> MagicMock:
    """Return a mock ImageGenerationModel with generate_images configured."""
    model = MagicMock()
    if side_effect is not None:
        model.generate_images.side_effect = side_effect
    else:
        model.generate_images.return_value = images if images is not None else [_make_mock_image()]
    return model


def _make_svc(model: MagicMock | None = None) -> ImageGenerationService:
    """
    Return an ImageGenerationService with all SDK dependencies pre-mocked
    and already initialised (skips lazy-init path).
    """
    mock_vertexai = MagicMock()
    mock_model_class = MagicMock()
    mock_model_instance = model or _make_mock_model()
    mock_model_class.from_pretrained.return_value = mock_model_instance

    svc = ImageGenerationService(
        vertexai_module=mock_vertexai,
        model_class=mock_model_class,
    )
    # Simulate already-initialised state
    svc._model = mock_model_instance
    svc._initialised = True
    return svc


# ---------------------------------------------------------------------------
# T23-01 — T23-04: generate success
# ---------------------------------------------------------------------------


class TestGenerateSuccess:
    @pytest.mark.anyio
    async def test_returns_bytes_no_reference_images(self) -> None:
        """T23-01: returns PNG bytes > 0 when reference_urls is empty."""
        svc = _make_svc()
        result = await svc.generate(ImagePrompt(text_prompt="a blue rabbit in a meadow"))
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.anyio
    async def test_returns_bytes_with_reference_images(self) -> None:
        """T23-02: returns PNG bytes > 0 when reference_urls is non-empty."""
        svc = _make_svc()
        fake_image_bytes = b"fakeimgdata"

        with patch.object(svc, "_load_reference_images", return_value=[MagicMock()]):
            result = await svc.generate(
                ImagePrompt(
                    text_prompt="a blue rabbit in a meadow",
                    reference_urls=[GS_URI_1],
                )
            )
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.anyio
    async def test_call_imagen_invoked_with_prompt(self) -> None:
        """T23-03: _call_imagen is called with the provided ImagePrompt."""
        svc = _make_svc()
        prompt = ImagePrompt(text_prompt="a warm cosy forest scene")
        with patch.object(svc, "_call_imagen", return_value=FAKE_PNG) as mock_call:
            await svc.generate(prompt)
        mock_call.assert_called_once_with(prompt)

    @pytest.mark.anyio
    async def test_imagen_model_called_with_number_of_images_1(self) -> None:
        """T23-04: generate_images is called with number_of_images=1."""
        svc = _make_svc()
        await svc.generate(ImagePrompt(text_prompt="a brave rabbit"))
        call_kwargs = svc._model.generate_images.call_args
        assert call_kwargs.kwargs.get("number_of_images") == 1


# ---------------------------------------------------------------------------
# T23-05 — T23-09: retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    @pytest.mark.anyio
    async def test_first_failure_triggers_retry(self) -> None:
        """T23-05: first attempt exception triggers a second _call_imagen call."""
        call_count = 0
        original_bytes = FAKE_PNG

        def fake_call_imagen(prompt: ImagePrompt) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient network error")
            return original_bytes

        svc = _make_svc()
        with patch.object(svc, "_call_imagen", side_effect=fake_call_imagen):
            with patch("app.services.image_generation.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.generate(ImagePrompt(text_prompt="meadow scene"))
        assert call_count == 2

    @pytest.mark.anyio
    async def test_success_on_second_attempt_returns_bytes(self) -> None:
        """T23-06: second attempt succeeds → returns valid bytes."""
        attempt = 0

        def fake_call(prompt: ImagePrompt) -> bytes:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("first failure")
            return FAKE_PNG

        svc = _make_svc()
        with patch.object(svc, "_call_imagen", side_effect=fake_call):
            with patch("app.services.image_generation.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.generate(ImagePrompt(text_prompt="a cosy scene"))
        assert result == FAKE_PNG

    @pytest.mark.anyio
    async def test_both_attempts_fail_raises_image_generation_error(self) -> None:
        """T23-07: both attempts fail → ImageGenerationError is raised."""
        svc = _make_svc()
        with patch.object(
            svc, "_call_imagen", side_effect=RuntimeError("API down")
        ):
            with patch("app.services.image_generation.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ImageGenerationError):
                    await svc.generate(ImagePrompt(text_prompt="a forest"))

    @pytest.mark.anyio
    async def test_error_cause_is_last_exception(self) -> None:
        """T23-08: ImageGenerationError.cause is the last exception raised."""
        first_exc = RuntimeError("first")
        second_exc = RuntimeError("second")
        side_effects = [first_exc, second_exc]

        svc = _make_svc()
        with patch.object(svc, "_call_imagen", side_effect=side_effects):
            with patch("app.services.image_generation.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ImageGenerationError) as exc_info:
                    await svc.generate(ImagePrompt(text_prompt="a scene"))
        assert exc_info.value.cause is second_exc

    @pytest.mark.anyio
    async def test_sleep_called_between_attempts(self) -> None:
        """T23-09: asyncio.sleep is called with _RETRY_BACKOFF_SECONDS between retries."""
        svc = _make_svc()
        with patch.object(svc, "_call_imagen", side_effect=[RuntimeError("fail"), FAKE_PNG]):
            with patch(
                "app.services.image_generation.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep:
                await svc.generate(ImagePrompt(text_prompt="scene"))
        mock_sleep.assert_called_once_with(_RETRY_BACKOFF_SECONDS)

    @pytest.mark.anyio
    async def test_no_sleep_on_first_attempt_success(self) -> None:
        """asyncio.sleep is NOT called when the first attempt succeeds."""
        svc = _make_svc()
        with patch.object(svc, "_call_imagen", return_value=FAKE_PNG):
            with patch(
                "app.services.image_generation.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep:
                await svc.generate(ImagePrompt(text_prompt="scene"))
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# T23-10: safety-filter ImageGenerationError propagation
# ---------------------------------------------------------------------------


class TestSafetyFilterPropagation:
    @pytest.mark.anyio
    async def test_image_generation_error_from_call_imagen_propagates_immediately(
        self,
    ) -> None:
        """T23-10: ImageGenerationError from _call_imagen is re-raised without retry."""
        safety_error = ImageGenerationError(
            "Imagen returned zero images; filtered by safety settings."
        )
        call_count = 0

        def fake_call(prompt: ImagePrompt) -> bytes:
            nonlocal call_count
            call_count += 1
            raise safety_error

        svc = _make_svc()
        with patch.object(svc, "_call_imagen", side_effect=fake_call):
            with patch("app.services.image_generation.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ImageGenerationError) as exc_info:
                    await svc.generate(ImagePrompt(text_prompt="a scene"))

        # Must NOT retry — only one call
        assert call_count == 1
        assert exc_info.value is safety_error


# ---------------------------------------------------------------------------
# T23-11: empty images list
# ---------------------------------------------------------------------------


class TestEmptyImagesList:
    @pytest.mark.anyio
    async def test_zero_images_from_model_raises_image_generation_error(self) -> None:
        """T23-11: when model.generate_images returns [] → ImageGenerationError."""
        svc = _make_svc(model=_make_mock_model(images=[]))
        with pytest.raises(ImageGenerationError, match="zero images"):
            await svc.generate(ImagePrompt(text_prompt="a scene"))


# ---------------------------------------------------------------------------
# T23-12 — T23-14: reference images
# ---------------------------------------------------------------------------


class TestReferenceImages:
    @pytest.mark.anyio
    async def test_non_empty_reference_urls_pass_reference_images_to_model(self) -> None:
        """T23-12: non-empty reference_urls → model called with reference_images kwarg."""
        svc = _make_svc()
        mock_ref_image = MagicMock()

        with patch.object(
            svc, "_load_reference_images", return_value=[mock_ref_image]
        ):
            await svc.generate(
                ImagePrompt(text_prompt="a scene", reference_urls=[GS_URI_1])
            )

        call_kwargs = svc._model.generate_images.call_args.kwargs
        assert "reference_images" in call_kwargs
        assert call_kwargs["reference_images"] == [mock_ref_image]

    @pytest.mark.anyio
    async def test_empty_reference_urls_does_not_pass_reference_images(self) -> None:
        """T23-13: empty reference_urls → model NOT called with reference_images kwarg."""
        svc = _make_svc()
        await svc.generate(ImagePrompt(text_prompt="a plain scene", reference_urls=[]))
        call_kwargs = svc._model.generate_images.call_args.kwargs
        assert "reference_images" not in call_kwargs

    def test_non_gs_uri_is_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """T23-14: non-gs:// URL is skipped with a warning, no crash."""
        svc = _make_svc()
        with patch("app.services.image_generation.gcs", create=True):
            with patch(
                "app.services.image_generation.ImageGenerationService._load_reference_images"
            ) as mock_load:
                mock_load.return_value = []
                # Call the actual method with a non-gs:// URL
                result = ImageGenerationService._load_reference_images.__wrapped__ if hasattr(
                    ImageGenerationService._load_reference_images, "__wrapped__"
                ) else None

        # Verify the logic directly via a real call with a mocked GCS
        svc2 = _make_svc()
        # Monkey-patch GCS import inside the method
        with patch.dict("sys.modules", {
            "google.cloud.storage": MagicMock(),
            "vertexai.preview.vision_models": MagicMock(),
        }):
            import sys
            mock_gcs = sys.modules["google.cloud.storage"]
            mock_gcs_client = MagicMock()
            mock_gcs.Client.return_value = mock_gcs_client

            import importlib
            import app.services.image_generation as img_mod
            with patch.object(img_mod, "logger") as mock_logger:
                result = svc2._load_reference_images(["http://not-gs-uri/file.png"])

        assert result == []


# ---------------------------------------------------------------------------
# T23-15: logging
# ---------------------------------------------------------------------------


class TestLogging:
    @pytest.mark.anyio
    async def test_prompt_text_logged_on_attempt(self, caplog: pytest.LogCaptureFixture) -> None:
        """T23-15: prompt text is included in the log on each generation attempt."""
        import logging

        svc = _make_svc()
        prompt_text = "a magical watercolour forest scene"

        with caplog.at_level(logging.INFO, logger="app.services.image_generation"):
            await svc.generate(ImagePrompt(text_prompt=prompt_text))

        # At least one log record should contain the prompt text
        assert any(prompt_text in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# T23-16 — T23-17: ImagePrompt dataclass
# ---------------------------------------------------------------------------


class TestImagePrompt:
    def test_default_reference_urls_is_empty_list(self) -> None:
        """T23-16: ImagePrompt default reference_urls is an empty list."""
        prompt = ImagePrompt(text_prompt="a cosy meadow")
        assert prompt.reference_urls == []

    def test_stores_text_prompt_and_reference_urls(self) -> None:
        """T23-17: ImagePrompt stores text_prompt and reference_urls."""
        prompt = ImagePrompt(
            text_prompt="Pip the rabbit",
            reference_urls=[GS_URI_1, GS_URI_2],
        )
        assert prompt.text_prompt == "Pip the rabbit"
        assert prompt.reference_urls == [GS_URI_1, GS_URI_2]

    def test_mutable_reference_urls_list(self) -> None:
        """reference_urls can be mutated after creation."""
        prompt = ImagePrompt(text_prompt="scene")
        prompt.reference_urls.append(GS_URI_1)
        assert GS_URI_1 in prompt.reference_urls


# ---------------------------------------------------------------------------
# T23-18 — T23-20: lazy initialisation
# ---------------------------------------------------------------------------


class TestLazyInitialisation:
    def test_ensure_initialised_is_idempotent(self) -> None:
        """T23-18: calling _ensure_initialised twice runs SDK init only once."""
        mock_vertexai = MagicMock()
        mock_model_class = MagicMock()
        mock_model_class.from_pretrained.return_value = MagicMock()

        with patch("app.services.image_generation.settings") as mock_settings:
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            mock_settings.IMAGEN_MODEL = "imagen-3.0-generate-001"

            svc = ImageGenerationService(
                vertexai_module=mock_vertexai,
                model_class=mock_model_class,
            )
            svc._ensure_initialised()
            svc._ensure_initialised()

        assert mock_vertexai.init.call_count == 1

    def test_vertexai_init_called_with_project_and_location(self) -> None:
        """T23-19: vertexai.init is called with the correct project and region."""
        mock_vertexai = MagicMock()
        mock_model_class = MagicMock()
        mock_model_class.from_pretrained.return_value = MagicMock()

        with patch("app.services.image_generation.settings") as mock_settings:
            mock_settings.require_gcp.return_value = "my-project-123"
            mock_settings.GCP_REGION = "europe-west4"
            mock_settings.IMAGEN_MODEL = "imagen-3.0-generate-001"

            svc = ImageGenerationService(
                vertexai_module=mock_vertexai,
                model_class=mock_model_class,
            )
            svc._ensure_initialised()

        mock_vertexai.init.assert_called_once_with(
            project="my-project-123", location="europe-west4"
        )

    def test_from_pretrained_called_with_imagen_model(self) -> None:
        """T23-20: ImageGenerationModel.from_pretrained is called with IMAGEN_MODEL."""
        mock_vertexai = MagicMock()
        mock_model_class = MagicMock()
        mock_model_class.from_pretrained.return_value = MagicMock()

        with patch("app.services.image_generation.settings") as mock_settings:
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            mock_settings.IMAGEN_MODEL = "imagen-3.0-generate-001"

            svc = ImageGenerationService(
                vertexai_module=mock_vertexai,
                model_class=mock_model_class,
            )
            svc._ensure_initialised()

        mock_model_class.from_pretrained.assert_called_once_with("imagen-3.0-generate-001")
