"""
VoiceSessionService — wraps the Google GenAI Live API for bidi-streaming voice.

Public interface (T-013):
    async def start(session_id: str, system_prompt: str) -> None
    async def send_audio(session_id: str, pcm_bytes: bytes) -> None
    async def end(session_id: str) -> None

Public interface (T-014):
    async def stream_turns(session_id: str) -> AsyncIterator[VoiceTurn]
    async def speak(session_id: str, text: str) -> None

Design:
- Active sessions are held in an in-memory dict keyed by session_id.
- Each entry stores (AsyncSession, AsyncExitStack) so the async context
  manager from AsyncLive.connect() stays open across multiple calls.
- The genai.Client is injectable via the constructor for testability.
- No references to ADK/SDK private classes (names starting with `_`).
- VoiceSessionNotFoundError → send_audio / end / stream_turns / speak on a session
  that is not open.
- VoiceSessionError         → any Google GenAI API failure.

Audio format for send_audio:
    PCM linear-16, 16 kHz, mono (16-bit signed, little-endian).
    MIME type: "audio/pcm;rate=16000"
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Literal

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.exceptions import VoiceSessionError, VoiceSessionNotFoundError

if TYPE_CHECKING:
    from google.genai.live import AsyncSession

logger = logging.getLogger(__name__)

_PCM_MIME_TYPE = "audio/pcm;rate=16000"
_SPEAK_TIMEOUT_SECONDS = 10.0


@dataclass
class VoiceTurn:
    """A single normalised turn emitted by stream_turns().

    Attributes:
        role:        "user" for speech from the child; "agent" for Gemini responses.
        transcript:  Text content of the turn (may be empty for audio-only agent turns).
        audio_bytes: Raw PCM/audio bytes for agent turns; None for user turns.
        is_final:    False while the transcript is still being streamed; True once
                     the turn is complete and no further updates will follow.
    """

    role: Literal["user", "agent"]
    transcript: str
    audio_bytes: bytes | None
    is_final: bool


def _build_client() -> genai.Client:
    """Return a Vertex AI–backed GenAI client from application settings."""
    project_id = settings.require_gcp("VoiceSessionService")
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=settings.GCP_REGION,
    )


class VoiceSessionService:
    """
    Manages bidi-streaming Gemini Live sessions keyed by session_id.

    One application-wide instance is typical; the in-memory dict is
    process-local so sessions do not survive restarts.

    Usage:
        svc = VoiceSessionService()
        await svc.start("sess-1", system_prompt="You are a storyteller…")
        await svc.send_audio("sess-1", pcm_chunk)
        await svc.end("sess-1")
    """

    def __init__(self, client: genai.Client | None = None) -> None:
        # Injecting a client allows unit tests to avoid real network calls.
        self._client = client
        # Maps session_id → (AsyncSession, AsyncExitStack)
        self._sessions: dict[str, tuple[AsyncSession, contextlib.AsyncExitStack]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> genai.Client:
        if self._client is None:
            self._client = _build_client()
        return self._client

    def _live_connect_config(self, system_prompt: str) -> genai_types.LiveConnectConfig:
        return genai_types.LiveConnectConfig(
            responseModalities=[genai_types.Modality.AUDIO],
            systemInstruction=genai_types.Content(
                parts=[genai_types.Part(text=system_prompt)],
                role="user",
            ),
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def start(self, session_id: str, system_prompt: str) -> None:
        """
        Open a bidi-streaming Gemini Live session.

        If a session for *session_id* is already open the call is a no-op
        (logs a warning). Raises VoiceSessionError on API failure.
        """
        if session_id in self._sessions:
            logger.warning(
                "VoiceSessionService.start called for already-open session %s; "
                "ignoring.",
                session_id,
            )
            return

        client = self._get_client()
        config = self._live_connect_config(system_prompt)
        stack = contextlib.AsyncExitStack()

        try:
            session: AsyncSession = await stack.enter_async_context(
                client.aio.live.connect(
                    model=settings.GEMINI_LIVE_MODEL,
                    config=config,
                )
            )
        except Exception as exc:
            await stack.aclose()
            raise VoiceSessionError(
                f"Failed to open Gemini Live session for '{session_id}': {exc}",
                cause=exc,
            ) from exc

        self._sessions[session_id] = (session, stack)
        logger.info("VoiceSession opened (session=%s)", session_id)

    async def send_audio(self, session_id: str, pcm_bytes: bytes) -> None:
        """
        Forward one PCM audio chunk (16-bit, 16 kHz, mono) to the open stream.

        Raises VoiceSessionNotFoundError if the session is not open.
        Raises VoiceSessionError on API failure.
        """
        if session_id not in self._sessions:
            raise VoiceSessionNotFoundError(session_id)

        session, _ = self._sessions[session_id]
        try:
            await session.send_realtime_input(
                audio=genai_types.Blob(
                    data=pcm_bytes,
                    mimeType=_PCM_MIME_TYPE,
                )
            )
        except Exception as exc:
            raise VoiceSessionError(
                f"send_audio failed for session '{session_id}': {exc}",
                cause=exc,
            ) from exc

    async def end(self, session_id: str) -> None:
        """
        Close the stream and remove the session entry.

        No-op (does not raise) if the session is already closed or was
        never opened — matches the spec requirement for idempotent end().
        """
        if session_id not in self._sessions:
            logger.debug(
                "VoiceSessionService.end called for unknown session %s; "
                "treating as no-op.",
                session_id,
            )
            return

        session, stack = self._sessions.pop(session_id)
        try:
            await session.close()
        except Exception as exc:
            logger.warning(
                "Error closing AsyncSession for session %s: %s", session_id, exc
            )
        try:
            await stack.aclose()
        except Exception as exc:
            logger.warning(
                "Error closing AsyncExitStack for session %s: %s", session_id, exc
            )

        logger.info("VoiceSession closed (session=%s)", session_id)

    async def stream_turns(self, session_id: str) -> AsyncIterator[VoiceTurn]:
        """
        Yield VoiceTurn objects as the ADK stream delivers events.

        Normalises all SDK-specific event shapes so callers never depend on
        ADK internals.  The sequence typically looks like:

          1. One or more partial user transcripts  (is_final=False, role="user")
          2. A final user transcript               (is_final=True,  role="user")
          3. One or more agent turns with audio    (is_final=True,  role="agent")

        Raises VoiceSessionNotFoundError if *session_id* is not open.
        Raises VoiceSessionError on SDK receive failures.
        """
        if session_id not in self._sessions:
            raise VoiceSessionNotFoundError(session_id)

        session, _ = self._sessions[session_id]

        try:
            async for response in session.receive():
                server_content = getattr(response, "server_content", None)
                if server_content is None:
                    continue

                # --- User input transcription (partial or final) ---
                input_tx = getattr(server_content, "input_transcription", None)
                if input_tx is not None:
                    text = getattr(input_tx, "text", "") or ""
                    # The SDK field may be "finished" or "is_final" depending on version
                    finished = getattr(input_tx, "finished", None)
                    if finished is None:
                        finished = getattr(input_tx, "is_final", True)
                    yield VoiceTurn(
                        role="user",
                        transcript=text,
                        audio_bytes=None,
                        is_final=bool(finished),
                    )

                # --- Agent model turn ---
                model_turn = getattr(server_content, "model_turn", None)
                if model_turn is not None:
                    audio_chunks: list[bytes] = []
                    text_parts: list[str] = []
                    for part in getattr(model_turn, "parts", []):
                        inline = getattr(part, "inline_data", None)
                        if inline is not None:
                            chunk = getattr(inline, "data", b"") or b""
                            if chunk:
                                audio_chunks.append(chunk)
                        part_text = getattr(part, "text", None)
                        if part_text:
                            text_parts.append(part_text)

                    turn_complete = bool(
                        getattr(server_content, "turn_complete", False)
                    )
                    yield VoiceTurn(
                        role="agent",
                        transcript="".join(text_parts),
                        audio_bytes=b"".join(audio_chunks) if audio_chunks else None,
                        is_final=turn_complete,
                    )

        except (VoiceSessionNotFoundError, VoiceSessionError):
            raise
        except Exception as exc:
            raise VoiceSessionError(
                f"stream_turns failed for session '{session_id}': {exc}",
                cause=exc,
            ) from exc

    async def speak(self, session_id: str, text: str) -> None:
        """
        Send *text* to the Gemini Live model for TTS and wait until the agent's
        audio response has been fully delivered.

        Applies a 10 s timeout; raises VoiceSessionError if the response does
        not complete in time.

        Raises VoiceSessionNotFoundError if *session_id* is not open.
        Raises VoiceSessionError on SDK API failures or timeout.
        """
        if session_id not in self._sessions:
            raise VoiceSessionNotFoundError(session_id)

        session, _ = self._sessions[session_id]

        # Send the text prompt to the model for voice synthesis.
        try:
            await session.send_client_content(
                turns=[
                    genai_types.Content(
                        parts=[genai_types.Part(text=text)],
                        role="user",
                    )
                ],
                turn_complete=True,
            )
        except Exception as exc:
            raise VoiceSessionError(
                f"speak failed to send text for session '{session_id}': {exc}",
                cause=exc,
            ) from exc

        # Wait for the agent to signal that its audio response is complete.
        async def _await_turn_complete() -> None:
            async for response in session.receive():
                server_content = getattr(response, "server_content", None)
                if server_content is None:
                    continue
                if getattr(server_content, "turn_complete", False):
                    return

        try:
            await asyncio.wait_for(
                _await_turn_complete(), timeout=_SPEAK_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise VoiceSessionError(
                f"speak timed out after {_SPEAK_TIMEOUT_SECONDS}s waiting for "
                f"audio response for session '{session_id}'",
                cause=exc,
            ) from exc
        except (VoiceSessionNotFoundError, VoiceSessionError):
            raise
        except Exception as exc:
            raise VoiceSessionError(
                f"speak receive loop failed for session '{session_id}': {exc}",
                cause=exc,
            ) from exc
