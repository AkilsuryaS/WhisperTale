"""
Tests for T-017: WebSocket safety gate integration.

Strategy
--------
All external dependencies (SessionStore, VoiceSessionService, SafetyService)
are injected via app.dependency_overrides — no real Firestore, Gemini Live,
or Gemini Flash calls are made.

VoiceSessionService.stream_turns is mocked as an async generator; SafetyService
.evaluate is mocked to return controlled SafetyResult objects.

Covers:
  transcript_input path — safe turn:
    T17-01  safe text → SafetyService.evaluate called once
    T17-02  safe text → turn_detected emitted (no safety_rewrite)

  transcript_input path — unsafe turn:
    T17-03  unsafe text → safety_rewrite emitted before turn_detected
    T17-04  unsafe text → safety_rewrite contains decision_id, turn_id,
            detected_category, proposed_rewrite, phase
    T17-05  unsafe text → voice_svc.speak called with proposed_rewrite
    T17-06  unsafe text → follow-up transcript_input → safety_accepted emitted
    T17-07  unsafe text → follow-up → store.save_safety_decision called
    T17-08  unsafe text → follow-up → ContentPolicy update called
    T17-09  safety_accepted carries decision_id and final_premise

  fail-safe path:
    T17-10  classifier exception → safety_rewrite with SAFE_FALLBACK_REWRITE

  _turn_loop path — ADK stream:
    T17-11  safe final user turn in stream → turn_detected (no safety_rewrite)
    T17-12  unsafe final user turn in stream → safety_rewrite emitted
    T17-13  unsafe stream turn + ack transcript_input → safety_accepted

  disconnect while awaiting ack:
    T17-14  save_safety_decision(user_accepted=False) called on disconnect
    T17-15  update_session_status(error) called on disconnect

  unit tests for helpers:
    T17-16  _begin_safety_rewrite: arms gate, emits safety_rewrite, calls speak
    T17-17  _complete_safety_ack: emits safety_accepted, calls save_safety_decision
    T17-18  _persist_abandoned_safety_decision: no-op when gate not armed
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_safety_service, get_setup_handler, get_store, get_voice_service
from app.exceptions import SessionNotFoundError
from app.main import app
from app.models.safety import (
    SAFE_FALLBACK_REWRITE,
    SafetyCategory,
    SafetyResult,
)
from app.models.session import Session, SessionStatus
from app.services.adk_voice_service import VoiceTurn
from app.websocket.story_ws import (
    _SafetyGate,
    _begin_safety_rewrite,
    _complete_safety_ack,
    _persist_abandoned_safety_decision,
)

# ---------------------------------------------------------------------------
# Constants & factories
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())
VALID_TOKEN = "token-t017"
WS_URL = f"/ws/story/{SESSION_ID}"

UNSAFE_TEXT = "the dragon kills everyone in the village"
SAFE_TEXT = "the bunny feels very sad today"
UNSAFE_REWRITE = "How about a story where a brave bunny helps a lost friend find their way home?"
UNSAFE_CATEGORY = SafetyCategory.character_death


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
    store.save_safety_decision = AsyncMock()
    store.get_character_bible = AsyncMock(return_value=None)
    store.update_character_bible_field = AsyncMock()
    store.update_session_status = AsyncMock()
    return store


def _make_voice_svc(turns=()) -> MagicMock:
    svc = MagicMock()
    svc.start = AsyncMock()
    svc.send_audio = AsyncMock()
    svc.end = AsyncMock()
    svc.speak = AsyncMock()

    turn_list = list(turns)

    async def _stream_turns(_session_id):
        for t in turn_list:
            yield t

    svc.stream_turns = _stream_turns
    return svc


def _make_safety_svc(
    safe: bool = True,
    category: SafetyCategory | None = None,
    rewrite: str | None = None,
    raises: Exception | None = None,
) -> MagicMock:
    svc = MagicMock()
    if raises is not None:
        svc.evaluate = AsyncMock(side_effect=raises)
    else:
        svc.evaluate = AsyncMock(
            return_value=SafetyResult(safe=safe, category=category, rewrite=rewrite)
        )
    return svc


def _unsafe_safety_svc(
    category: SafetyCategory = UNSAFE_CATEGORY,
    rewrite: str = UNSAFE_REWRITE,
) -> MagicMock:
    return _make_safety_svc(safe=False, category=category, rewrite=rewrite)


def _safe_safety_svc() -> MagicMock:
    return _make_safety_svc(safe=True)


def _mock_setup_handler() -> MagicMock:
    """
    Return a SetupHandler mock that emits `turn_detected` (T-015 stub behaviour).

    Lets T-017 tests check safe-turn routing without depending on the real
    Gemini extraction call introduced in T-020.
    """
    handler = MagicMock()

    async def _handle(ws, turn, session_id, voice_svc, setup_state, store):
        import uuid as _uuid

        await ws.send_json(
            {
                "type": "turn_detected",
                "turn_id": str(_uuid.uuid4()),
                "text": turn.transcript,
                "phase": "setup",
            }
        )

    handler.handle = _handle
    return handler


def _client(
    voice_svc: MagicMock,
    safety_svc: MagicMock,
    store: MagicMock | None = None,
) -> TestClient:
    app.dependency_overrides[get_store] = lambda: (store or _mock_store())
    app.dependency_overrides[get_voice_service] = lambda: voice_svc
    app.dependency_overrides[get_safety_service] = lambda: safety_svc
    app.dependency_overrides[get_setup_handler] = lambda: _mock_setup_handler()
    return TestClient(app, raise_server_exceptions=False)


def _clear():
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# T17-01 / T17-02  transcript_input — safe turn
# ---------------------------------------------------------------------------


class TestTranscriptInputSafe:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_evaluate_called_on_transcript_input(self):
        """SafetyService.evaluate is called once for each transcript_input."""
        safety_svc = _safe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": SAFE_TEXT})
            ws.receive_json()  # turn_detected
        safety_svc.evaluate.assert_called_once()

    def test_safe_turn_emits_turn_detected_not_safety_rewrite(self):
        """A safe utterance must emit turn_detected and no safety_rewrite."""
        safety_svc = _safe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": SAFE_TEXT})
            data = ws.receive_json()
        assert data["type"] == "turn_detected"


# ---------------------------------------------------------------------------
# T17-03 / T17-04 / T17-05  transcript_input — unsafe turn
# ---------------------------------------------------------------------------


class TestTranscriptInputUnsafe:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_unsafe_turn_emits_safety_rewrite(self):
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["type"] == "safety_rewrite"

    def test_safety_rewrite_has_required_fields(self):
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        for field in ("decision_id", "turn_id", "detected_category", "proposed_rewrite", "phase"):
            assert field in data, f"Missing field: {field}"

    def test_safety_rewrite_contains_proposed_rewrite(self):
        safety_svc = _unsafe_safety_svc(rewrite=UNSAFE_REWRITE)
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["proposed_rewrite"] == UNSAFE_REWRITE

    def test_safety_rewrite_contains_category(self):
        safety_svc = _unsafe_safety_svc(category=SafetyCategory.character_death)
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["detected_category"] == "character_death"

    def test_safety_rewrite_phase_is_setup(self):
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["phase"] == "setup"

    def test_unsafe_turn_no_turn_detected_emitted(self):
        """safety_rewrite must be emitted; turn_detected must NOT be emitted."""
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        events = []
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            events.append(ws.receive_json())
        assert all(e["type"] != "turn_detected" for e in events)

    def test_voice_speak_called_with_proposed_rewrite(self):
        """VoiceSessionService.speak must be called with the proposed_rewrite."""
        voice_svc = _make_voice_svc()
        safety_svc = _unsafe_safety_svc(rewrite=UNSAFE_REWRITE)
        store = _mock_store()
        client = _client(voice_svc, safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
        voice_svc.speak.assert_called_once_with(SESSION_ID, UNSAFE_REWRITE)


# ---------------------------------------------------------------------------
# T17-06 / T17-07 / T17-08 / T17-09  safety acknowledgement flow
# ---------------------------------------------------------------------------


class TestSafetyAck:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_follow_up_transcript_input_emits_safety_accepted(self):
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
            ws.send_json({"type": "transcript_input", "text": "ok, sounds good"})
            data = ws.receive_json()
        assert data["type"] == "safety_accepted"

    def test_safety_accepted_has_decision_id(self):
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            rewrite_event = ws.receive_json()
            ws.send_json({"type": "transcript_input", "text": "ok"})
            ack_event = ws.receive_json()
        assert ack_event["decision_id"] == rewrite_event["decision_id"]

    def test_safety_accepted_has_final_premise(self):
        safety_svc = _unsafe_safety_svc(rewrite=UNSAFE_REWRITE)
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
            ws.send_json({"type": "transcript_input", "text": "yes"})
            ack_event = ws.receive_json()
        assert ack_event["final_premise"] == UNSAFE_REWRITE

    def test_save_safety_decision_called_on_ack(self):
        store = _mock_store()
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
            ws.send_json({"type": "transcript_input", "text": "ok"})
            ws.receive_json()  # safety_accepted
        store.save_safety_decision.assert_called_once()

    def test_save_safety_decision_user_accepted_true(self):
        store = _mock_store()
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
            ws.send_json({"type": "transcript_input", "text": "sure"})
            ws.receive_json()  # safety_accepted
        _args, _kwargs = store.save_safety_decision.call_args
        decision = _args[1] if len(_args) > 1 else _kwargs.get("decision")
        assert decision.user_accepted is True

    def test_update_character_bible_field_not_called_when_no_bible(self):
        """If no CharacterBible exists, ContentPolicy update is skipped."""
        store = _mock_store()
        store.get_character_bible = AsyncMock(return_value=None)
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
            ws.send_json({"type": "transcript_input", "text": "ok"})
            ws.receive_json()  # safety_accepted
        store.update_character_bible_field.assert_not_called()

    def test_second_unsafe_turn_after_ack_triggers_new_safety_rewrite(self):
        """After an ack is processed, a new unsafe turn restarts the safety gate."""
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            # First unsafe turn
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite #1
            # Ack
            ws.send_json({"type": "transcript_input", "text": "ok"})
            ws.receive_json()  # safety_accepted
            # Second unsafe turn
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["type"] == "safety_rewrite"


# ---------------------------------------------------------------------------
# T17-10  fail-safe path
# ---------------------------------------------------------------------------


class TestFailSafe:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_classifier_failsafe_emits_safety_rewrite(self):
        """
        When SafetyService returns safe=False with no rewrite (fail-safe),
        safety_rewrite is emitted with SAFE_FALLBACK_REWRITE.
        """
        safety_svc = _make_safety_svc(safe=False, category=None, rewrite=None)
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["type"] == "safety_rewrite"
        assert data["proposed_rewrite"] == SAFE_FALLBACK_REWRITE

    def test_classifier_failsafe_category_is_none(self):
        safety_svc = _make_safety_svc(safe=False, category=None, rewrite=None)
        client = _client(_make_voice_svc(), safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            data = ws.receive_json()
        assert data["detected_category"] is None


# ---------------------------------------------------------------------------
# T17-11 / T17-12 / T17-13  _turn_loop ADK stream path
# ---------------------------------------------------------------------------


class TestTurnLoopSafetyGate:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_safe_adk_turn_emits_turn_detected(self):
        """A safe final user turn from the ADK stream routes to turn_detected."""
        turns = [
            VoiceTurn(role="user", transcript=SAFE_TEXT, audio_bytes=None, is_final=True),
        ]
        voice_svc = _make_voice_svc(turns=turns)
        safety_svc = _safe_safety_svc()
        client = _client(voice_svc, safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.receive_json()  # transcript
            data = ws.receive_json()  # turn_detected
        assert data["type"] == "turn_detected"

    def test_unsafe_adk_turn_emits_safety_rewrite(self):
        """An unsafe final user turn from the ADK stream triggers safety_rewrite."""
        turns = [
            VoiceTurn(role="user", transcript=UNSAFE_TEXT, audio_bytes=None, is_final=True),
        ]
        voice_svc = _make_voice_svc(turns=turns)
        safety_svc = _unsafe_safety_svc(rewrite=UNSAFE_REWRITE)
        client = _client(voice_svc, safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.receive_json()  # transcript
            data = ws.receive_json()  # safety_rewrite
        assert data["type"] == "safety_rewrite"
        assert data["proposed_rewrite"] == UNSAFE_REWRITE

    def test_unsafe_adk_turn_ack_via_transcript_input(self):
        """
        Unsafe ADK turn → safety gate armed → transcript_input ack →
        safety_accepted emitted.
        """
        turns = [
            VoiceTurn(role="user", transcript=UNSAFE_TEXT, audio_bytes=None, is_final=True),
        ]
        voice_svc = _make_voice_svc(turns=turns)
        safety_svc = _unsafe_safety_svc(rewrite=UNSAFE_REWRITE)
        client = _client(voice_svc, safety_svc)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "session_start"})
            ws.receive_json()  # voice_session_ready
            ws.receive_json()  # transcript
            ws.receive_json()  # safety_rewrite
            # Now ack via transcript_input
            ws.send_json({"type": "transcript_input", "text": "ok"})
            data = ws.receive_json()
        assert data["type"] == "safety_accepted"


# ---------------------------------------------------------------------------
# T17-14 / T17-15  disconnect while awaiting ack
# ---------------------------------------------------------------------------


class TestDisconnectWithPendingGate:
    def setup_method(self):
        _clear()

    def teardown_method(self):
        _clear()

    def test_disconnect_mid_gate_calls_save_safety_decision(self):
        """On disconnect while gate is open, save_safety_decision is called."""
        store = _mock_store()
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
            # Disconnect without sending ack
        store.save_safety_decision.assert_called_once()

    def test_disconnect_mid_gate_user_accepted_false(self):
        store = _mock_store()
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
        _args, _kwargs = store.save_safety_decision.call_args
        decision = _args[1] if len(_args) > 1 else _kwargs.get("decision")
        assert decision.user_accepted is False

    def test_disconnect_mid_gate_updates_session_status_to_error(self):
        store = _mock_store()
        safety_svc = _unsafe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": UNSAFE_TEXT})
            ws.receive_json()  # safety_rewrite
        store.update_session_status.assert_called_once_with(SESSION_ID, SessionStatus.error)

    def test_no_safety_decision_on_clean_disconnect(self):
        """If no safety gate was triggered, save_safety_decision is not called."""
        store = _mock_store()
        safety_svc = _safe_safety_svc()
        client = _client(_make_voice_svc(), safety_svc, store)
        with client.websocket_connect(f"{WS_URL}?token={VALID_TOKEN}") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "transcript_input", "text": SAFE_TEXT})
            ws.receive_json()  # turn_detected
        store.save_safety_decision.assert_not_called()


# ---------------------------------------------------------------------------
# T17-16 / T17-17 / T17-18  unit tests for helper coroutines
# ---------------------------------------------------------------------------


class TestSafetyGateHelpers:
    @pytest.mark.asyncio
    async def test_begin_safety_rewrite_arms_gate(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        voice_svc = MagicMock()
        voice_svc.speak = AsyncMock()
        gate = _SafetyGate()
        turn = VoiceTurn(role="user", transcript=UNSAFE_TEXT, audio_bytes=None, is_final=True)
        turn_id = str(uuid.uuid4())

        await _begin_safety_rewrite(
            ws, turn, turn_id, SESSION_ID, voice_svc, gate, UNSAFE_REWRITE, UNSAFE_CATEGORY
        )

        assert gate.awaiting_ack is True
        assert gate.proposed_rewrite == UNSAFE_REWRITE
        assert gate.category == UNSAFE_CATEGORY
        assert gate.raw_input == UNSAFE_TEXT

    @pytest.mark.asyncio
    async def test_begin_safety_rewrite_emits_safety_rewrite_event(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        voice_svc = MagicMock()
        voice_svc.speak = AsyncMock()
        gate = _SafetyGate()
        turn = VoiceTurn(role="user", transcript=UNSAFE_TEXT, audio_bytes=None, is_final=True)
        turn_id = str(uuid.uuid4())

        await _begin_safety_rewrite(
            ws, turn, turn_id, SESSION_ID, voice_svc, gate, UNSAFE_REWRITE, UNSAFE_CATEGORY
        )

        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "safety_rewrite"
        assert payload["proposed_rewrite"] == UNSAFE_REWRITE

    @pytest.mark.asyncio
    async def test_begin_safety_rewrite_calls_speak(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        voice_svc = MagicMock()
        voice_svc.speak = AsyncMock()
        gate = _SafetyGate()
        turn = VoiceTurn(role="user", transcript=UNSAFE_TEXT, audio_bytes=None, is_final=True)
        turn_id = str(uuid.uuid4())

        await _begin_safety_rewrite(
            ws, turn, turn_id, SESSION_ID, voice_svc, gate, UNSAFE_REWRITE, UNSAFE_CATEGORY
        )

        voice_svc.speak.assert_called_once_with(SESSION_ID, UNSAFE_REWRITE)

    @pytest.mark.asyncio
    async def test_complete_safety_ack_disarms_gate(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        store = MagicMock()
        store.save_safety_decision = AsyncMock()
        store.get_character_bible = AsyncMock(return_value=None)
        gate = _SafetyGate()
        gate.awaiting_ack = True
        gate.decision_id = uuid.uuid4()
        gate.turn_uuid = uuid.uuid4()
        gate.raw_input = UNSAFE_TEXT
        gate.category = UNSAFE_CATEGORY
        gate.proposed_rewrite = UNSAFE_REWRITE
        gate.triggered_at = NOW

        await _complete_safety_ack(ws, SESSION_ID, store, gate)

        assert gate.awaiting_ack is False

    @pytest.mark.asyncio
    async def test_complete_safety_ack_emits_safety_accepted(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        store = MagicMock()
        store.save_safety_decision = AsyncMock()
        store.get_character_bible = AsyncMock(return_value=None)
        gate = _SafetyGate()
        gate.awaiting_ack = True
        gate.decision_id = uuid.uuid4()
        gate.turn_uuid = uuid.uuid4()
        gate.raw_input = UNSAFE_TEXT
        gate.category = UNSAFE_CATEGORY
        gate.proposed_rewrite = UNSAFE_REWRITE
        gate.triggered_at = NOW

        await _complete_safety_ack(ws, SESSION_ID, store, gate)

        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "safety_accepted"
        assert payload["final_premise"] == UNSAFE_REWRITE

    @pytest.mark.asyncio
    async def test_complete_safety_ack_calls_save_safety_decision(self):
        ws = MagicMock()
        ws.send_json = AsyncMock()
        store = MagicMock()
        store.save_safety_decision = AsyncMock()
        store.get_character_bible = AsyncMock(return_value=None)
        gate = _SafetyGate()
        gate.awaiting_ack = True
        gate.decision_id = uuid.uuid4()
        gate.turn_uuid = uuid.uuid4()
        gate.raw_input = UNSAFE_TEXT
        gate.category = UNSAFE_CATEGORY
        gate.proposed_rewrite = UNSAFE_REWRITE
        gate.triggered_at = NOW

        await _complete_safety_ack(ws, SESSION_ID, store, gate)

        store.save_safety_decision.assert_called_once()
        _args, _kwargs = store.save_safety_decision.call_args
        decision = _args[1] if len(_args) > 1 else _kwargs.get("decision")
        assert decision.user_accepted is True

    @pytest.mark.asyncio
    async def test_persist_abandoned_is_noop_when_gate_not_armed(self):
        store = MagicMock()
        store.save_safety_decision = AsyncMock()
        store.update_session_status = AsyncMock()
        gate = _SafetyGate()
        gate.awaiting_ack = False

        await _persist_abandoned_safety_decision(SESSION_ID, store, gate)

        store.save_safety_decision.assert_not_called()
        store.update_session_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_abandoned_saves_user_accepted_false(self):
        store = MagicMock()
        store.save_safety_decision = AsyncMock()
        store.update_session_status = AsyncMock()
        gate = _SafetyGate()
        gate.awaiting_ack = True
        gate.decision_id = uuid.uuid4()
        gate.turn_uuid = uuid.uuid4()
        gate.raw_input = UNSAFE_TEXT
        gate.category = UNSAFE_CATEGORY
        gate.proposed_rewrite = UNSAFE_REWRITE
        gate.triggered_at = NOW

        await _persist_abandoned_safety_decision(SESSION_ID, store, gate)

        store.save_safety_decision.assert_called_once()
        _args, _kwargs = store.save_safety_decision.call_args
        decision = _args[1] if len(_args) > 1 else _kwargs.get("decision")
        assert decision.user_accepted is False
