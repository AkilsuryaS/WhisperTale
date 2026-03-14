"""
TTSService — synthesises narration audio using Google Cloud Text-to-Speech.

Public interface (T-024):
    @dataclass
    class VoiceConfig:
        voice_name:    str    # e.g. "en-US-Neural2-F"
        language_code: str    # e.g. "en-US"
        speaking_rate: float  # 0.85 for warm children's narration

    class TTSService:
        async def synthesize(script: str, voice_config: VoiceConfig) -> bytes

    def default_voice_config() -> VoiceConfig

Design
------
- Calls `google.cloud.texttospeech.TextToSpeechAsyncClient.synthesize_speech`.
- Audio encoding: MP3 (AudioEncoding.MP3). Returns raw MP3 bytes.
- Retry policy: 1 retry with a 1-second backoff between attempts.
  After both attempts fail, raises TTSError.
- The TextToSpeechAsyncClient is injectable via the constructor for full
  test isolation — the real client is created lazily on first use.
- Voice defaults are sourced from `settings.TTS_VOICE_NAME` and
  `settings.TTS_LANGUAGE_CODE` so they can be overridden via environment.
- `default_voice_config()` returns a VoiceConfig using the settings defaults
  with speaking_rate=0.85 (warm, child-friendly pace).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.exceptions import TTSError

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECONDS = 1.0
_MAX_ATTEMPTS = 2
_DEFAULT_SPEAKING_RATE = 1.0


# ---------------------------------------------------------------------------
# Public data structures
# ---------------------------------------------------------------------------


@dataclass
class VoiceConfig:
    """
    Cloud TTS voice parameters for a single synthesis request.

    Attributes:
        voice_name:    Neural2 or similar voice identifier (e.g. "en-US-Neural2-F").
        language_code: BCP-47 language tag (e.g. "en-US").
        speaking_rate: Speech speed multiplier; 1.0 = normal.
                       0.85 is recommended for children's narration (warm, unhurried).
    """

    voice_name: str
    language_code: str
    speaking_rate: float = _DEFAULT_SPEAKING_RATE


# ---------------------------------------------------------------------------
# Default config factory
# ---------------------------------------------------------------------------


def default_voice_config() -> VoiceConfig:
    """
    Return a VoiceConfig using the application settings defaults.

    Uses:
        voice_name    = settings.TTS_VOICE_NAME    (default: "en-US-Neural2-F")
        language_code = settings.TTS_LANGUAGE_CODE (default: "en-US")
        speaking_rate = 0.85 (warm, child-friendly pace)
    """
    return VoiceConfig(
        voice_name=settings.TTS_VOICE_NAME,
        language_code=settings.TTS_LANGUAGE_CODE,
        speaking_rate=_DEFAULT_SPEAKING_RATE,
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TTSService:
    """
    Synthesises narration text to MP3 audio via Cloud Text-to-Speech.

    Usage:
        svc = TTSService()
        mp3_bytes = await svc.synthesize(
            "Pip the rabbit stepped into the sunlit meadow.",
            default_voice_config(),
        )

    The TextToSpeechAsyncClient is injectable via the constructor to allow
    full unit-test isolation without real GCP calls.
    """

    def __init__(self, tts_client: Any | None = None) -> None:
        self._tts_client = tts_client

    # ------------------------------------------------------------------
    # Lazy client accessor
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """
        Return the Cloud TTS async client, creating it on first call.

        Importing google.cloud.texttospeech triggers no network I/O; the
        actual connection is deferred until the first RPC.
        """
        if self._tts_client is None:
            from google.cloud import texttospeech  # type: ignore[import-not-found]

            self._tts_client = texttospeech.TextToSpeechAsyncClient()
        return self._tts_client

    # ------------------------------------------------------------------
    # Core synthesis (single attempt)
    # ------------------------------------------------------------------

    async def _call_tts(self, script: str, voice_config: VoiceConfig) -> bytes:
        """
        Issue one Cloud TTS synthesize_speech call and return raw MP3 bytes.

        Raises any exception from the TTS client so the caller can retry.
        """
        from google.cloud import texttospeech  # type: ignore[import-not-found]
        import html

        client = self._get_client()

        # XML-escape the script to avoid breaking SSML when narration contains
        # quotes, ampersands, angle brackets, or other special characters.
        escaped = html.escape(script, quote=False)
        ssml_script = (
            "<speak>"
            f'<prosody rate="95%" pitch="+1st">{escaped}</prosody>'
            "</speak>"
        )
        synthesis_input = texttospeech.SynthesisInput(ssml=ssml_script)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=voice_config.language_code,
            name=voice_config.voice_name,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=voice_config.speaking_rate,
        )

        response = await client.synthesize_speech(
            input=synthesis_input,
            voice=voice_params,
            audio_config=audio_config,
        )
        return response.audio_content

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def synthesize(self, script: str, voice_config: VoiceConfig) -> bytes:
        """
        Synthesise *script* to MP3 audio and return raw bytes.

        Retry policy: up to 2 attempts with a 1-second backoff between them.

        Args:
            script:       The narration text to synthesise.
            voice_config: Voice and audio parameters (use default_voice_config()
                          for the application defaults).

        Returns:
            Raw MP3 bytes (non-empty on success).

        Raises:
            TTSError: when both attempts fail.
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                mp3_bytes = await self._call_tts(script, voice_config)
                logger.info(
                    "TTSService: synthesised %d chars → %d bytes (attempt %d/%d)",
                    len(script),
                    len(mp3_bytes),
                    attempt,
                    _MAX_ATTEMPTS,
                )
                return mp3_bytes

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "TTSService: attempt %d/%d failed (error_type=%s): %s",
                    attempt,
                    _MAX_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS)

        raise TTSError(
            f"synthesize failed after {_MAX_ATTEMPTS} attempts",
            cause=last_exc,
        )
