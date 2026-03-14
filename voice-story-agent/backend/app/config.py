"""
Application configuration loaded from environment variables / .env file.

Usage pattern for GCP services
───────────────────────────────
Every service that needs GCP must call the appropriate guard before its
first API call.  These guards raise clear RuntimeError messages so the
developer knows exactly what to set and how to authenticate.

Example:
    project_id = settings.require_gcp("VoiceSessionService")
    bucket     = settings.require_gcs_bucket()

Credentials are NEVER stored here.  The Google Cloud SDK picks them up
automatically from one of these sources (in priority order):
  1. GOOGLE_APPLICATION_CREDENTIALS env var → path to a service-account key
  2. gcloud auth application-default login  → writes ~/.config/gcloud/adc.json
  3. Cloud Run / GCE metadata server        → automatic in deployed environments
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Google Cloud Project ──────────────────────────────────────────────
    GCP_PROJECT_ID: Optional[str] = None
    GCP_REGION: str = "us-central1"

    # ── Cloud Storage ─────────────────────────────────────────────────────
    GCS_BUCKET_NAME: Optional[str] = None

    # ── Firestore ─────────────────────────────────────────────────────────
    FIRESTORE_DATABASE: str = "(default)"

    # ── Gemini Models ─────────────────────────────────────────────────────
    GEMINI_PRO_MODEL: str = "gemini-2.5-pro"
    GEMINI_FLASH_MODEL: str = "gemini-2.5-flash"

    # ── Imagen ────────────────────────────────────────────────────────────
    IMAGEN_MODEL: str = "imagen-3.0-generate-001"

    # ── Gemini Live (bidi-streaming voice) ────────────────────────────────
    GEMINI_LIVE_MODEL: str = "gemini-2.0-flash-live-001"

    # ── Cloud Text-to-Speech ──────────────────────────────────────────────
    TTS_VOICE_NAME: str = "en-US-Neural2-F"
    TTS_LANGUAGE_CODE: str = "en-US"

    # ── ADK ───────────────────────────────────────────────────────────────
    ADK_AGENT_NAME: str = "voice-story-agent"
    # Gemini Live API requires "global" region; falls back to GCP_REGION if unset
    GEMINI_LIVE_REGION: str = "global"

    # ── Google AI API Key (non-Vertex) ────────────────────────────────────
    GOOGLE_API_KEY: Optional[str] = None

    # ── Server ────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Derived helpers ───────────────────────────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        """Split comma-separated CORS_ORIGINS into a list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    def require_gcp(self, service_name: str) -> str:
        """
        Return GCP_PROJECT_ID or raise a developer-friendly error.

        Call this at the top of any method that needs Vertex AI, Gemini,
        Firestore, or Cloud TTS — but NOT at import time.
        """
        if not self.GCP_PROJECT_ID:
            raise RuntimeError(
                f"\n"
                f"  ❌  {service_name} requires GCP_PROJECT_ID to be set.\n"
                f"\n"
                f"  Add it to backend/.env:\n"
                f"      GCP_PROJECT_ID=your-gcp-project-id\n"
                f"\n"
                f"  Then authenticate with one of:\n"
                f"    • gcloud auth application-default login   (recommended for local dev)\n"
                f"    • export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json\n"
            )
        return self.GCP_PROJECT_ID

    def require_gcs_bucket(self) -> str:
        """Return GCS_BUCKET_NAME or raise a developer-friendly error."""
        if not self.GCS_BUCKET_NAME:
            raise RuntimeError(
                "\n"
                "  ❌  GCS_BUCKET_NAME is required for asset storage.\n"
                "\n"
                "  Add it to backend/.env:\n"
                "      GCS_BUCKET_NAME={your-project-id}-story-assets\n"
                "\n"
                "  Create the bucket first if it doesn't exist:\n"
                "      gcloud storage buckets create gs://{your-project-id}-story-assets \\\n"
                "        --location=us-central1 --uniform-bucket-level-access\n"
            )
        return self.GCS_BUCKET_NAME

    def startup_warnings(self) -> list[str]:
        """
        Return a list of warning messages for missing optional-at-startup vars.
        Logged on startup — the app still starts, but GCP calls will fail later.
        """
        warnings: list[str] = []
        if not self.GCP_PROJECT_ID:
            warnings.append(
                "GCP_PROJECT_ID is not set — Gemini, Firestore, GCS, and TTS calls "
                "will fail until it is configured. "
                "See backend/.env.example for setup instructions."
            )
        if not self.GCS_BUCKET_NAME:
            warnings.append(
                "GCS_BUCKET_NAME is not set — asset storage calls will fail. "
                "Set it in backend/.env when ready."
            )
        return warnings


settings = Settings()


def get_genai_client(service_name: str = "GenAI") -> "genai.Client":
    """
    Build a google.genai.Client using the best available credentials.

    Priority:
      1. GOOGLE_API_KEY set → AI Studio (generativelanguage) endpoint
      2. Otherwise          → Vertex AI endpoint (requires GCP_PROJECT_ID + ADC)
    """
    from google import genai  # lazy import to avoid circular deps

    if settings.GOOGLE_API_KEY:
        logger.info("%s: using Google AI (API key) endpoint", service_name)
        return genai.Client(api_key=settings.GOOGLE_API_KEY)

    project_id = settings.require_gcp(service_name)
    logger.info("%s: using Vertex AI endpoint (project=%s)", service_name, project_id)
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=settings.GCP_REGION,
    )
