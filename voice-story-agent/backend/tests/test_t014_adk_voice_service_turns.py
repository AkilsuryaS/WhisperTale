"""
Tests for T-014: VoiceSessionService — stream_turns + speak.

Strategy: inject a mock genai.Client via the constructor so no real Gemini
Live calls are made.  session.receive() is implemented as an async generator
that yields pre-built mock response objects.

Covers:
  VoiceTurn dataclass
    - fields: role, transcript, audio_bytes, is_final
    - role constrained to "user" | "agent"
  stream_turns()
    - yields partial user transcript (is_final=False)
    - yields final user transcript (is_final=True)
    - yields agent turn with audio_bytes
    - agent turn transcript is empty when no text parts
    - agent is_final follows server_content.turn_complete
    - skips responses with no server_content
    - handles both "finished" and "is_final" field name for input_transcription
    - raises VoiceSessionNotFoundError for unknown session_id
    - raises VoiceSessionError on SDK receive failure
    - audio_bytes is None for user turns
    - audio_bytes is non-None for agent turns that contain inline_data
  speak()
    - sends text via session.send_client_content with turn_complete=True
    - resolves once agent returns turn_complete=True in receive stream
    - raises VoiceSessionError on timeout (patched to near-zero)
    - raises VoiceSessionNotFoundError for unknown session_id
    - raises VoiceSessionError when send_client_content raises
    - raises VoiceSessionError when receive loop raises unexpected error
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import VoiceSessionError, VoiceSessionNotFoundError
from app.services.adk_voice_service import VoiceTurn, VoiceSessionService

SESSION_ID = "t014-session-001"
SYSTEM_PROMPT = "You are a warm children's storyteller."
FAKE_AUDIO = b"\x01\x02" * 256  # 512 bytes of fake audio


# ---------------------------------------------------------------------------
# Mock factory helpers
# ---------------------------------------------------------------------------


def _make_input_tx_response(text: str, finished: bool, use_is_final: bool = False):
    """Return a mock server response carrying an input_transcription event."""
    tx = MagicMock()
    tx.text = text
    if use_is_final:
        # Test the fallback attribute name
        del tx.finished  # remove default attribute
        tx.finished = None  # signal it's missing
        tx.is_final = finished
    else:
        tx.finished = finished
    sc = MagicMock()
    sc.input_transcription = tx
    sc.model_turn = None
    sc.turn_complete = False
    resp = MagicMock()
    resp.server_content = sc
    return resp


def _make_agent_response(
    audio: bytes | None = None,
    text: str = "",
    turn_complete: bool = False,
):
    """Return a mock server response carrying a model_turn event."""
    parts = []
    if audio:
        inline = MagicMock()
        inline.data = audio
        part = MagicMock()
        part.inline_data = inline
        part.text = None
        parts.append(part)
    if text:
        part = MagicMock()
        part.inline_data = None
        part.text = text
        parts.append(part)

    model_turn = MagicMock()
    model_turn.parts = parts

    sc = MagicMock()
    sc.input_transcription = None
    sc.model_turn = model_turn
    sc.turn_complete = turn_complete

    resp = MagicMock()
    resp.server_content = sc
    return resp


def _make_turn_complete_response():
    """Return a response with only turn_complete=True (no content)."""
    sc = MagicMock()
    sc.input_transcription = None
    sc.model_turn = None
    sc.turn_complete = True

    resp = MagicMock()
    resp.server_content = sc
    return resp


def _make_no_content_response():
    """Return a response where server_content is None."""
    resp = MagicMock()
    resp.server_content = None
    return resp


async def _async_responses(*responses):
    """Async generator that yields the given responses."""
    for r in responses:
        yield r


def _make_mock_session(receive_responses=()) -> AsyncMock:
    """Return a mock AsyncSession."""
    session = AsyncMock()
    session.send_realtime_input = AsyncMock()
    session.send_client_content = AsyncMock()
    session.close = AsyncMock()
    session.receive = MagicMock(
        return_value=_async_responses(*receive_responses)
    )
    return session


def _make_mock_client(session: AsyncMock):
    @contextlib.asynccontextmanager
    async def _fake_connect(**kwargs):
        yield session

    mock_live = MagicMock()
    mock_live.connect = MagicMock(side_effect=_fake_connect)
    mock_aio = MagicMock()
    mock_aio.live = mock_live
    mock_client = MagicMock()
    mock_client.aio = mock_aio
    return mock_client


async def _open_session(svc: VoiceSessionService, session_id: str = SESSION_ID) -> None:
    with patch("app.services.adk_voice_service.settings") as mock_settings:
        mock_settings.GEMINI_LIVE_MODEL = "gemini-2.0-flash-live-001"
        mock_settings.require_gcp.return_value = "test-project"
        mock_settings.GCP_REGION = "us-central1"
        await svc.start(session_id, SYSTEM_PROMPT)


def _make_service(receive_responses=()):
    session = _make_mock_session(receive_responses=receive_responses)
    client = _make_mock_client(session)
    svc = VoiceSessionService(client=client)
    return svc, session


# ---------------------------------------------------------------------------
# VoiceTurn dataclass
# ---------------------------------------------------------------------------


class TestVoiceTurn:
    def test_user_turn_fields(self):
        turn = VoiceTurn(role="user", transcript="hello", audio_bytes=None, is_final=False)
        assert turn.role == "user"
        assert turn.transcript == "hello"
        assert turn.audio_bytes is None
        assert turn.is_final is False

    def test_agent_turn_fields(self):
        turn = VoiceTurn(role="agent", transcript="Once upon a time", audio_bytes=FAKE_AUDIO, is_final=True)
        assert turn.role == "agent"
        assert turn.transcript == "Once upon a time"
        assert turn.audio_bytes == FAKE_AUDIO
        assert turn.is_final is True

    def test_audio_bytes_none_for_user(self):
        turn = VoiceTurn(role="user", transcript="hi", audio_bytes=None, is_final=True)
        assert turn.audio_bytes is None


# ---------------------------------------------------------------------------
# stream_turns()
# ---------------------------------------------------------------------------


class TestStreamTurns:
    @pytest.mark.asyncio
    async def test_raises_not_found_for_unknown_session(self):
        svc, _ = _make_service()
        with pytest.raises(VoiceSessionNotFoundError) as exc_info:
            async for _ in svc.stream_turns("nonexistent"):
                pass
        assert exc_info.value.session_id == "nonexistent"

    @pytest.mark.asyncio
    async def test_yields_partial_user_transcript(self):
        partial = _make_input_tx_response("hel", finished=False)
        svc, _ = _make_service(receive_responses=[partial])
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert len(turns) == 1
        assert turns[0].role == "user"
        assert turns[0].transcript == "hel"
        assert turns[0].is_final is False
        assert turns[0].audio_bytes is None

    @pytest.mark.asyncio
    async def test_yields_final_user_transcript(self):
        final = _make_input_tx_response("hello there", finished=True)
        svc, _ = _make_service(receive_responses=[final])
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert len(turns) == 1
        assert turns[0].is_final is True
        assert turns[0].transcript == "hello there"

    @pytest.mark.asyncio
    async def test_yields_partial_then_final_user_transcripts(self):
        responses = [
            _make_input_tx_response("hel", finished=False),
            _make_input_tx_response("hello", finished=True),
        ]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert len(turns) == 2
        assert turns[0].is_final is False
        assert turns[1].is_final is True

    @pytest.mark.asyncio
    async def test_yields_agent_turn_with_audio(self):
        responses = [_make_agent_response(audio=FAKE_AUDIO, turn_complete=True)]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert len(turns) == 1
        assert turns[0].role == "agent"
        assert turns[0].audio_bytes == FAKE_AUDIO
        assert turns[0].is_final is True

    @pytest.mark.asyncio
    async def test_agent_audio_bytes_non_none(self):
        responses = [_make_agent_response(audio=FAKE_AUDIO)]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert turns[0].audio_bytes is not None

    @pytest.mark.asyncio
    async def test_agent_turn_transcript_from_text_parts(self):
        responses = [_make_agent_response(text="Once upon a time", audio=FAKE_AUDIO)]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert turns[0].transcript == "Once upon a time"

    @pytest.mark.asyncio
    async def test_agent_turn_empty_transcript_when_no_text(self):
        responses = [_make_agent_response(audio=FAKE_AUDIO)]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert turns[0].transcript == ""

    @pytest.mark.asyncio
    async def test_agent_is_final_follows_turn_complete(self):
        responses = [
            _make_agent_response(audio=FAKE_AUDIO, turn_complete=False),
            _make_agent_response(audio=FAKE_AUDIO, turn_complete=True),
        ]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert turns[0].is_final is False
        assert turns[1].is_final is True

    @pytest.mark.asyncio
    async def test_skips_responses_without_server_content(self):
        responses = [
            _make_no_content_response(),
            _make_input_tx_response("hello", finished=True),
        ]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        # Only the second response should yield a turn
        assert len(turns) == 1
        assert turns[0].transcript == "hello"

    @pytest.mark.asyncio
    async def test_handles_is_final_field_name_fallback(self):
        """SDK may use 'is_final' instead of 'finished' — both should work."""
        partial = _make_input_tx_response("test", finished=False, use_is_final=True)
        svc, _ = _make_service(receive_responses=[partial])
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert turns[0].is_final is False

    @pytest.mark.asyncio
    async def test_full_sequence_partial_final_agent(self):
        responses = [
            _make_input_tx_response("tell me", finished=False),
            _make_input_tx_response("tell me a story", finished=True),
            _make_agent_response(audio=FAKE_AUDIO, text="Once upon a time", turn_complete=True),
        ]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert len(turns) == 3

        user_partial, user_final, agent = turns
        assert user_partial.role == "user" and user_partial.is_final is False
        assert user_final.role == "user" and user_final.is_final is True
        assert agent.role == "agent"
        assert agent.audio_bytes == FAKE_AUDIO
        assert agent.is_final is True

    @pytest.mark.asyncio
    async def test_raises_voice_session_error_on_receive_failure(self):
        async def _bad_receive():
            raise RuntimeError("stream broken")
            yield  # make it a generator

        session = _make_mock_session()
        session.receive = MagicMock(return_value=_bad_receive())
        client = _make_mock_client(session)
        svc = VoiceSessionService(client=client)
        await _open_session(svc)

        with pytest.raises(VoiceSessionError) as exc_info:
            async for _ in svc.stream_turns(SESSION_ID):
                pass
        assert "stream_turns failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_user_audio_bytes_always_none(self):
        responses = [_make_input_tx_response("hi", finished=True)]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        turns = [t async for t in svc.stream_turns(SESSION_ID)]
        assert turns[0].audio_bytes is None


# ---------------------------------------------------------------------------
# speak()
# ---------------------------------------------------------------------------


class TestSpeak:
    @pytest.mark.asyncio
    async def test_raises_not_found_for_unknown_session(self):
        svc, _ = _make_service()
        with pytest.raises(VoiceSessionNotFoundError):
            await svc.speak("nonexistent", "Hello!")

    @pytest.mark.asyncio
    async def test_calls_send_client_content_with_text(self):
        svc, session = _make_service(receive_responses=[_make_turn_complete_response()])
        await _open_session(svc)

        await svc.speak(SESSION_ID, "Tell me a story.")
        session.send_client_content.assert_called_once()
        call_kwargs = session.send_client_content.call_args.kwargs
        turns = call_kwargs.get("turns", [])
        assert len(turns) == 1
        assert turns[0].parts[0].text == "Tell me a story."

    @pytest.mark.asyncio
    async def test_calls_send_client_content_with_turn_complete_true(self):
        svc, session = _make_service(receive_responses=[_make_turn_complete_response()])
        await _open_session(svc)

        await svc.speak(SESSION_ID, "Hello.")
        call_kwargs = session.send_client_content.call_args.kwargs
        assert call_kwargs.get("turn_complete") is True

    @pytest.mark.asyncio
    async def test_resolves_once_turn_complete_received(self):
        responses = [
            _make_agent_response(audio=FAKE_AUDIO),
            _make_turn_complete_response(),
        ]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        # Should complete without raising
        await svc.speak(SESSION_ID, "Please continue the story.")

    @pytest.mark.asyncio
    async def test_raises_voice_session_error_on_send_failure(self):
        session = _make_mock_session(receive_responses=[])
        session.send_client_content = AsyncMock(side_effect=RuntimeError("quota"))
        client = _make_mock_client(session)
        svc = VoiceSessionService(client=client)
        await _open_session(svc)

        with pytest.raises(VoiceSessionError) as exc_info:
            await svc.speak(SESSION_ID, "Hello.")
        assert "speak failed to send text" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_raises_voice_session_error_on_timeout(self):
        """speak() raises VoiceSessionError if no turn_complete within timeout."""
        # receive() never yields turn_complete — simulate an infinite hang via
        # an async generator that just sleeps forever.
        async def _hang_receive():
            await asyncio.sleep(999)
            yield  # unreachable, but makes it an async generator

        session = _make_mock_session()
        session.send_client_content = AsyncMock()
        session.receive = MagicMock(return_value=_hang_receive())
        client = _make_mock_client(session)
        svc = VoiceSessionService(client=client)
        await _open_session(svc)

        # Patch the timeout constant to 0.05 s so the test doesn't actually wait 10 s
        with patch("app.services.adk_voice_service._SPEAK_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(VoiceSessionError) as exc_info:
                await svc.speak(SESSION_ID, "Hello.")

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_timeout_error_wrapped_as_voice_session_error(self):
        async def _hang_receive():
            await asyncio.sleep(999)
            yield

        session = _make_mock_session()
        session.send_client_content = AsyncMock()
        session.receive = MagicMock(return_value=_hang_receive())
        client = _make_mock_client(session)
        svc = VoiceSessionService(client=client)
        await _open_session(svc)

        with patch("app.services.adk_voice_service._SPEAK_TIMEOUT_SECONDS", 0.05):
            with pytest.raises(VoiceSessionError) as exc_info:
                await svc.speak(SESSION_ID, "Hello.")

        assert isinstance(exc_info.value.cause, asyncio.TimeoutError)

    @pytest.mark.asyncio
    async def test_skips_no_content_responses_before_turn_complete(self):
        """speak() must ignore responses without server_content while waiting."""
        responses = [
            _make_no_content_response(),
            _make_turn_complete_response(),
        ]
        svc, _ = _make_service(receive_responses=responses)
        await _open_session(svc)

        # Should complete without raising
        await svc.speak(SESSION_ID, "Continue.")

    @pytest.mark.asyncio
    async def test_speak_send_error_has_cause(self):
        cause = RuntimeError("network error")
        session = _make_mock_session(receive_responses=[])
        session.send_client_content = AsyncMock(side_effect=cause)
        client = _make_mock_client(session)
        svc = VoiceSessionService(client=client)
        await _open_session(svc)

        with pytest.raises(VoiceSessionError) as exc_info:
            await svc.speak(SESSION_ID, "Hello.")
        assert exc_info.value.cause is cause
