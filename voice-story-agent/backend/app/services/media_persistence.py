"""
MediaPersistenceService — GCS-backed storage for story media assets.

GCS key patterns:
    sessions/{session_id}/pages/{page}/illustration.png
    sessions/{session_id}/pages/{page}/narration.mp3
    sessions/{session_id}/characters/{char_id}_ref.png

All store methods return a  gs://{bucket}/{key}  URI.
Signed URLs use v4 signatures and default to 1-hour expiry.

The google-cloud-storage client is synchronous; every GCS call is wrapped
in asyncio.to_thread() so the service is safe to await inside an async app.

Usage:
    svc = MediaPersistenceService()
    uri = await svc.store_illustration("sess-1", 2, png_bytes)
    url = await svc.get_signed_url(uri)
"""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import storage

from app.config import settings
from app.exceptions import MediaPersistenceError

if TYPE_CHECKING:
    from google.cloud.storage import Bucket, Client


def _gcs_client() -> "Client":
    """Return a GCS client using the project from settings."""
    return storage.Client(project=settings.require_gcp("MediaPersistenceService"))


class MediaPersistenceService:
    """Async wrapper around google-cloud-storage for story media assets."""

    def __init__(self, client: "Client | None" = None) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> "Client":
        if self._client is None:
            self._client = _gcs_client()
        return self._client

    def _bucket(self) -> "Bucket":
        bucket_name = settings.require_gcs_bucket()
        return self._get_client().bucket(bucket_name)

    @staticmethod
    def _gs_uri(bucket_name: str, key: str) -> str:
        return f"gs://{bucket_name}/{key}"

    @staticmethod
    def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
        """Parse  gs://bucket/key  into  (bucket, key)."""
        if not gcs_uri.startswith("gs://"):
            raise ValueError(f"Invalid GCS URI: {gcs_uri!r}")
        without_scheme = gcs_uri[5:]
        bucket, _, key = without_scheme.partition("/")
        return bucket, key

    # ------------------------------------------------------------------
    # Store methods
    # ------------------------------------------------------------------

    async def store_illustration(
        self, session_id: str, page: int, image_bytes: bytes
    ) -> str:
        """Upload illustration PNG and return its gs:// URI."""
        key = f"sessions/{session_id}/pages/{page}/illustration.png"
        return await self._upload(key, image_bytes, content_type="image/png")

    async def store_narration(
        self, session_id: str, page: int, audio_bytes: bytes
    ) -> str:
        """Upload narration MP3 and return its gs:// URI."""
        key = f"sessions/{session_id}/pages/{page}/narration.mp3"
        return await self._upload(key, audio_bytes, content_type="audio/mpeg")

    async def store_character_ref(
        self, session_id: str, char_id: str, image_bytes: bytes
    ) -> str:
        """Upload character reference PNG and return its gs:// URI."""
        key = f"sessions/{session_id}/characters/{char_id}_ref.png"
        return await self._upload(key, image_bytes, content_type="image/png")

    async def _upload(self, key: str, data: bytes, content_type: str) -> str:
        """Upload *data* to *key* in the configured bucket; return gs:// URI."""
        bucket_name = settings.require_gcs_bucket()

        def _sync_upload() -> None:
            bucket = self._get_client().bucket(bucket_name)
            blob = bucket.blob(key)
            blob.upload_from_string(data, content_type=content_type)

        try:
            await asyncio.to_thread(_sync_upload)
        except GoogleAPICallError as exc:
            raise MediaPersistenceError(
                f"GCS upload failed for key '{key}': {exc}", cause=exc
            ) from exc

        return self._gs_uri(bucket_name, key)

    # ------------------------------------------------------------------
    # Signed URL
    # ------------------------------------------------------------------

    async def get_signed_url(
        self, gcs_uri: str, expiry_seconds: int = 3600
    ) -> str:
        """Return a v4 signed HTTPS URL for *gcs_uri* valid for *expiry_seconds*."""
        bucket_name, key = self._parse_gcs_uri(gcs_uri)
        expiration = datetime.timedelta(seconds=expiry_seconds)

        def _sync_sign() -> str:
            bucket = self._get_client().bucket(bucket_name)
            blob = bucket.blob(key)
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                method="GET",
            )

        try:
            return await asyncio.to_thread(_sync_sign)
        except GoogleAPICallError as exc:
            raise MediaPersistenceError(
                f"Failed to generate signed URL for '{gcs_uri}': {exc}", cause=exc
            ) from exc
