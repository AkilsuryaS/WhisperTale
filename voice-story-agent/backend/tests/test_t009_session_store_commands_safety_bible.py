"""
Tests for T-009: SessionStore — VoiceCommand, SafetyDecision,
CharacterBible, and StyleBible CRUD.

Strategy: inject a mock AsyncClient identical to T-007/T-008 tests.

Firestore paths covered:
    sessions/{id}/voice_commands/{command_id}    ← VoiceCommand
    sessions/{id}/safety_decisions/{decision_id} ← SafetyDecision
    sessions/{id}/character_bible/main           ← CharacterBible
    sessions/{id}/style_bible/main               ← StyleBible

Covers:
- save_voice_command: .set() on voice_commands/{command_id}; uses command_id as doc ID
- list_voice_commands: order_by("received_at"); returns [] when empty; returns models
- save_safety_decision: .set() on safety_decisions/{decision_id}
- list_safety_decisions: order_by("triggered_at"); returns [] when empty; returns models
- save_character_bible: single batch.commit() writes character_bible/main AND style_bible/main
- get_character_bible: returns CharacterBible | None
- update_character_bible_field: .update() with dot-notation field path + value
- save_style_bible: .set() on style_bible/main
- get_style_bible: returns StyleBible | None
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.safety import SafetyCategory, SafetyDecision
from app.models.voice_command import CommandType, VoiceCommand
from app.services.session_store import SessionStore

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> tuple[SessionStore, MagicMock]:
    client = MagicMock()
    store = SessionStore(client=client)
    return store, client


def _mock_doc(exists: bool, data: dict | None = None) -> MagicMock:
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict = MagicMock(return_value=data or {})
    return doc


def _wire_subcollection(client: MagicMock, sub_col_name: str, doc_mock: MagicMock) -> MagicMock:
    """Wire: client.collection("sessions").document(id).collection(sub_col_name).document(x)."""
    sub_doc_ref = MagicMock()
    sub_doc_ref.get = AsyncMock(return_value=doc_mock)
    sub_doc_ref.set = AsyncMock()
    sub_doc_ref.update = AsyncMock()
    sub_col = MagicMock()
    sub_col.document = MagicMock(return_value=sub_doc_ref)
    sub_col.order_by = MagicMock(return_value=sub_col)
    sub_col.get = AsyncMock(return_value=[])
    session_doc = MagicMock()
    session_doc.collection = MagicMock(return_value=sub_col)
    top_col = MagicMock()
    top_col.document = MagicMock(return_value=session_doc)
    client.collection = MagicMock(return_value=top_col)
    return sub_doc_ref


def _make_voice_command() -> VoiceCommand:
    return VoiceCommand(
        command_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        raw_transcript="Make it more exciting",
        interpreted_intent="increase pacing",
        command_type=CommandType.pacing_change,
        applied_to_pages=[3, 4, 5],
        safe=True,
        received_at=NOW,
    )


def _make_safety_decision() -> SafetyDecision:
    return SafetyDecision(
        decision_id=uuid.uuid4(),
        turn_id=uuid.uuid4(),
        phase="setup",
        raw_input="add blood and gore",
        detected_category=SafetyCategory.gore,
        proposed_rewrite="How about a fun painting adventure?",
        user_accepted=True,
        triggered_at=NOW,
    )


def _make_protagonist() -> ProtagonistProfile:
    return ProtagonistProfile(
        name="Spark",
        species_or_type="purple dragon",
        color="bright purple",
        notable_traits=["big round eyes", "stumpy legs"],
    )


def _make_style_bible() -> StyleBible:
    return StyleBible(
        art_style="soft colorful picture book",
        color_palette="pastel purples, warm yellows",
        mood="warm, gentle, playful",
        negative_style_terms=["realistic", "dark", "scary"],
    )


def _make_character_bible() -> CharacterBible:
    return CharacterBible(
        protagonist=_make_protagonist(),
        style_bible=_make_style_bible(),
        content_policy=ContentPolicy(exclusions=["no gore"]),
    )


# ---------------------------------------------------------------------------
# save_voice_command
# ---------------------------------------------------------------------------


class TestSaveVoiceCommand:
    @pytest.mark.asyncio
    async def test_calls_set_with_serialised_data(self):
        cmd = _make_voice_command()
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "voice_commands", _mock_doc(exists=False))

        await store.save_voice_command(SESSION_ID, cmd)

        doc_ref.set.assert_called_once()
        payload = doc_ref.set.call_args[0][0]
        assert payload["command_type"] == "pacing_change"
        assert payload["safe"] is True
        assert payload["applied_to_pages"] == [3, 4, 5]

    @pytest.mark.asyncio
    async def test_document_id_is_command_id(self):
        cmd = _make_voice_command()
        store, client = _make_store()
        sub_col = MagicMock()
        doc_ref = MagicMock()
        doc_ref.set = AsyncMock()
        sub_col.document = MagicMock(return_value=doc_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_voice_command(SESSION_ID, cmd)

        sub_col.document.assert_called_with(str(cmd.command_id))

    @pytest.mark.asyncio
    async def test_serialises_uuid_fields_as_strings(self):
        cmd = _make_voice_command()
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "voice_commands", _mock_doc(exists=False))

        await store.save_voice_command(SESSION_ID, cmd)

        payload = doc_ref.set.call_args[0][0]
        assert isinstance(payload["command_id"], str)
        assert isinstance(payload["turn_id"], str)


# ---------------------------------------------------------------------------
# list_voice_commands
# ---------------------------------------------------------------------------


class TestListVoiceCommands:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_none(self):
        store, client = _make_store()
        query = MagicMock()
        query.get = AsyncMock(return_value=[])
        sub_col = MagicMock()
        sub_col.order_by = MagicMock(return_value=query)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_voice_commands(SESSION_ID)
        assert result == []

    @pytest.mark.asyncio
    async def test_orders_by_received_at(self):
        store, client = _make_store()
        query = MagicMock()
        query.get = AsyncMock(return_value=[])
        sub_col = MagicMock()
        sub_col.order_by = MagicMock(return_value=query)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.list_voice_commands(SESSION_ID)
        sub_col.order_by.assert_called_with("received_at")

    @pytest.mark.asyncio
    async def test_deserialises_voice_commands(self):
        cmd = _make_voice_command()
        data = cmd.model_dump(mode="json")
        mock_doc = MagicMock()
        mock_doc.to_dict = MagicMock(return_value=data)
        store, client = _make_store()
        query = MagicMock()
        query.get = AsyncMock(return_value=[mock_doc])
        sub_col = MagicMock()
        sub_col.order_by = MagicMock(return_value=query)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_voice_commands(SESSION_ID)
        assert len(result) == 1
        assert isinstance(result[0], VoiceCommand)
        assert result[0].command_type == "pacing_change"


# ---------------------------------------------------------------------------
# save_safety_decision
# ---------------------------------------------------------------------------


class TestSaveSafetyDecision:
    @pytest.mark.asyncio
    async def test_calls_set_with_serialised_data(self):
        decision = _make_safety_decision()
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "safety_decisions", _mock_doc(exists=False))

        await store.save_safety_decision(SESSION_ID, decision)

        doc_ref.set.assert_called_once()
        payload = doc_ref.set.call_args[0][0]
        assert payload["detected_category"] == "gore"
        assert payload["user_accepted"] is True
        assert payload["phase"] == "setup"

    @pytest.mark.asyncio
    async def test_document_id_is_decision_id(self):
        decision = _make_safety_decision()
        store, client = _make_store()
        sub_col = MagicMock()
        doc_ref = MagicMock()
        doc_ref.set = AsyncMock()
        sub_col.document = MagicMock(return_value=doc_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_safety_decision(SESSION_ID, decision)
        sub_col.document.assert_called_with(str(decision.decision_id))


# ---------------------------------------------------------------------------
# list_safety_decisions
# ---------------------------------------------------------------------------


class TestListSafetyDecisions:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_none(self):
        store, client = _make_store()
        query = MagicMock()
        query.get = AsyncMock(return_value=[])
        sub_col = MagicMock()
        sub_col.order_by = MagicMock(return_value=query)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_safety_decisions(SESSION_ID)
        assert result == []

    @pytest.mark.asyncio
    async def test_orders_by_triggered_at(self):
        store, client = _make_store()
        query = MagicMock()
        query.get = AsyncMock(return_value=[])
        sub_col = MagicMock()
        sub_col.order_by = MagicMock(return_value=query)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.list_safety_decisions(SESSION_ID)
        sub_col.order_by.assert_called_with("triggered_at")

    @pytest.mark.asyncio
    async def test_deserialises_safety_decisions(self):
        decision = _make_safety_decision()
        data = decision.model_dump(mode="json")
        mock_doc = MagicMock()
        mock_doc.to_dict = MagicMock(return_value=data)
        store, client = _make_store()
        query = MagicMock()
        query.get = AsyncMock(return_value=[mock_doc])
        sub_col = MagicMock()
        sub_col.order_by = MagicMock(return_value=query)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_safety_decisions(SESSION_ID)
        assert len(result) == 1
        assert isinstance(result[0], SafetyDecision)
        assert result[0].detected_category == "gore"


# ---------------------------------------------------------------------------
# save_character_bible — atomic batch write
# ---------------------------------------------------------------------------


class TestSaveCharacterBible:
    @pytest.mark.asyncio
    async def test_uses_single_batch_commit(self):
        bible = _make_character_bible()
        store, client = _make_store()

        batch = MagicMock()
        batch.set = MagicMock()
        batch.commit = AsyncMock()
        client.batch = MagicMock(return_value=batch)

        # Wire collection chain so refs resolve
        doc_ref = MagicMock()
        sub_col = MagicMock()
        sub_col.document = MagicMock(return_value=doc_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_character_bible(SESSION_ID, bible)

        # Exactly one commit — not two separate writes
        batch.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_batch_writes_character_bible_and_style_bible(self):
        bible = _make_character_bible()
        store, client = _make_store()

        batch = MagicMock()
        batch.set = MagicMock()
        batch.commit = AsyncMock()
        client.batch = MagicMock(return_value=batch)

        doc_ref = MagicMock()
        sub_col = MagicMock()
        sub_col.document = MagicMock(return_value=doc_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_character_bible(SESSION_ID, bible)

        # Two batch.set() calls — one for character_bible, one for style_bible
        assert batch.set.call_count == 2

    @pytest.mark.asyncio
    async def test_character_bible_payload_contains_protagonist(self):
        bible = _make_character_bible()
        store, client = _make_store()

        set_calls = []
        batch = MagicMock()
        batch.set = MagicMock(side_effect=lambda ref, data: set_calls.append(data))
        batch.commit = AsyncMock()
        client.batch = MagicMock(return_value=batch)

        doc_ref = MagicMock()
        sub_col = MagicMock()
        sub_col.document = MagicMock(return_value=doc_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_character_bible(SESSION_ID, bible)

        bible_payload = set_calls[0]
        assert "protagonist" in bible_payload
        assert bible_payload["protagonist"]["name"] == "Spark"

    @pytest.mark.asyncio
    async def test_style_bible_payload_is_standalone(self):
        bible = _make_character_bible()
        store, client = _make_store()

        set_calls = []
        batch = MagicMock()
        batch.set = MagicMock(side_effect=lambda ref, data: set_calls.append(data))
        batch.commit = AsyncMock()
        client.batch = MagicMock(return_value=batch)

        doc_ref = MagicMock()
        sub_col = MagicMock()
        sub_col.document = MagicMock(return_value=doc_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=sub_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_character_bible(SESSION_ID, bible)

        style_payload = set_calls[1]
        assert "art_style" in style_payload
        assert "mood" in style_payload
        assert "protagonist" not in style_payload


# ---------------------------------------------------------------------------
# get_character_bible
# ---------------------------------------------------------------------------


class TestGetCharacterBible:
    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self):
        store, client = _make_store()
        _wire_subcollection(client, "character_bible", _mock_doc(exists=False))

        result = await store.get_character_bible(SESSION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_character_bible_when_exists(self):
        bible = _make_character_bible()
        data = bible.model_dump(mode="json")
        store, client = _make_store()
        _wire_subcollection(client, "character_bible", _mock_doc(exists=True, data=data))

        result = await store.get_character_bible(SESSION_ID)

        assert isinstance(result, CharacterBible)
        assert result.protagonist.name == "Spark"
        assert result.content_policy.exclusions == ["no gore"]


# ---------------------------------------------------------------------------
# update_character_bible_field
# ---------------------------------------------------------------------------


class TestUpdateCharacterBibleField:
    @pytest.mark.asyncio
    async def test_calls_update_with_field_and_value(self):
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "character_bible", _mock_doc(exists=True))

        await store.update_character_bible_field(
            SESSION_ID, "content_policy.exclusions", ["no gore", "no darkness"]
        )

        doc_ref.update.assert_called_once_with(
            {"content_policy.exclusions": ["no gore", "no darkness"]}
        )

    @pytest.mark.asyncio
    async def test_dot_notation_field_passed_through(self):
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "character_bible", _mock_doc(exists=True))

        await store.update_character_bible_field(
            SESSION_ID, "protagonist.reference_image_gcs_uri", "gs://b/s/p/1/illustration.png"
        )

        call_payload = doc_ref.update.call_args[0][0]
        assert "protagonist.reference_image_gcs_uri" in call_payload

    @pytest.mark.asyncio
    async def test_accepts_any_value_type(self):
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "character_bible", _mock_doc(exists=True))

        # string value
        await store.update_character_bible_field(SESSION_ID, "style_bible.mood", "exciting")
        payload = doc_ref.update.call_args[0][0]
        assert payload["style_bible.mood"] == "exciting"


# ---------------------------------------------------------------------------
# save_style_bible
# ---------------------------------------------------------------------------


class TestSaveStyleBible:
    @pytest.mark.asyncio
    async def test_calls_set_with_serialised_data(self):
        style = _make_style_bible()
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "style_bible", _mock_doc(exists=False))

        await store.save_style_bible(SESSION_ID, style)

        doc_ref.set.assert_called_once()
        payload = doc_ref.set.call_args[0][0]
        assert payload["art_style"] == "soft colorful picture book"
        assert payload["mood"] == "warm, gentle, playful"
        assert "protagonist" not in payload

    @pytest.mark.asyncio
    async def test_overwrites_existing_style_bible(self):
        style = _make_style_bible()
        store, client = _make_store()
        doc_ref = _wire_subcollection(client, "style_bible", _mock_doc(exists=True))

        await store.save_style_bible(SESSION_ID, style)

        doc_ref.set.assert_called_once()


# ---------------------------------------------------------------------------
# get_style_bible
# ---------------------------------------------------------------------------


class TestGetStyleBible:
    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self):
        store, client = _make_store()
        _wire_subcollection(client, "style_bible", _mock_doc(exists=False))

        result = await store.get_style_bible(SESSION_ID)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_style_bible_when_exists(self):
        style = _make_style_bible()
        data = style.model_dump(mode="json")
        store, client = _make_store()
        _wire_subcollection(client, "style_bible", _mock_doc(exists=True, data=data))

        result = await store.get_style_bible(SESSION_ID)

        assert isinstance(result, StyleBible)
        assert result.mood == "warm, gentle, playful"
        assert result.art_style == "soft colorful picture book"
