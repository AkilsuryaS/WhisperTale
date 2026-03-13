"""
Tests for T-007: SessionStore — Session, StoryBrief, UserTurn CRUD.

Strategy: inject a mock AsyncClient so no real Firestore / GCP credentials
are needed. Each test builds the minimal mock chain for the method under test.

Firestore paths covered:
    sessions/{id}                    ← Session
    sessions/{id}/story_brief/main   ← StoryBrief
    sessions/{id}/turns/{turn_id}    ← UserTurn

Covers:
- create_session: calls .set() with serialised model data
- get_session: deserialises returned doc; raises SessionNotFoundError when missing
- update_session_status: calls .update() with status + updated_at; raises on missing
- update_story_arc: calls .update() with new arc + updated_at; raises on missing
- save_story_brief: calls .set() on story_brief/main
- get_story_brief: returns None when doc missing; returns model when present
- save_user_turn: calls .set() on turns/{turn_id}
- list_user_turns: returns turns ordered by sequence; returns [] when empty
- SessionNotFoundError: carries the session_id; readable message
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions import SessionNotFoundError
from app.models.session import Session, SessionStatus, StoryBrief, UserTurn
from app.services.session_store import SessionStore

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())
TURN_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session(**overrides) -> Session:
    defaults = dict(
        session_id=uuid.UUID(SESSION_ID),
        status=SessionStatus.setup,
        created_at=NOW,
        updated_at=NOW,
        page_count=5,
        current_page=0,
        story_arc=[],
    )
    defaults.update(overrides)
    return Session(**defaults)


def _make_story_brief() -> StoryBrief:
    return StoryBrief(
        protagonist_name="Spark",
        protagonist_description="A small purple dragon",
        setting="A mushroom forest",
        tone="adventurous",
        raw_setup_transcript="I want a purple dragon",
        confirmed_at=NOW,
        confirmed_by_agent=True,
    )


def _make_user_turn(sequence: int = 1) -> UserTurn:
    return UserTurn(
        turn_id=uuid.UUID(TURN_ID),
        sequence=sequence,
        phase="setup",
        speaker="user",
        raw_transcript="I want a purple dragon",
        caption_text="I want a purple dragon",
        timestamp=NOW,
    )


def _mock_doc(exists: bool, data: dict | None = None) -> AsyncMock:
    """Build a mock Firestore DocumentSnapshot."""
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict = MagicMock(return_value=data or {})
    return doc


def _make_store() -> tuple[SessionStore, MagicMock]:
    """Return (store, mock_client)."""
    client = MagicMock()
    store = SessionStore(client=client)
    return store, client


def _wire_doc_ref(client: MagicMock, doc_mock: AsyncMock) -> MagicMock:
    """Wire client.collection().document() chain to return a ref whose .get() yields doc_mock."""
    doc_ref = MagicMock()
    doc_ref.get = AsyncMock(return_value=doc_mock)
    doc_ref.set = AsyncMock()
    doc_ref.update = AsyncMock()
    col = MagicMock()
    col.document = MagicMock(return_value=doc_ref)
    client.collection = MagicMock(return_value=col)
    return doc_ref


# ---------------------------------------------------------------------------
# SessionNotFoundError
# ---------------------------------------------------------------------------


class TestSessionNotFoundError:
    def test_carries_session_id(self):
        err = SessionNotFoundError("abc-123")
        assert err.session_id == "abc-123"

    def test_message_contains_session_id(self):
        err = SessionNotFoundError("abc-123")
        assert "abc-123" in str(err)

    def test_is_exception_subclass(self):
        assert issubclass(SessionNotFoundError, Exception)


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_calls_set_with_serialised_data(self):
        session = _make_session()
        store, client = _make_store()
        doc_ref = _wire_doc_ref(client, _mock_doc(exists=True))

        await store.create_session(session)

        doc_ref.set.assert_called_once()
        payload = doc_ref.set.call_args[0][0]
        assert payload["session_id"] == SESSION_ID
        assert payload["status"] == "setup"
        assert payload["page_count"] == 5

    @pytest.mark.asyncio
    async def test_session_id_serialised_as_string(self):
        session = _make_session()
        store, client = _make_store()
        doc_ref = _wire_doc_ref(client, _mock_doc(exists=True))

        await store.create_session(session)

        payload = doc_ref.set.call_args[0][0]
        assert isinstance(payload["session_id"], str)


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    @pytest.mark.asyncio
    async def test_returns_session_when_doc_exists(self):
        session = _make_session()
        data = session.model_dump(mode="json")
        store, client = _make_store()
        _wire_doc_ref(client, _mock_doc(exists=True, data=data))

        result = await store.get_session(SESSION_ID)

        assert str(result.session_id) == SESSION_ID
        assert result.status == "setup"

    @pytest.mark.asyncio
    async def test_raises_session_not_found_when_missing(self):
        store, client = _make_store()
        _wire_doc_ref(client, _mock_doc(exists=False))

        with pytest.raises(SessionNotFoundError) as exc_info:
            await store.get_session(SESSION_ID)

        assert exc_info.value.session_id == SESSION_ID

    @pytest.mark.asyncio
    async def test_deserialises_all_fields(self):
        session = _make_session(
            story_arc=["beat1", "beat2", "beat3", "beat4", "beat5"],
            current_page=2,
            error_message=None,
        )
        data = session.model_dump(mode="json")
        store, client = _make_store()
        _wire_doc_ref(client, _mock_doc(exists=True, data=data))

        result = await store.get_session(SESSION_ID)

        assert result.story_arc == ["beat1", "beat2", "beat3", "beat4", "beat5"]
        assert result.current_page == 2


# ---------------------------------------------------------------------------
# update_session_status
# ---------------------------------------------------------------------------


class TestUpdateSessionStatus:
    @pytest.mark.asyncio
    async def test_calls_update_with_status_and_updated_at(self):
        session = _make_session()
        data = session.model_dump(mode="json")
        store, client = _make_store()
        doc_ref = _wire_doc_ref(client, _mock_doc(exists=True, data=data))

        await store.update_session_status(SESSION_ID, SessionStatus.generating)

        doc_ref.update.assert_called_once()
        update_payload = doc_ref.update.call_args[0][0]
        assert update_payload["status"] == "generating"
        assert "updated_at" in update_payload

    @pytest.mark.asyncio
    async def test_accepts_string_status(self):
        session = _make_session()
        data = session.model_dump(mode="json")
        store, client = _make_store()
        doc_ref = _wire_doc_ref(client, _mock_doc(exists=True, data=data))

        await store.update_session_status(SESSION_ID, "complete")

        update_payload = doc_ref.update.call_args[0][0]
        assert update_payload["status"] == "complete"

    @pytest.mark.asyncio
    async def test_raises_when_session_missing(self):
        store, client = _make_store()
        _wire_doc_ref(client, _mock_doc(exists=False))

        with pytest.raises(SessionNotFoundError):
            await store.update_session_status(SESSION_ID, SessionStatus.error)


# ---------------------------------------------------------------------------
# update_story_arc
# ---------------------------------------------------------------------------


class TestUpdateStoryArc:
    @pytest.mark.asyncio
    async def test_calls_update_with_arc_and_updated_at(self):
        session = _make_session()
        data = session.model_dump(mode="json")
        store, client = _make_store()
        doc_ref = _wire_doc_ref(client, _mock_doc(exists=True, data=data))
        arc = ["beat1", "beat2", "beat3", "beat4", "beat5"]

        await store.update_story_arc(SESSION_ID, arc)

        doc_ref.update.assert_called_once()
        update_payload = doc_ref.update.call_args[0][0]
        assert update_payload["story_arc"] == arc
        assert "updated_at" in update_payload

    @pytest.mark.asyncio
    async def test_raises_when_session_missing(self):
        store, client = _make_store()
        _wire_doc_ref(client, _mock_doc(exists=False))

        with pytest.raises(SessionNotFoundError):
            await store.update_story_arc(SESSION_ID, ["a", "b", "c", "d", "e"])


# ---------------------------------------------------------------------------
# save_story_brief
# ---------------------------------------------------------------------------


class TestSaveStoryBrief:
    @pytest.mark.asyncio
    async def test_calls_set_on_story_brief_sub_doc(self):
        brief = _make_story_brief()
        store, client = _make_store()

        # Wire nested collection chain for story_brief/main
        brief_doc_ref = MagicMock()
        brief_doc_ref.set = AsyncMock()
        brief_col = MagicMock()
        brief_col.document = MagicMock(return_value=brief_doc_ref)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=brief_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        await store.save_story_brief(SESSION_ID, brief)

        brief_doc_ref.set.assert_called_once()
        payload = brief_doc_ref.set.call_args[0][0]
        assert payload["protagonist_name"] == "Spark"
        assert payload["tone"] == "adventurous"
        assert payload["confirmed_by_agent"] is True

    @pytest.mark.asyncio
    async def test_serialises_raw_setup_transcript(self):
        brief = _make_story_brief()
        store, client = _make_store()

        brief_doc_ref = MagicMock()
        brief_doc_ref.set = AsyncMock()
        brief_col = MagicMock()
        brief_col.document = MagicMock(return_value=brief_doc_ref)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=brief_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        await store.save_story_brief(SESSION_ID, brief)

        payload = brief_doc_ref.set.call_args[0][0]
        assert "raw_setup_transcript" in payload


# ---------------------------------------------------------------------------
# get_story_brief
# ---------------------------------------------------------------------------


class TestGetStoryBrief:
    @pytest.mark.asyncio
    async def test_returns_none_when_doc_missing(self):
        store, client = _make_store()

        brief_doc_ref = MagicMock()
        brief_doc_ref.get = AsyncMock(return_value=_mock_doc(exists=False))
        brief_col = MagicMock()
        brief_col.document = MagicMock(return_value=brief_doc_ref)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=brief_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        result = await store.get_story_brief(SESSION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_brief_when_doc_exists(self):
        brief = _make_story_brief()
        data = brief.model_dump(mode="json")
        store, client = _make_store()

        brief_doc_ref = MagicMock()
        brief_doc_ref.get = AsyncMock(return_value=_mock_doc(exists=True, data=data))
        brief_col = MagicMock()
        brief_col.document = MagicMock(return_value=brief_doc_ref)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=brief_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        result = await store.get_story_brief(SESSION_ID)

        assert isinstance(result, StoryBrief)
        assert result.protagonist_name == "Spark"
        assert result.tone == "adventurous"


# ---------------------------------------------------------------------------
# save_user_turn
# ---------------------------------------------------------------------------


class TestSaveUserTurn:
    @pytest.mark.asyncio
    async def test_calls_set_on_turn_sub_doc(self):
        turn = _make_user_turn(sequence=1)
        store, client = _make_store()

        turn_doc_ref = MagicMock()
        turn_doc_ref.set = AsyncMock()
        turns_col = MagicMock()
        turns_col.document = MagicMock(return_value=turn_doc_ref)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=turns_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        await store.save_user_turn(SESSION_ID, turn)

        turn_doc_ref.set.assert_called_once()
        payload = turn_doc_ref.set.call_args[0][0]
        assert payload["sequence"] == 1
        assert payload["speaker"] == "user"
        assert payload["phase"] == "setup"

    @pytest.mark.asyncio
    async def test_uses_turn_id_as_document_id(self):
        turn = _make_user_turn()
        store, client = _make_store()

        turn_doc_ref = MagicMock()
        turn_doc_ref.set = AsyncMock()
        turns_col = MagicMock()
        turns_col.document = MagicMock(return_value=turn_doc_ref)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=turns_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        await store.save_user_turn(SESSION_ID, turn)

        turns_col.document.assert_called_with(TURN_ID)


# ---------------------------------------------------------------------------
# list_user_turns
# ---------------------------------------------------------------------------


class TestListUserTurns:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_turns(self):
        store, client = _make_store()

        query_mock = MagicMock()
        query_mock.get = AsyncMock(return_value=[])
        turns_col = MagicMock()
        turns_col.order_by = MagicMock(return_value=query_mock)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=turns_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_user_turns(SESSION_ID)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_turns_in_sequence_order(self):
        turn1 = _make_user_turn(sequence=1)
        turn2 = UserTurn(
            sequence=2,
            phase="setup",
            speaker="agent",
            raw_transcript="Tell me your story idea",
            caption_text="Tell me your story idea",
            timestamp=NOW,
        )
        store, client = _make_store()

        def _make_turn_doc(turn: UserTurn):
            doc = MagicMock()
            doc.to_dict = MagicMock(return_value=turn.model_dump(mode="json"))
            return doc

        query_mock = MagicMock()
        query_mock.get = AsyncMock(return_value=[_make_turn_doc(turn1), _make_turn_doc(turn2)])
        turns_col = MagicMock()
        turns_col.order_by = MagicMock(return_value=query_mock)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=turns_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_user_turns(SESSION_ID)

        assert len(result) == 2
        assert result[0].sequence == 1
        assert result[1].sequence == 2
        assert result[0].speaker == "user"
        assert result[1].speaker == "agent"

    @pytest.mark.asyncio
    async def test_orders_by_sequence(self):
        store, client = _make_store()

        query_mock = MagicMock()
        query_mock.get = AsyncMock(return_value=[])
        turns_col = MagicMock()
        turns_col.order_by = MagicMock(return_value=query_mock)
        session_doc_ref = MagicMock()
        session_doc_ref.collection = MagicMock(return_value=turns_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc_ref)
        client.collection = MagicMock(return_value=top_col)

        await store.list_user_turns(SESSION_ID)

        turns_col.order_by.assert_called_with("sequence")
