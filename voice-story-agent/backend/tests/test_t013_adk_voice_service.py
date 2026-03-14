"""
Tests for T-013: VoiceSessionService — ADK bidi-stream open/close/audio.

Strategy: inject a mock genai.Client via the constructor so no real Gemini
Live calls are made.  Each mock wires the async context manager chain:

    client.aio.live.connect(model=..., config=...)
        → AsyncContextManager[AsyncSession]
            → mock_session (AsyncMock)

Covers:
  start()
    - opens a Gemini Live session (calls connect)
    - stores session in _sessions dict
    - repeat start() for same session_id is a no-op (logs warning)
    - raises VoiceSessionError on API failure
  send_audio()
    - calls session.send_realtime_input with Blob(data=pcm, mimeType=...)
    - raises VoiceSessionNotFoundError if session_id is not open
    - raises VoiceSessionError on send failure
  end()
    - calls session.close() and stack.aclose()
    - removes session from _sessions
    - is a no-op (no raise) for unknown session_id
    - after end(), session_id is no longer in _sessions
  exceptions
    - VoiceSessionNotFoundError carries session_id
    - VoiceSessionError carries cause
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import VoiceSessionError, VoiceSessionNotFoundError
from app.services.adk_voice_service import VoiceSessionService

SESSION_ID = "test-session-001"
SYSTEM_PROMPT = "You are a warm children's storyteller."
PCM_BYTES = b"\x00\x01" * 160  # 320 bytes of fake PCM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session() -> AsyncMock:
    """Return a mock AsyncSession with async send_realtime_input and close."""
    session = AsyncMock()
    session.send_realtime_input = AsyncMock()
    session.close = AsyncMock()
    return session


def _make_mock_client(session: AsyncMock | None = None, connect_raises: Exception | None = None):
    """
    Return a mock genai.Client where aio.live.connect() is an async
    context manager that yields *session*.
    """
    if session is None:
        session = _make_mock_session()

    # Build an async context manager that yields the session
    @contextlib.asynccontextmanager
    async def _fake_connect(**kwargs):
        if connect_raises:
            raise connect_raises
        yield session

    mock_live = MagicMock()
    mock_live.connect = MagicMock(side_effect=_fake_connect)

    mock_aio = MagicMock()
    mock_aio.live = mock_live

    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client, session


def _make_service(session: AsyncMock | None = None, connect_raises: Exception | None = None):
    mock_client, session = _make_mock_client(session=session, connect_raises=connect_raises)
    svc = VoiceSessionService(client=mock_client)
    return svc, mock_client, session


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------


class TestStart:
    @pytest.mark.asyncio
    async def test_calls_connect_with_model_and_config(self):
        svc, mock_client, _ = _make_service()
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)

        mock_client.aio.live.connect.assert_called_once()
        call_kwargs = mock_client.aio.live.connect.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.0-flash-live-001"
        assert call_kwargs["config"] is not None

    @pytest.mark.asyncio
    async def test_session_stored_after_start(self):
        svc, _, _ = _make_service()
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)

        assert SESSION_ID in svc._sessions

    @pytest.mark.asyncio
    async def test_repeat_start_is_noop(self):
        svc, mock_client, _ = _make_service()
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)
            await svc.start(SESSION_ID, SYSTEM_PROMPT)  # second call is no-op

        # connect() called only once
        assert mock_client.aio.live.connect.call_count == 1

    @pytest.mark.asyncio
    async def test_raises_voice_session_error_on_api_failure(self):
        svc, _, _ = _make_service(connect_raises=RuntimeError("quota exceeded"))
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            with pytest.raises(VoiceSessionError) as exc_info:
                await svc.start(SESSION_ID, SYSTEM_PROMPT)

        assert "quota exceeded" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_voice_session_error_has_cause(self):
        cause = RuntimeError("network error")
        svc, _, _ = _make_service(connect_raises=cause)
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            with pytest.raises(VoiceSessionError) as exc_info:
                await svc.start(SESSION_ID, SYSTEM_PROMPT)

        assert exc_info.value.cause is cause

    @pytest.mark.asyncio
    async def test_session_not_stored_on_api_failure(self):
        svc, _, _ = _make_service(connect_raises=RuntimeError("fail"))
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            with pytest.raises(VoiceSessionError):
                await svc.start(SESSION_ID, SYSTEM_PROMPT)

        assert SESSION_ID not in svc._sessions

    @pytest.mark.asyncio
    async def test_system_prompt_in_config(self):
        svc, mock_client, _ = _make_service()
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)

        config = mock_client.aio.live.connect.call_args.kwargs["config"]
        # system_instruction is the snake_case Pydantic attribute (camelCase alias used in ctor)
        assert SYSTEM_PROMPT in config.system_instruction.parts[0].text


# ---------------------------------------------------------------------------
# send_audio()
# ---------------------------------------------------------------------------


class TestSendAudio:
    async def _open_session(self, svc: VoiceSessionService) -> None:
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)

    @pytest.mark.asyncio
    async def test_calls_send_realtime_input(self):
        svc, _, mock_session = _make_service()
        await self._open_session(svc)
        await svc.send_audio(SESSION_ID, PCM_BYTES)
        mock_session.send_realtime_input.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_pcm_bytes_as_blob_data(self):
        svc, _, mock_session = _make_service()
        await self._open_session(svc)
        await svc.send_audio(SESSION_ID, PCM_BYTES)

        call_kwargs = mock_session.send_realtime_input.call_args.kwargs
        blob = call_kwargs["audio"]
        assert blob.data == PCM_BYTES

    @pytest.mark.asyncio
    async def test_uses_pcm_mime_type(self):
        svc, _, mock_session = _make_service()
        await self._open_session(svc)
        await svc.send_audio(SESSION_ID, PCM_BYTES)

        call_kwargs = mock_session.send_realtime_input.call_args.kwargs
        blob = call_kwargs["audio"]
        # mime_type is the snake_case Pydantic attribute (mimeType alias used in ctor)
        assert "audio/pcm" in blob.mime_type

    @pytest.mark.asyncio
    async def test_raises_voice_session_not_found_for_unknown_id(self):
        svc, _, _ = _make_service()
        with pytest.raises(VoiceSessionNotFoundError) as exc_info:
            await svc.send_audio("nonexistent-session", PCM_BYTES)
        assert exc_info.value.session_id == "nonexistent-session"

    @pytest.mark.asyncio
    async def test_raises_voice_session_error_on_send_failure(self):
        svc, _, mock_session = _make_service()
        mock_session.send_realtime_input = AsyncMock(side_effect=RuntimeError("send failed"))
        await self._open_session(svc)

        with pytest.raises(VoiceSessionError) as exc_info:
            await svc.send_audio(SESSION_ID, PCM_BYTES)
        assert "send_audio failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_voice_session_not_found_error_carries_session_id(self):
        svc, _, _ = _make_service()
        with pytest.raises(VoiceSessionNotFoundError) as exc_info:
            await svc.send_audio("missing-id", b"")
        assert "missing-id" in str(exc_info.value)


# ---------------------------------------------------------------------------
# end()
# ---------------------------------------------------------------------------


class TestEnd:
    async def _open_session(self, svc: VoiceSessionService) -> None:
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)

    @pytest.mark.asyncio
    async def test_calls_session_close(self):
        svc, _, mock_session = _make_service()
        await self._open_session(svc)
        await svc.end(SESSION_ID)
        mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_session_from_dict(self):
        svc, _, _ = _make_service()
        await self._open_session(svc)
        assert SESSION_ID in svc._sessions
        await svc.end(SESSION_ID)
        assert SESSION_ID not in svc._sessions

    @pytest.mark.asyncio
    async def test_end_unknown_session_is_noop(self):
        svc, _, _ = _make_service()
        # Should not raise
        await svc.end("never-opened-session")

    @pytest.mark.asyncio
    async def test_end_already_closed_session_is_noop(self):
        svc, _, _ = _make_service()
        await self._open_session(svc)
        await svc.end(SESSION_ID)
        # Second call — must not raise
        await svc.end(SESSION_ID)

    @pytest.mark.asyncio
    async def test_session_not_in_dict_after_end(self):
        svc, _, _ = _make_service()
        await self._open_session(svc)
        await svc.end(SESSION_ID)
        assert SESSION_ID not in svc._sessions

    @pytest.mark.asyncio
    async def test_send_audio_fails_after_end(self):
        svc, _, _ = _make_service()
        await self._open_session(svc)
        await svc.end(SESSION_ID)
        with pytest.raises(VoiceSessionNotFoundError):
            await svc.send_audio(SESSION_ID, PCM_BYTES)

    @pytest.mark.asyncio
    async def test_end_swallows_close_errors(self):
        svc, _, mock_session = _make_service()
        mock_session.close = AsyncMock(side_effect=RuntimeError("close failed"))
        await self._open_session(svc)
        # Should not raise — errors during close are logged, not propagated
        await svc.end(SESSION_ID)
        assert SESSION_ID not in svc._sessions


# ---------------------------------------------------------------------------
# Full lifecycle: start → send_audio → end
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        svc, _, mock_session = _make_service()
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)
            await svc.send_audio(SESSION_ID, PCM_BYTES)
            await svc.end(SESSION_ID)

        mock_session.send_realtime_input.assert_called_once()
        mock_session.close.assert_called_once()
        assert SESSION_ID not in svc._sessions

    @pytest.mark.asyncio
    async def test_multiple_independent_sessions(self):
        svc, mock_client, _ = _make_service()
        sid2 = "session-002"
        with patch("app.services.adk_voice_service.settings") as mock_settings:
            mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
            mock_settings.require_gcp.return_value = "test-project"
            mock_settings.GCP_REGION = "us-central1"
            await svc.start(SESSION_ID, SYSTEM_PROMPT)
            await svc.start(sid2, "Another prompt")

        assert SESSION_ID in svc._sessions
        assert sid2 in svc._sessions
        assert mock_client.aio.live.connect.call_count == 2


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_voice_session_not_found_error_message(self):
        err = VoiceSessionNotFoundError("sess-xyz")
        assert "sess-xyz" in str(err)
        assert err.session_id == "sess-xyz"

    def test_voice_session_error_message(self):
        err = VoiceSessionError("something failed")
        assert "something failed" in str(err)

    def test_voice_session_error_cause(self):
        cause = ConnectionError("timeout")
        err = VoiceSessionError("wrapped", cause=cause)
        assert err.cause is cause

    def test_voice_session_error_default_cause_is_none(self):
        err = VoiceSessionError("no cause")
        assert err.cause is None
