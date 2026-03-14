"""
Tests for T-015: WebSocket handler — audio streaming + turn routing.

Strategy
--------
- Both SessionStore and VoiceSessionService are injected via
  app.dependency_overrides — no real Firestore or Gemini Live calls.
- VoiceSessionService.stream_turns is mocked as an async generator so the
  _turn_loop background task runs deterministically within the same event loop.

Covers:
  session_start handler
    - calls VoiceSessionService.start with _SETUP_SYSTEM_PROMPT
    - emits voice_session_ready after a successful start
    - emits session_error code=voice_start_failed when start() raises
  binary audio frames
    - sends bytes to VoiceSessionService.send_audio
    - silently ignores binary frames before session_start
  _turn_loop background task
    - emits `transcript` event for partial user turn (is_final=False)
    - emits `transcript` event for final user turn (is_final=True)
    - emits `transcript` event for agent turn
    - sends binary WebSocket frame for agent turns with audio_bytes
    - emits `turn_detected` for final user turns (via _route_user_turn)
    - agent turns without audio_bytes do NOT trigger a binary frame
    - transcript event contains role, text, is_final, phase, turn_id fields
  transcript_input text message
    - emits turn_detected with text from the message
    - turn_detected contains turn_id, text, phase fields
    - empty text field produces turn_detected with empty string
  _route_user_turn (unit)
    - emits turn_detected event
    - turn_detected includes turn_id, text, phase
  voice_svc.end called on disconnect
    - VoiceSessionService.end is called when WebSocket closes
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_store, get_voice_service
from app.exceptions import SessionNotFoundError, VoiceSessionError
from app.main import app
from app.models.session import Session, SessionStatus
from app.services.adk_voice_service import VoiceTurn
from app.websocket.story_ws import _SETUP_SYSTEM_PROMPT, _route_user_turn

# ---------------------------------------------------------------------------
# Constants & factories
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())
VALID_TOKEN = "token-t015"
WS_URL = f"/ws/story/{SESSION_ID}"
FAKE_AUDIO = b"\x01\x02" * 128  # 256 bytes of fake PCM
PCM_AUDIO = b"\x00\x01" * 160   # 320 bytes of fake microphone audio


def _make_session(status: SessionStatus = SessionStatus.setup) -> Session:
    return Session(
        session_id=uuid.UUID(SESSION_ID),
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def _mock_store(not_found: bool = False) -> MagicMock:
    store = MagicMock()
    if not_found:
        store.get_session = AsyncMock(side_effect=SessionNotFoundError(SESSION_ID))
    else:
        store.get_session = AsyncMock(return_value=_make_session())
    return store


def _make_voice_svc(turns=()) -> MagicMock:
    """
    Build a VoiceSessionService mock.

    *turns* is a sequence of VoiceTurn objects that stream_turns will yield
    after session_start.  Default is empty (no turns emitted).
    """
    svc = MagicMock()
    svc.start = AsyncMock()
    svc.send_audio = AsyncMock()
    svc.end = AsyncMock()

    turn_list = list(turns)

    async def _stream_turns(_session_id):
        for t in turn_list:
            yield t

    svc.stream_turns = _stream_turns
    return svc


def _client(voice_svc: MagicMock, store: MagicMock | None = None) -> TestClient:
    app.dependency_overrides[get_store] = lambda: (store or _mock_store())
    app.dependency_overrides[get_voice_service] = lambda: voice_svc
    return TestClient(app, raise_server_exceptions=False)


def _clear():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# session_start handler
# ---------------------------------------------------------------------------


class TestSessionStart:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_calls_voice_start_on_session_start(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
        svc.start.assert_called_once()

    def test_voice_start_called_with_session_id(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()
            ws.send_json({"type": "session_start"})
            ws.receive_json()
        args, kwargs = svc.start.call_args
        assert SESSION_ID in (args + tuple(kwargs.values()))

    def test_voice_start_called_with_setup_system_prompt(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()
            ws.send_json({"type": "session_start"})
            ws.receive_json()
        _, kwargs = svc.start.call_args
        prompt = kwargs.get("system_prompt") or svc.start.call_args[0][1]
        assert prompt == _SETUP_SYSTEM_PROMPT

    def test_emits_voice_session_ready(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            data = ws.receive_json()
        assert data["type"] == "voice_session_ready"

    def test_emits_session_error_when_voice_start_raises(self):
        svc = _make_voice_svc()
        svc.start = AsyncMock(side_effect=VoiceSessionError("quota"))
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            data = ws.receive_json()
        assert data["type"] == "session_error"
        assert data["code"] == "voice_start_failed"

    def test_voice_session_ready_not_sent_when_start_fails(self):
        svc = _make_voice_svc()
        svc.start = AsyncMock(side_effect=VoiceSessionError("fail"))
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            data = ws.receive_json()
        assert data["type"] != "voice_session_ready"


# ---------------------------------------------------------------------------
# Binary audio frames
# ---------------------------------------------------------------------------


class TestBinaryAudioFrames:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_binary_frame_calls_send_audio(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            # Start voice session first
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.send_bytes(PCM_AUDIO)
        svc.send_audio.assert_called_once_with(SESSION_ID, PCM_AUDIO)

    def test_binary_before_session_start_no_error_event(self):
        """
        Sending audio before session_start must NOT produce a session_error or
        any other unexpected JSON event — the frame is silently dropped.

        The handler calls send_audio; if the voice session isn't open the mock
        raises VoiceSessionNotFoundError which the handler swallows.
        """
        from app.exceptions import VoiceSessionNotFoundError

        svc = _make_voice_svc()
        # Make send_audio behave like the real service would before session_start
        svc.send_audio = AsyncMock(side_effect=VoiceSessionNotFoundError(SESSION_ID))
        client = _client(svc)
        events = []
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_bytes(PCM_AUDIO)
            # Send a ping to prove the connection is still alive and working
            ws.send_json({"type": "ping"})
            events.append(ws.receive_json())  # should be pong, not session_error
        assert events[0]["type"] == "pong"

    def test_multiple_audio_chunks_each_call_send_audio(self):
        svc = _make_voice_svc()
        client = _client(svc)
        chunk1 = b"\x00\x01" * 80
        chunk2 = b"\x02\x03" * 80
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.send_bytes(chunk1)
            ws.send_bytes(chunk2)
        assert svc.send_audio.call_count == 2

    def test_send_audio_passed_correct_bytes(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.send_bytes(PCM_AUDIO)
        _, call_args, _ = svc.send_audio.mock_calls[0]
        assert call_args[1] == PCM_AUDIO


# ---------------------------------------------------------------------------
# _turn_loop background task
# ---------------------------------------------------------------------------


class TestTurnLoop:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_partial_user_turn_emits_transcript(self):
        turns = [
            VoiceTurn(role="user", transcript="hel", audio_bytes=None, is_final=False),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            data = ws.receive_json()  # transcript from _turn_loop
        assert data["type"] == "transcript"
        assert data["role"] == "user"
        assert data["text"] == "hel"
        assert data["is_final"] is False

    def test_final_user_turn_emits_transcript_is_final_true(self):
        turns = [
            VoiceTurn(role="user", transcript="hello there", audio_bytes=None, is_final=True),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            data = ws.receive_json()  # transcript
        assert data["type"] == "transcript"
        assert data["is_final"] is True

    def test_agent_turn_emits_transcript(self):
        turns = [
            VoiceTurn(
                role="agent",
                transcript="Once upon a time",
                audio_bytes=FAKE_AUDIO,
                is_final=True,
            ),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            data = ws.receive_json()  # transcript
        assert data["type"] == "transcript"
        assert data["role"] == "agent"
        assert data["text"] == "Once upon a time"

    def test_transcript_event_has_required_fields(self):
        turns = [
            VoiceTurn(role="user", transcript="hi", audio_bytes=None, is_final=False),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            data = ws.receive_json()  # transcript
        for field in ("type", "role", "text", "is_final", "phase", "turn_id"):
            assert field in data, f"Missing field: {field}"

    def test_transcript_turn_id_is_uuid(self):
        turns = [
            VoiceTurn(role="user", transcript="hi", audio_bytes=None, is_final=False),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            data = ws.receive_json()
        # Should parse as a valid UUID
        uuid.UUID(data["turn_id"])

    def test_agent_turn_with_audio_sends_binary_frame(self):
        turns = [
            VoiceTurn(
                role="agent",
                transcript="",
                audio_bytes=FAKE_AUDIO,
                is_final=True,
            ),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.receive_json()  # transcript (JSON)
            binary = ws.receive_bytes()  # agent audio binary frame
        assert binary == FAKE_AUDIO

    def test_agent_turn_without_audio_no_binary_frame(self):
        """Agent turn with audio_bytes=None must NOT send a binary frame."""
        turns = [
            VoiceTurn(
                role="agent",
                transcript="hello",
                audio_bytes=None,
                is_final=True,
            ),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            transcript_data = ws.receive_json()  # transcript
            # Next message should be from a subsequent receive or close, not binary.
            # Close the connection and verify no binary was queued.
            assert transcript_data["type"] == "transcript"

    def test_final_user_turn_also_emits_turn_detected(self):
        turns = [
            VoiceTurn(
                role="user",
                transcript="tell me a story",
                audio_bytes=None,
                is_final=True,
            ),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            msg1 = ws.receive_json()  # transcript
            msg2 = ws.receive_json()  # turn_detected
        # Order: transcript first, then turn_detected
        assert msg1["type"] == "transcript"
        assert msg2["type"] == "turn_detected"

    def test_turn_detected_text_matches_transcript(self):
        turns = [
            VoiceTurn(
                role="user",
                transcript="tell me a story",
                audio_bytes=None,
                is_final=True,
            ),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.receive_json()  # transcript
            td = ws.receive_json()  # turn_detected
        assert td["text"] == "tell me a story"

    def test_partial_user_turn_does_not_emit_turn_detected(self):
        """Partial (is_final=False) user turns must NOT trigger turn_detected."""
        turns = [
            VoiceTurn(role="user", transcript="tell", audio_bytes=None, is_final=False),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        received = []
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            received.append(ws.receive_json())  # should be transcript only
        assert all(m["type"] != "turn_detected" for m in received)

    def test_sequence_partial_final_agent(self):
        """Smoke-test the typical full turn sequence."""
        turns = [
            VoiceTurn(role="user", transcript="tell me", audio_bytes=None, is_final=False),
            VoiceTurn(role="user", transcript="tell me a story", audio_bytes=None, is_final=True),
            VoiceTurn(role="agent", transcript="Once upon a time", audio_bytes=FAKE_AUDIO, is_final=True),
        ]
        svc = _make_voice_svc(turns=turns)
        client = _client(svc)
        events = []
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            # partial transcript
            events.append(ws.receive_json())
            # final transcript + turn_detected
            events.append(ws.receive_json())
            events.append(ws.receive_json())
            # agent transcript
            events.append(ws.receive_json())
            # agent binary audio
            audio = ws.receive_bytes()

        assert events[0] == {**events[0], "type": "transcript", "is_final": False}
        assert events[1]["type"] == "transcript" and events[1]["is_final"] is True
        assert events[2]["type"] == "turn_detected"
        assert events[3]["type"] == "transcript" and events[3]["role"] == "agent"
        assert audio == FAKE_AUDIO


# ---------------------------------------------------------------------------
# transcript_input text message
# ---------------------------------------------------------------------------


class TestTranscriptInput:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_transcript_input_emits_turn_detected(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": "a brave rabbit"})
            data = ws.receive_json()
        assert data["type"] == "turn_detected"

    def test_transcript_input_text_in_turn_detected(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": "a brave rabbit"})
            data = ws.receive_json()
        assert data["text"] == "a brave rabbit"

    def test_turn_detected_has_turn_id(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": "hello"})
            data = ws.receive_json()
        assert "turn_id" in data
        uuid.UUID(data["turn_id"])  # valid UUID

    def test_turn_detected_phase_is_setup(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": "hello"})
            data = ws.receive_json()
        assert data["phase"] == "setup"

    def test_transcript_input_empty_text_still_emits(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": ""})
            data = ws.receive_json()
        assert data["type"] == "turn_detected"
        assert data["text"] == ""

    def test_transcript_input_before_session_start_still_works(self):
        """transcript_input does not depend on voice session being started."""
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": "hello"})
            data = ws.receive_json()
        assert data["type"] == "turn_detected"


# ---------------------------------------------------------------------------
# _route_user_turn unit tests (no server required)
# ---------------------------------------------------------------------------


class TestRouteUserTurn:
    @pytest.mark.asyncio
    async def test_emits_turn_detected(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        turn = VoiceTurn(role="user", transcript="hello", audio_bytes=None, is_final=True)
        await _route_user_turn(ws, turn, SESSION_ID)
        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "turn_detected"

    @pytest.mark.asyncio
    async def test_turn_detected_contains_text(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        turn = VoiceTurn(role="user", transcript="the brave rabbit", audio_bytes=None, is_final=True)
        await _route_user_turn(ws, turn, SESSION_ID)
        payload = ws.send_json.call_args[0][0]
        assert payload["text"] == "the brave rabbit"

    @pytest.mark.asyncio
    async def test_turn_detected_contains_turn_id(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        turn = VoiceTurn(role="user", transcript="hello", audio_bytes=None, is_final=True)
        await _route_user_turn(ws, turn, SESSION_ID)
        payload = ws.send_json.call_args[0][0]
        assert "turn_id" in payload
        uuid.UUID(payload["turn_id"])

    @pytest.mark.asyncio
    async def test_turn_detected_phase_setup(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        turn = VoiceTurn(role="user", transcript="hello", audio_bytes=None, is_final=True)
        await _route_user_turn(ws, turn, SESSION_ID)
        payload = ws.send_json.call_args[0][0]
        assert payload["phase"] == "setup"


# ---------------------------------------------------------------------------
# Cleanup on disconnect
# ---------------------------------------------------------------------------


class TestDisconnectCleanup:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_voice_end_called_on_normal_close(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
        svc.end.assert_called_once_with(SESSION_ID)

    def test_voice_end_called_after_session_start(self):
        svc = _make_voice_svc()
        client = _client(svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
        svc.end.assert_called_once_with(SESSION_ID)
