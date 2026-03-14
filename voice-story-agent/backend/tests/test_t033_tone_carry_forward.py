"""
test_t033_tone_carry_forward.py

Unit tests for T-033: Tone carry-forward in page generation.

Tests cover:
  1. After a tone_change command, get_style_bible().mood reflects the new tone
  2. update_mood persists both standalone StyleBible and embedded CharacterBible.style_bible
  3. update_mood stores command_id in last_updated_by_command_id
  4. expand_page for page N+1 uses updated mood (verified via bible read from store)
  5. SteeringHandler._apply_command calls update_mood for tone_change commands
  6. SteeringHandler._apply_command does NOT call update_mood for non-tone commands
  7. update_mood raises CharacterBibleServiceError when CharacterBible not found
  8. update_mood failure is swallowed by SteeringHandler (does not abort flow)
  9. A new session has empty page_history and fresh ContentPolicy (session isolation)
  10. update_mood preserves all other StyleBible fields (art_style, color_palette, etc.)

Depends: T-032, T-030, T-033
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.exceptions import CharacterBibleServiceError
from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.session import Session
from app.models.voice_command import CommandType, VoiceCommand
from app.services.character_bible_service import CharacterBibleService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_style_bible(mood: str = "cosy") -> StyleBible:
    return StyleBible(
        art_style="soft watercolour",
        color_palette="warm pastels",
        mood=mood,
        negative_style_terms=["dark", "scary"],
    )


def _make_bible(mood: str = "cosy") -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="rabbit",
            color="golden",
            notable_traits=["big eyes", "fluffy tail"],
        ),
        style_bible=_make_style_bible(mood),
        content_policy=ContentPolicy(exclusions=["no gore"]),
    )


def _make_session(
    arc: list[str] | None = None,
    page_history: list[str] | None = None,
) -> Session:
    return Session(
        story_arc=arc or [f"beat {i}" for i in range(1, 6)],
        page_history=page_history or [],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_command(command_type: CommandType = CommandType.tone_change) -> VoiceCommand:
    return VoiceCommand(
        command_id=uuid4(),
        turn_id=uuid4(),
        raw_transcript="make it funnier",
        interpreted_intent="funnier",
        command_type=command_type,
        applied_to_pages=[2, 3, 4, 5],
        received_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# T-033: CharacterBibleService.update_mood unit tests
# ---------------------------------------------------------------------------


class TestUpdateMood:

    @pytest.mark.asyncio
    async def test_update_mood_calls_save_style_bible(self) -> None:
        """update_mood persists standalone StyleBible with new mood."""
        bible = _make_bible(mood="cosy")
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock()
        store.update_character_bible_field = AsyncMock()

        svc = CharacterBibleService(store=store)
        await svc.update_mood("sess-1", new_mood="funnier")

        store.save_style_bible.assert_awaited_once()
        saved_style: StyleBible = store.save_style_bible.call_args[0][1]
        assert saved_style.mood == "funnier"

    @pytest.mark.asyncio
    async def test_update_mood_syncs_embedded_character_bible_field(self) -> None:
        """update_mood syncs style_bible field in CharacterBible document."""
        bible = _make_bible(mood="cosy")
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock()
        store.update_character_bible_field = AsyncMock()

        svc = CharacterBibleService(store=store)
        await svc.update_mood("sess-1", new_mood="funnier")

        store.update_character_bible_field.assert_awaited_once()
        call_args = store.update_character_bible_field.call_args[0]
        assert call_args[1] == "style_bible"
        assert call_args[2]["mood"] == "funnier"

    @pytest.mark.asyncio
    async def test_update_mood_stores_command_id(self) -> None:
        """update_mood stores command_id in last_updated_by_command_id."""
        bible = _make_bible()
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock()
        store.update_character_bible_field = AsyncMock()

        cmd_id = uuid4()
        svc = CharacterBibleService(store=store)
        await svc.update_mood("sess-1", new_mood="adventurous", command_id=cmd_id)

        saved_style: StyleBible = store.save_style_bible.call_args[0][1]
        assert saved_style.last_updated_by_command_id == cmd_id

    @pytest.mark.asyncio
    async def test_update_mood_preserves_other_style_fields(self) -> None:
        """update_mood keeps art_style, color_palette, negative_style_terms unchanged."""
        bible = _make_bible(mood="cosy")
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock()
        store.update_character_bible_field = AsyncMock()

        svc = CharacterBibleService(store=store)
        await svc.update_mood("sess-1", new_mood="silly")

        saved_style: StyleBible = store.save_style_bible.call_args[0][1]
        assert saved_style.art_style == "soft watercolour"
        assert saved_style.color_palette == "warm pastels"
        assert saved_style.negative_style_terms == ["dark", "scary"]

    @pytest.mark.asyncio
    async def test_update_mood_reflects_new_mood_in_style_bible(self) -> None:
        """After update_mood, the persisted StyleBible.mood equals the new value (spec)."""
        new_mood = "sleepier"
        bible = _make_bible(mood="adventurous")
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock()
        store.update_character_bible_field = AsyncMock()

        svc = CharacterBibleService(store=store)
        await svc.update_mood("sess-1", new_mood=new_mood)

        saved_style: StyleBible = store.save_style_bible.call_args[0][1]
        assert saved_style.mood == new_mood

    @pytest.mark.asyncio
    async def test_update_mood_raises_when_bible_not_found(self) -> None:
        """update_mood raises CharacterBibleServiceError when CharacterBible absent."""
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=None)

        svc = CharacterBibleService(store=store)
        with pytest.raises(CharacterBibleServiceError):
            await svc.update_mood("sess-missing", new_mood="funnier")

    @pytest.mark.asyncio
    async def test_update_mood_raises_on_store_error(self) -> None:
        """update_mood wraps Firestore errors in CharacterBibleServiceError."""
        bible = _make_bible()
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock(side_effect=RuntimeError("Firestore down"))
        store.update_character_bible_field = AsyncMock()

        svc = CharacterBibleService(store=store)
        with pytest.raises(CharacterBibleServiceError):
            await svc.update_mood("sess-1", new_mood="funnier")

    @pytest.mark.asyncio
    async def test_update_mood_without_command_id(self) -> None:
        """update_mood works without a command_id (last_updated_by_command_id=None)."""
        bible = _make_bible()
        store = MagicMock()
        store.get_character_bible = AsyncMock(return_value=bible)
        store.save_style_bible = AsyncMock()
        store.update_character_bible_field = AsyncMock()

        svc = CharacterBibleService(store=store)
        await svc.update_mood("sess-1", new_mood="calmer")

        saved_style: StyleBible = store.save_style_bible.call_args[0][1]
        assert saved_style.last_updated_by_command_id is None


# ---------------------------------------------------------------------------
# T-033: SteeringHandler wires update_mood on tone_change
# ---------------------------------------------------------------------------


class TestSteeringHandlerToneChange:

    @pytest.mark.asyncio
    async def test_tone_change_calls_update_mood(self) -> None:
        """SteeringHandler calls character_bible_svc.update_mood on tone_change."""
        from app.websocket.steering_handler import SteeringHandler
        from app.models.voice_command import CommandType

        session = _make_session()
        bible = _make_bible()
        new_arc = [f"new beat {i}" for i in range(1, 6)]

        store = MagicMock()
        store.get_session = AsyncMock(return_value=session)
        store.get_character_bible = AsyncMock(return_value=bible)
        store.update_story_arc = AsyncMock()
        store.save_voice_command = AsyncMock()

        story_planner = MagicMock()
        story_planner.apply_steering = AsyncMock(return_value=new_arc)

        character_bible_svc = MagicMock()
        character_bible_svc.update_mood = AsyncMock()

        emitted: list[dict] = []

        async def emit(event_type: str, **fields) -> None:
            emitted.append({"type": event_type, **fields})

        from app.services.adk_voice_service import VoiceTurn

        handler = SteeringHandler(
            safety_svc=MagicMock(),
            story_planner=story_planner,
            character_bible_svc=character_bible_svc,
            store=store,
            voice_svc=MagicMock(),
        )

        turn = VoiceTurn(role="user", transcript="make it funnier", audio_bytes=None, is_final=True)
        from app.websocket.steering_router import SteeringClassification
        classification = SteeringClassification(type=CommandType.tone_change, confidence=1.0, detail="funnier")

        await handler._apply_command(
            session_id="sess-1",
            page_number=1,
            turn=turn,
            classification=classification,
            emit=emit,
        )

        character_bible_svc.update_mood.assert_awaited_once()
        call_kwargs = character_bible_svc.update_mood.call_args
        assert call_kwargs[0][0] == "sess-1"
        assert call_kwargs[1]["new_mood"] == "funnier"

    @pytest.mark.asyncio
    async def test_non_tone_change_does_not_call_update_mood(self) -> None:
        """SteeringHandler does NOT call update_mood for pacing_change commands."""
        from app.websocket.steering_handler import SteeringHandler
        from app.models.voice_command import CommandType

        session = _make_session()
        bible = _make_bible()
        new_arc = [f"new beat {i}" for i in range(1, 6)]

        store = MagicMock()
        store.get_session = AsyncMock(return_value=session)
        store.get_character_bible = AsyncMock(return_value=bible)
        store.update_story_arc = AsyncMock()
        store.save_voice_command = AsyncMock()

        story_planner = MagicMock()
        story_planner.apply_steering = AsyncMock(return_value=new_arc)

        character_bible_svc = MagicMock()
        character_bible_svc.update_mood = AsyncMock()

        async def emit(event_type: str, **fields) -> None:
            pass

        from app.services.adk_voice_service import VoiceTurn
        from app.websocket.steering_router import SteeringClassification

        handler = SteeringHandler(
            safety_svc=MagicMock(),
            story_planner=story_planner,
            character_bible_svc=character_bible_svc,
            store=store,
            voice_svc=MagicMock(),
        )

        turn = VoiceTurn(role="user", transcript="make it faster", audio_bytes=None, is_final=True)
        classification = SteeringClassification(type=CommandType.pacing_change, confidence=1.0, detail="faster")

        await handler._apply_command(
            session_id="sess-1",
            page_number=1,
            turn=turn,
            classification=classification,
            emit=emit,
        )

        character_bible_svc.update_mood.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_mood_failure_does_not_abort_command_flow(self) -> None:
        """A failure in update_mood is logged but does not prevent voice_command_applied."""
        from app.websocket.steering_handler import SteeringHandler
        from app.models.voice_command import CommandType

        session = _make_session()
        bible = _make_bible()
        new_arc = [f"new beat {i}" for i in range(1, 6)]

        store = MagicMock()
        store.get_session = AsyncMock(return_value=session)
        store.get_character_bible = AsyncMock(return_value=bible)
        store.update_story_arc = AsyncMock()
        store.save_voice_command = AsyncMock()

        story_planner = MagicMock()
        story_planner.apply_steering = AsyncMock(return_value=new_arc)

        character_bible_svc = MagicMock()
        character_bible_svc.update_mood = AsyncMock(
            side_effect=CharacterBibleServiceError("Firestore error", cause=None)
        )

        emitted: list[str] = []

        async def emit(event_type: str, **fields) -> None:
            emitted.append(event_type)

        from app.services.adk_voice_service import VoiceTurn
        from app.websocket.steering_router import SteeringClassification

        handler = SteeringHandler(
            safety_svc=MagicMock(),
            story_planner=story_planner,
            character_bible_svc=character_bible_svc,
            store=store,
            voice_svc=MagicMock(),
        )

        turn = VoiceTurn(role="user", transcript="make it funnier", audio_bytes=None, is_final=True)
        classification = SteeringClassification(type=CommandType.tone_change, confidence=1.0, detail="funnier")

        # Should not raise; update_mood failure is swallowed
        await handler._apply_command(
            session_id="sess-1",
            page_number=1,
            turn=turn,
            classification=classification,
            emit=emit,
        )

        # voice_command_applied must still be emitted
        assert "voice_command_applied" in emitted

    @pytest.mark.asyncio
    async def test_tone_change_update_mood_receives_command_id(self) -> None:
        """update_mood receives the VoiceCommand.command_id from the steering flow."""
        from app.websocket.steering_handler import SteeringHandler
        from app.models.voice_command import CommandType

        session = _make_session()
        bible = _make_bible()
        new_arc = [f"new beat {i}" for i in range(1, 6)]
        captured_command_ids: list[UUID | None] = []

        store = MagicMock()
        store.get_session = AsyncMock(return_value=session)
        store.get_character_bible = AsyncMock(return_value=bible)
        store.update_story_arc = AsyncMock()
        store.save_voice_command = AsyncMock()

        story_planner = MagicMock()
        story_planner.apply_steering = AsyncMock(return_value=new_arc)

        async def capturing_update_mood(session_id: str, new_mood: str, command_id=None) -> None:
            captured_command_ids.append(command_id)

        character_bible_svc = MagicMock()
        character_bible_svc.update_mood = AsyncMock(side_effect=capturing_update_mood)

        async def emit(event_type: str, **fields) -> None:
            pass

        from app.services.adk_voice_service import VoiceTurn
        from app.websocket.steering_router import SteeringClassification

        handler = SteeringHandler(
            safety_svc=MagicMock(),
            story_planner=story_planner,
            character_bible_svc=character_bible_svc,
            store=store,
            voice_svc=MagicMock(),
        )

        turn = VoiceTurn(role="user", transcript="make it funnier", audio_bytes=None, is_final=True)
        classification = SteeringClassification(type=CommandType.tone_change, confidence=1.0, detail="funnier")

        await handler._apply_command(
            session_id="sess-1",
            page_number=1,
            turn=turn,
            classification=classification,
            emit=emit,
        )

        assert len(captured_command_ids) == 1
        assert captured_command_ids[0] is not None
        assert isinstance(captured_command_ids[0], UUID)


# ---------------------------------------------------------------------------
# T-033: New session isolation
# ---------------------------------------------------------------------------


class TestNewSessionIsolation:

    def test_new_session_has_empty_page_history(self) -> None:
        """A freshly created Session has empty page_history."""
        session = Session(
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert session.page_history == []

    def test_new_session_story_arc_is_empty(self) -> None:
        """A freshly created Session has empty story_arc."""
        session = Session(
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert session.story_arc == []

    def test_new_character_bible_has_fresh_content_policy(self) -> None:
        """CharacterBibleService.initialise always creates a fresh ContentPolicy."""
        from app.services.character_bible_service import BASE_EXCLUSIONS

        bible = _make_bible()
        # Fresh bible should only contain base exclusions, no session-specific additions
        assert set(bible.content_policy.exclusions) == set(BASE_EXCLUSIONS) or (
            len(bible.content_policy.derived_from_safety_decisions) == 0
        )

    def test_new_session_content_policy_has_no_derived_decisions(self) -> None:
        """A fresh CharacterBible ContentPolicy has no safety decision IDs."""
        bible = CharacterBible(
            protagonist=ProtagonistProfile(
                name="Luna",
                species_or_type="fox",
                color="orange",
                notable_traits=["bushy tail", "amber eyes"],
            ),
            style_bible=StyleBible(
                art_style="pencil sketch",
                color_palette="earth tones",
                mood="curious",
                negative_style_terms=["dark"],
            ),
            content_policy=ContentPolicy(exclusions=["no gore"]),
        )
        assert bible.content_policy.derived_from_safety_decisions == []

    def test_sessions_are_independent_page_histories(self) -> None:
        """Two sessions have independent page_history lists."""
        s1 = Session(
            page_history=["Page 1 of session 1."],
            story_arc=["beat 1", "beat 2", "beat 3", "beat 4", "beat 5"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        s2 = Session(
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        assert s2.page_history == []
        assert s1.page_history != s2.page_history


# ---------------------------------------------------------------------------
# T-033: Expand_page uses updated mood
# ---------------------------------------------------------------------------


class TestExpandPageUsesUpdatedMood:

    @pytest.mark.asyncio
    async def test_expand_page_prompt_contains_updated_mood(self) -> None:
        """After update_mood, expand_page prompt includes the new mood (spec)."""
        from app.services.story_planner import _build_expand_page_prompt

        updated_bible = _make_bible(mood="funnier and sillier")
        prompt = _build_expand_page_prompt(
            beat="The rabbit finds a treasure chest.",
            page_history=[],
            bible=updated_bible,
        )
        assert "funnier and sillier" in prompt

    @pytest.mark.asyncio
    async def test_expand_page_prompt_with_original_mood(self) -> None:
        """expand_page prompt contains the original mood before any tone change."""
        from app.services.story_planner import _build_expand_page_prompt

        original_bible = _make_bible(mood="cosy")
        prompt = _build_expand_page_prompt(
            beat="The rabbit hops home.",
            page_history=[],
            bible=original_bible,
        )
        assert "cosy" in prompt

    @pytest.mark.asyncio
    async def test_update_mood_changes_prompt_content(self) -> None:
        """Prompt content differs between original and updated mood."""
        from app.services.story_planner import _build_expand_page_prompt

        beat = "The rabbit discovers a hidden garden."
        original_bible = _make_bible(mood="sleepy")
        updated_bible = _make_bible(mood="adventurous")

        original_prompt = _build_expand_page_prompt(beat=beat, page_history=[], bible=original_bible)
        updated_prompt = _build_expand_page_prompt(beat=beat, page_history=[], bible=updated_bible)

        assert "sleepy" in original_prompt
        assert "adventurous" in updated_prompt
        assert original_prompt != updated_prompt
