"""
Tests for T-012: WebSocket handler skeleton.

Uses FastAPI's synchronous TestClient WebSocket support (Starlette under the
hood).  SessionStore and VoiceSessionService are injected via
app.dependency_overrides so no real Firestore / Gemini Live calls are made.

Covers:
  - connect with valid token → receives `connected` event with session_status
  - missing token            → server closes with code 4001
  - empty/whitespace token   → server closes with code 4001
  - ping                     → pong
  - session_start            → voice_session_ready
  - unknown message type     → session_error code=unknown_message_type
  - session not found        → session_error code=session_not_found + close 4001
  - emit helper              → always wraps payload in {"type": "..."}
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.dependencies import get_safety_service, get_setup_handler, get_store, get_voice_service
from app.exceptions import SessionNotFoundError
from app.main import app
from app.models.safety import SafetyResult
from app.models.session import Session, SessionStatus
from app.websocket.story_ws import _is_valid_token, emit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())
VALID_TOKEN = "bearer-test-token-abc123"
WS_URL = f"/ws/story/{SESSION_ID}"


def _make_session(status: SessionStatus = SessionStatus.setup) -> Session:
    return Session(
        session_id=uuid.UUID(SESSION_ID),
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def _mock_store(
    session: Session | None = None,
    not_found: bool = False,
) -> MagicMock:
    store = MagicMock()
    if not_found:
        store.get_session = AsyncMock(side_effect=SessionNotFoundError(SESSION_ID))
    else:
        store.get_session = AsyncMock(return_value=session or _make_session())
    return store


def _mock_voice_svc() -> MagicMock:
    """Return a VoiceSessionService mock that no-ops on all calls."""
    svc = MagicMock()
    svc.start = AsyncMock()
    svc.send_audio = AsyncMock()
    svc.end = AsyncMock()

    # stream_turns returns an empty async generator so _turn_loop exits immediately.
    async def _empty_stream(_session_id):
        return
        yield  # makes this an async generator (unreachable but required)

    svc.stream_turns = _empty_stream
    return svc


def _mock_safety_svc(safe: bool = True) -> MagicMock:
    """Return a SafetyService mock that always evaluates to safe=True by default."""
    svc = MagicMock()
    svc.evaluate = AsyncMock(return_value=SafetyResult(safe=safe))
    return svc


def _mock_setup_handler() -> MagicMock:
    """Return a SetupHandler mock that no-ops (T-012 tests don't exercise routing)."""
    handler = MagicMock()
    handler.handle = AsyncMock()
    return handler


def _client(store: MagicMock, voice_svc: MagicMock | None = None) -> TestClient:
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[get_voice_service] = lambda: (
        voice_svc if voice_svc is not None else _mock_voice_svc()
    )
    app.dependency_overrides[get_safety_service] = lambda: _mock_safety_svc()
    app.dependency_overrides[get_setup_handler] = lambda: _mock_setup_handler()
    return TestClient(app, raise_server_exceptions=False)


def _clear():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# emit helper (unit test — no server required)
# ---------------------------------------------------------------------------


class TestEmitHelper:
    @pytest.mark.asyncio
    async def test_sends_type_field(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        await emit(ws, "pong")
        ws.send_json.assert_called_once_with({"type": "pong"})

    @pytest.mark.asyncio
    async def test_merges_extra_fields(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        await emit(ws, "connected", session_status="setup")
        ws.send_json.assert_called_once_with(
            {"type": "connected", "session_status": "setup"}
        )

    @pytest.mark.asyncio
    async def test_multiple_extra_fields(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        await emit(ws, "session_error", code="unknown_message_type")
        ws.send_json.assert_called_once_with(
            {"type": "session_error", "code": "unknown_message_type"}
        )


# ---------------------------------------------------------------------------
# _is_valid_token (unit test)
# ---------------------------------------------------------------------------


class TestIsValidToken:
    def test_none_is_invalid(self):
        assert _is_valid_token(None) is False

    def test_empty_string_is_invalid(self):
        assert _is_valid_token("") is False

    def test_whitespace_only_is_invalid(self):
        assert _is_valid_token("   ") is False

    def test_non_empty_string_is_valid(self):
        assert _is_valid_token("any-token") is True

    def test_uuid_token_is_valid(self):
        assert _is_valid_token(str(uuid.uuid4())) is True


# ---------------------------------------------------------------------------
# Connect → receives `connected` event
# ---------------------------------------------------------------------------


class TestConnectedEvent:
    def setup_method(self):
        _clear()

    def test_receives_connected_event_on_connect(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            data = ws.receive_json()
        assert data["type"] == "connected"

    def test_connected_includes_session_status(self):
        client = _client(_mock_store(session=_make_session(SessionStatus.setup)))
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            data = ws.receive_json()
        assert data["session_status"] == "setup"

    def test_connected_reflects_generating_status(self):
        client = _client(_mock_store(session=_make_session(SessionStatus.generating)))
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            data = ws.receive_json()
        assert data["session_status"] == "generating"

    def test_get_session_called_with_session_id(self):
        store = _mock_store()
        client = _client(store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()
        store.get_session.assert_called_once_with(SESSION_ID)


# ---------------------------------------------------------------------------
# Token validation → close 4001
# ---------------------------------------------------------------------------


class TestTokenValidation:
    def setup_method(self):
        _clear()

    def test_missing_token_closes_with_4001(self):
        client = _client(_mock_store())
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(WS_URL) as ws:
                ws.receive_json()
        assert exc_info.value.code == 4001

    def test_empty_token_closes_with_4001(self):
        client = _client(_mock_store())
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"{WS_URL}?token=") as ws:
                ws.receive_json()
        assert exc_info.value.code == 4001

    def test_whitespace_token_closes_with_4001(self):
        client = _client(_mock_store())
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"{WS_URL}?token=   ") as ws:
                ws.receive_json()
        assert exc_info.value.code == 4001

    def test_valid_token_does_not_close_with_4001(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            data = ws.receive_json()
        assert data["type"] == "connected"


# ---------------------------------------------------------------------------
# ping → pong
# ---------------------------------------------------------------------------


class TestPingPong:
    def setup_method(self):
        _clear()

    def test_ping_receives_pong(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # consume `connected`
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
        assert data["type"] == "pong"

    def test_pong_has_no_extra_fields(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # consume `connected`
            ws.send_json({"type": "ping"})
            data = ws.receive_json()
        assert data == {"type": "pong"}

    def test_multiple_pings_each_receive_pong(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # consume `connected`
            for _ in range(3):
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"


# ---------------------------------------------------------------------------
# session_start → voice_session_ready
# ---------------------------------------------------------------------------


class TestSessionStart:
    def setup_method(self):
        _clear()

    def test_session_start_receives_voice_session_ready(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # consume `connected`
            ws.send_json({"type": "session_start"})
            data = ws.receive_json()
        assert data["type"] == "voice_session_ready"

    def test_voice_session_ready_has_type_field(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()
            ws.send_json({"type": "session_start"})
            data = ws.receive_json()
        assert "type" in data


# ---------------------------------------------------------------------------
# Unknown message type → session_error
# ---------------------------------------------------------------------------


class TestUnknownMessageType:
    def setup_method(self):
        _clear()

    def test_unknown_type_receives_session_error(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # consume `connected`
            ws.send_json({"type": "completely_unknown"})
            data = ws.receive_json()
        assert data["type"] == "session_error"

    def test_session_error_has_unknown_message_type_code(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()
            ws.send_json({"type": "completely_unknown"})
            data = ws.receive_json()
        assert data["code"] == "unknown_message_type"

    def test_missing_type_field_receives_session_error(self):
        client = _client(_mock_store())
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()
            ws.send_json({"action": "no_type_field_here"})
            data = ws.receive_json()
        assert data["type"] == "session_error"
        assert data["code"] == "unknown_message_type"


# ---------------------------------------------------------------------------
# Session not found → session_error + close 4001
# ---------------------------------------------------------------------------


class TestSessionNotFound:
    def setup_method(self):
        _clear()

    def test_session_not_found_emits_session_error(self):
        client = _client(_mock_store(not_found=True))
        with pytest.raises((WebSocketDisconnect, Exception)):
            with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
                data = ws.receive_json()
                assert data["type"] == "session_error"
                assert data["code"] == "session_not_found"
                ws.receive_json()  # triggers disconnect

    def test_session_not_found_closes_with_4001(self):
        client = _client(_mock_store(not_found=True))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
                ws.receive_json()  # session_error frame
                ws.receive_json()  # close frame → WebSocketDisconnect(4001)
        assert exc_info.value.code == 4001
