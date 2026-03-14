"""
Tests for T-010: MediaPersistenceService.

Strategy: inject a mock GCS Client so no real GCS calls are made.
Every test patches asyncio.to_thread to execute the sync callable
synchronously, keeping the test suite fast and free of I/O.

Covers:
- store_illustration: correct key, content-type image/png, returns gs:// URI
- store_narration:    correct key, content-type audio/mpeg, returns gs:// URI
- store_character_ref: correct key pattern, returns gs:// URI
- _upload: raises MediaPersistenceError on GoogleAPICallError
- get_signed_url: parses gs:// URI, calls generate_signed_url v4, returns url
- get_signed_url: raises MediaPersistenceError on GoogleAPICallError
- _parse_gcs_uri: raises ValueError on non-gs:// input
- gs:// URI format: f"gs://{bucket}/{key}"
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import GoogleAPICallError

from app.exceptions import MediaPersistenceError
from app.services.media_persistence import MediaPersistenceService

BUCKET = "my-project-story-assets"
SESSION_ID = "sess-abc123"
IMAGE_BYTES = b"\x89PNG\r\n\x1a\n"  # minimal PNG header bytes
AUDIO_BYTES = b"\xff\xfb\x90\x00"  # minimal MP3 header bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service() -> tuple[MediaPersistenceService, MagicMock]:
    """Return a service wired to a mock GCS client and mock bucket/blob."""
    mock_blob = MagicMock()
    mock_blob.upload_from_string = MagicMock()
    mock_blob.generate_signed_url = MagicMock(
        return_value="https://storage.googleapis.com/signed-url"
    )

    mock_bucket = MagicMock()
    mock_bucket.blob = MagicMock(return_value=mock_blob)

    mock_client = MagicMock()
    mock_client.bucket = MagicMock(return_value=mock_bucket)

    svc = MediaPersistenceService(client=mock_client)
    return svc, mock_client


def _patch_to_thread():
    """
    Patch asyncio.to_thread so it calls the sync function inline
    (avoids spawning real threads in unit tests).
    """

    async def _fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    return patch("app.services.media_persistence.asyncio.to_thread", side_effect=_fake_to_thread)


def _patch_settings(bucket_name: str = BUCKET, project_id: str = "test-project"):
    """Replace the module-level `settings` reference with a MagicMock."""
    mock_settings = MagicMock()
    mock_settings.require_gcs_bucket.return_value = bucket_name
    mock_settings.require_gcp.return_value = project_id
    return patch("app.services.media_persistence.settings", mock_settings)


# ---------------------------------------------------------------------------
# store_illustration
# ---------------------------------------------------------------------------


class TestStoreIllustration:
    @pytest.mark.asyncio
    async def test_returns_correct_gs_uri(self):
        svc, _ = _make_service()
        with _patch_to_thread(), _patch_settings():
            uri = await svc.store_illustration(SESSION_ID, 3, IMAGE_BYTES)
        assert uri == f"gs://{BUCKET}/sessions/{SESSION_ID}/pages/3/illustration.png"

    @pytest.mark.asyncio
    async def test_uploads_with_png_content_type(self):
        svc, mock_client = _make_service()
        with _patch_to_thread(), _patch_settings():
            await svc.store_illustration(SESSION_ID, 1, IMAGE_BYTES)

        blob = mock_client.bucket.return_value.blob.return_value
        blob.upload_from_string.assert_called_once_with(IMAGE_BYTES, content_type="image/png")

    @pytest.mark.asyncio
    async def test_uses_correct_blob_key(self):
        svc, mock_client = _make_service()
        with _patch_to_thread(), _patch_settings():
            await svc.store_illustration(SESSION_ID, 5, IMAGE_BYTES)

        expected_key = f"sessions/{SESSION_ID}/pages/5/illustration.png"
        mock_client.bucket.return_value.blob.assert_called_with(expected_key)

    @pytest.mark.asyncio
    async def test_raises_media_persistence_error_on_gcs_failure(self):
        svc, mock_client = _make_service()
        mock_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = (
            GoogleAPICallError("upload failed")
        )
        with _patch_to_thread(), _patch_settings(), pytest.raises(MediaPersistenceError):
            await svc.store_illustration(SESSION_ID, 1, IMAGE_BYTES)


# ---------------------------------------------------------------------------
# store_narration
# ---------------------------------------------------------------------------


class TestStoreNarration:
    @pytest.mark.asyncio
    async def test_returns_correct_gs_uri(self):
        svc, _ = _make_service()
        with _patch_to_thread(), _patch_settings():
            uri = await svc.store_narration(SESSION_ID, 2, AUDIO_BYTES)
        assert uri == f"gs://{BUCKET}/sessions/{SESSION_ID}/pages/2/narration.mp3"

    @pytest.mark.asyncio
    async def test_uploads_with_mpeg_content_type(self):
        svc, mock_client = _make_service()
        with _patch_to_thread(), _patch_settings():
            await svc.store_narration(SESSION_ID, 2, AUDIO_BYTES)

        blob = mock_client.bucket.return_value.blob.return_value
        blob.upload_from_string.assert_called_once_with(AUDIO_BYTES, content_type="audio/mpeg")

    @pytest.mark.asyncio
    async def test_uses_correct_blob_key(self):
        svc, mock_client = _make_service()
        with _patch_to_thread(), _patch_settings():
            await svc.store_narration(SESSION_ID, 7, AUDIO_BYTES)

        expected_key = f"sessions/{SESSION_ID}/pages/7/narration.mp3"
        mock_client.bucket.return_value.blob.assert_called_with(expected_key)

    @pytest.mark.asyncio
    async def test_raises_media_persistence_error_on_gcs_failure(self):
        svc, mock_client = _make_service()
        mock_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = (
            GoogleAPICallError("upload failed")
        )
        with _patch_to_thread(), _patch_settings(), pytest.raises(MediaPersistenceError):
            await svc.store_narration(SESSION_ID, 1, AUDIO_BYTES)


# ---------------------------------------------------------------------------
# store_character_ref
# ---------------------------------------------------------------------------


class TestStoreCharacterRef:
    @pytest.mark.asyncio
    async def test_returns_correct_gs_uri(self):
        svc, _ = _make_service()
        with _patch_to_thread(), _patch_settings():
            uri = await svc.store_character_ref(SESSION_ID, "dragon-spark", IMAGE_BYTES)
        assert uri == f"gs://{BUCKET}/sessions/{SESSION_ID}/characters/dragon-spark_ref.png"

    @pytest.mark.asyncio
    async def test_uses_correct_blob_key(self):
        svc, mock_client = _make_service()
        with _patch_to_thread(), _patch_settings():
            await svc.store_character_ref(SESSION_ID, "hero", IMAGE_BYTES)

        expected_key = f"sessions/{SESSION_ID}/characters/hero_ref.png"
        mock_client.bucket.return_value.blob.assert_called_with(expected_key)

    @pytest.mark.asyncio
    async def test_uploads_with_png_content_type(self):
        svc, mock_client = _make_service()
        with _patch_to_thread(), _patch_settings():
            await svc.store_character_ref(SESSION_ID, "hero", IMAGE_BYTES)

        blob = mock_client.bucket.return_value.blob.return_value
        blob.upload_from_string.assert_called_once_with(IMAGE_BYTES, content_type="image/png")

    @pytest.mark.asyncio
    async def test_raises_media_persistence_error_on_gcs_failure(self):
        svc, mock_client = _make_service()
        mock_client.bucket.return_value.blob.return_value.upload_from_string.side_effect = (
            GoogleAPICallError("upload failed")
        )
        with _patch_to_thread(), _patch_settings(), pytest.raises(MediaPersistenceError):
            await svc.store_character_ref(SESSION_ID, "hero", IMAGE_BYTES)


# ---------------------------------------------------------------------------
# get_signed_url
# ---------------------------------------------------------------------------

SIGNED_URL = "https://storage.googleapis.com/my-project-story-assets/sessions/sess-abc123/pages/1/illustration.png?X-Goog-Signature=abc"


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_https_url(self):
        svc, mock_client = _make_service()
        mock_client.bucket.return_value.blob.return_value.generate_signed_url.return_value = (
            SIGNED_URL
        )
        gcs_uri = f"gs://{BUCKET}/sessions/{SESSION_ID}/pages/1/illustration.png"
        with _patch_to_thread(), _patch_settings():
            url = await svc.get_signed_url(gcs_uri)
        assert url.startswith("https://")

    @pytest.mark.asyncio
    async def test_uses_v4_signature(self):
        svc, mock_client = _make_service()
        blob = mock_client.bucket.return_value.blob.return_value
        blob.generate_signed_url.return_value = SIGNED_URL

        gcs_uri = f"gs://{BUCKET}/sessions/{SESSION_ID}/pages/1/illustration.png"
        with _patch_to_thread(), _patch_settings():
            await svc.get_signed_url(gcs_uri, expiry_seconds=600)

        call_kwargs = blob.generate_signed_url.call_args
        assert call_kwargs.kwargs.get("version") == "v4" or call_kwargs[1].get("version") == "v4"

    @pytest.mark.asyncio
    async def test_respects_expiry_seconds(self):
        svc, mock_client = _make_service()
        blob = mock_client.bucket.return_value.blob.return_value
        blob.generate_signed_url.return_value = SIGNED_URL

        gcs_uri = f"gs://{BUCKET}/sessions/{SESSION_ID}/pages/1/illustration.png"
        with _patch_to_thread(), _patch_settings():
            await svc.get_signed_url(gcs_uri, expiry_seconds=7200)

        call_kwargs = blob.generate_signed_url.call_args
        expiration = call_kwargs.kwargs.get("expiration") or call_kwargs[1].get("expiration")
        assert expiration == datetime.timedelta(seconds=7200)

    @pytest.mark.asyncio
    async def test_parses_bucket_from_gcs_uri(self):
        svc, mock_client = _make_service()
        blob = mock_client.bucket.return_value.blob.return_value
        blob.generate_signed_url.return_value = SIGNED_URL

        gcs_uri = f"gs://other-bucket/sessions/{SESSION_ID}/pages/1/narration.mp3"
        with _patch_to_thread(), _patch_settings():
            await svc.get_signed_url(gcs_uri)

        mock_client.bucket.assert_called_with("other-bucket")

    @pytest.mark.asyncio
    async def test_raises_media_persistence_error_on_gcs_failure(self):
        svc, mock_client = _make_service()
        mock_client.bucket.return_value.blob.return_value.generate_signed_url.side_effect = (
            GoogleAPICallError("signing failed")
        )
        gcs_uri = f"gs://{BUCKET}/sessions/{SESSION_ID}/pages/1/illustration.png"
        with _patch_to_thread(), _patch_settings(), pytest.raises(MediaPersistenceError):
            await svc.get_signed_url(gcs_uri)

    @pytest.mark.asyncio
    async def test_invalid_uri_raises_value_error(self):
        svc, _ = _make_service()
        with pytest.raises(ValueError, match="Invalid GCS URI"):
            await svc.get_signed_url("https://not-a-gcs-uri/foo")


# ---------------------------------------------------------------------------
# _parse_gcs_uri (unit tests)
# ---------------------------------------------------------------------------


class TestParseGcsUri:
    def test_valid_uri(self):
        bucket, key = MediaPersistenceService._parse_gcs_uri(
            "gs://my-bucket/sessions/id/pages/1/illustration.png"
        )
        assert bucket == "my-bucket"
        assert key == "sessions/id/pages/1/illustration.png"

    def test_invalid_scheme_raises(self):
        with pytest.raises(ValueError):
            MediaPersistenceService._parse_gcs_uri("s3://bucket/key")

    def test_no_key_returns_empty_string(self):
        bucket, key = MediaPersistenceService._parse_gcs_uri("gs://bucket-only/")
        assert bucket == "bucket-only"
        assert key == ""


# ---------------------------------------------------------------------------
# gs:// URI format
# ---------------------------------------------------------------------------


class TestGsUri:
    def test_format(self):
        uri = MediaPersistenceService._gs_uri("my-bucket", "a/b/c.png")
        assert uri == "gs://my-bucket/a/b/c.png"

    def test_bucket_and_key_preserved(self):
        uri = MediaPersistenceService._gs_uri("proj-assets", "sessions/s1/pages/2/narration.mp3")
        assert "proj-assets" in uri
        assert "sessions/s1/pages/2/narration.mp3" in uri


# ---------------------------------------------------------------------------
# MediaPersistenceError
# ---------------------------------------------------------------------------


class TestMediaPersistenceError:
    def test_message_preserved(self):
        err = MediaPersistenceError("something went wrong")
        assert "something went wrong" in str(err)

    def test_cause_preserved(self):
        cause = ValueError("root cause")
        err = MediaPersistenceError("wrapper", cause=cause)
        assert err.cause is cause

    def test_cause_defaults_to_none(self):
        err = MediaPersistenceError("no cause")
        assert err.cause is None
