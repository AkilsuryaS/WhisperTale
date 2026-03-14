"""
Tests for T-025: PageOrchestrator (run_page).

Covers TEST-P01 (happy path, event sequence) and TEST-P02 (asset failure scenarios).

Strategy
--------
All services are injected as AsyncMock / MagicMock objects. A simple in-memory
emit function captures events in insertion order so we can assert the exact
event sequence.

TEST-P01 — Happy-path / event-sequence tests:
    TP01-01  happy-path event order: page_generating → page_text_ready →
             (page_image_ready AND page_audio_ready in any order) → page_complete
    TP01-02  page_text_ready.text equals the mocked expand_page return value
    TP01-03  page_complete is the final event regardless of outcomes
    TP01-04  exactly 4 unique event types emitted in the happy path
    TP01-05  every emitted event carries page=page_number

TEST-P02 — Asset-failure tests:
    TP02-01  image fails → page_asset_failed(asset_type="illustration") fires
             audio unaffected, page_audio_ready IS emitted
             page_complete(illustration_failed=True, audio_failed=False) is final
    TP02-02  audio fails → page_asset_failed(asset_type="narration") fires
             image unaffected, page_image_ready IS emitted
             page_complete(illustration_failed=False, audio_failed=True) is final
    TP02-03  both fail → two page_asset_failed events, page_complete with both flags True
             no page_image_ready or page_audio_ready events
    TP02-04  reference image set on page-1 success
    TP02-05  reference image NOT set on page-1 failure
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import ImageGenerationError, TTSError
from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.websocket.page_orchestrator import run_page

# ---------------------------------------------------------------------------
# Factories / helpers
# ---------------------------------------------------------------------------

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 100
FAKE_GCS_URI = "gs://test-bucket/sessions/s1/pages/1/illustration.png"
FAKE_SIGNED_URL = "https://storage.googleapis.com/test-bucket/signed?token=abc"
FAKE_AUDIO_GCS_URI = "gs://test-bucket/sessions/s1/pages/1/narration.mp3"
FAKE_AUDIO_SIGNED = "https://storage.googleapis.com/test-bucket/audio/signed"

SESSION_ID = "test-session-001"
PAGE_NUMBER = 1
BEAT = "Pip the rabbit discovers a hidden door in the old oak tree."
PAGE_HISTORY: list[str] = []
FAKE_TEXT = (
    "Pip bounded through the misty forest, her little nose twitching with curiosity. "
    "Between the gnarled roots of the ancient oak she spotted a tiny golden door, "
    "no bigger than a mushroom cap. Her ears perked up and her tail gave an excited flap. "
    "She pressed one soft paw against the cool wood. It creaked open just a crack, "
    "and a warm amber glow spilled out across the mossy ground. "
    "Pip took a deep breath, pushed the door wide, and stepped inside."
)
FAKE_NARRATION = "Pip pushed open the tiny golden door and stepped inside."


def _make_bible() -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="rabbit",
            color="blue",
            attire="tiny red scarf",
            notable_traits=["floppy ears", "small size"],
        ),
        style_bible=StyleBible(
            art_style="soft watercolour illustration",
            color_palette="warm pastels",
            mood="cosy and adventurous",
            negative_style_terms=["dark shadows", "sharp edges"],
        ),
        content_policy=ContentPolicy(
            exclusions=["no gore", "no character death", "no physical harm"]
        ),
    )


def _make_image_prompt():
    from app.services.image_generation import ImagePrompt

    return ImagePrompt(text_prompt="test prompt", reference_urls=[])


def _make_services(
    *,
    image_fails: bool = False,
    audio_fails: bool = False,
    image_exception: Exception | None = None,
    audio_exception: Exception | None = None,
    page_number: int = PAGE_NUMBER,
) -> dict:
    """Build all mocked services for run_page."""
    bible = _make_bible()

    story_planner = MagicMock()
    story_planner.expand_page = AsyncMock(return_value=(FAKE_TEXT, FAKE_NARRATION))

    character_bible_svc = MagicMock()
    character_bible_svc.build_image_prompt = MagicMock(return_value=_make_image_prompt())
    character_bible_svc.set_reference_image = AsyncMock()

    if image_fails:
        exc = image_exception or ImageGenerationError("image generation failed")
        image_svc = MagicMock()
        image_svc.generate = AsyncMock(side_effect=exc)
    else:
        image_svc = MagicMock()
        image_svc.generate = AsyncMock(return_value=FAKE_PNG)

    if audio_fails:
        exc = audio_exception or TTSError("TTS failed")
        tts_svc = MagicMock()
        tts_svc.synthesize = AsyncMock(side_effect=exc)
    else:
        tts_svc = MagicMock()
        tts_svc.synthesize = AsyncMock(return_value=FAKE_MP3)

    def _signed_url_for(uri: str) -> str:
        if "narration" in uri:
            return FAKE_AUDIO_SIGNED
        return FAKE_SIGNED_URL

    media_svc = MagicMock()
    media_svc.store_illustration = AsyncMock(return_value=FAKE_GCS_URI)
    media_svc.store_narration = AsyncMock(return_value=FAKE_AUDIO_GCS_URI)
    media_svc.get_signed_url = AsyncMock(side_effect=_signed_url_for)

    session_store = MagicMock()
    session_store.get_character_bible = AsyncMock(return_value=bible)
    session_store.save_page = AsyncMock()

    return dict(
        story_planner=story_planner,
        character_bible_svc=character_bible_svc,
        image_svc=image_svc,
        tts_svc=tts_svc,
        media_svc=media_svc,
        session_store=session_store,
    )


class EventCapture:
    """Collects all emitted events in order."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event_type: str, **fields) -> None:
        self.events.append({"type": event_type, **fields})

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def get(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["type"] == event_type]

    def last(self) -> dict | None:
        return self.events[-1] if self.events else None


# ---------------------------------------------------------------------------
# Patch default_voice_config so it doesn't call settings
# ---------------------------------------------------------------------------


def _patched_run_page(emit, svcs, page_number=PAGE_NUMBER):
    """Call run_page with patched default_voice_config."""
    from app.services.tts_service import VoiceConfig

    fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")

    with patch(
        "app.websocket.page_orchestrator.default_voice_config",
        return_value=fake_voice,
    ):
        import asyncio

        return asyncio.get_event_loop().run_until_complete(
            run_page(
                session_id=SESSION_ID,
                page_number=page_number,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )
        )


# ---------------------------------------------------------------------------
# TEST-P01: Happy path / event sequence
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.anyio
    async def test_event_order_happy_path(self) -> None:
        """TP01-01: happy-path event order."""
        emit = EventCapture()
        svcs = _make_services()
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        types = emit.types()
        assert types[0] == "page_generating"
        assert types[1] == "page_text_ready"
        assert "page_image_ready" in types
        assert "page_audio_ready" in types
        assert types[-1] == "page_complete"

    @pytest.mark.anyio
    async def test_page_text_ready_contains_correct_text(self) -> None:
        """TP01-02: page_text_ready.text equals mocked expand_page return value."""
        emit = EventCapture()
        svcs = _make_services()
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        text_events = emit.get("page_text_ready")
        assert len(text_events) == 1
        assert text_events[0]["text"] == FAKE_TEXT

    @pytest.mark.anyio
    async def test_page_complete_always_fires(self) -> None:
        """TP01-03: page_complete is the final event regardless of outcomes."""
        emit = EventCapture()
        svcs = _make_services()
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        assert emit.last()["type"] == "page_complete"

    @pytest.mark.anyio
    async def test_exactly_four_unique_event_types(self) -> None:
        """TP01-04: exactly 4 unique event types in happy path."""
        emit = EventCapture()
        svcs = _make_services()
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        unique_types = set(emit.types())
        assert unique_types == {
            "page_generating",
            "page_text_ready",
            "page_image_ready",
            "page_audio_ready",
            "page_complete",
        }

    @pytest.mark.anyio
    async def test_every_event_carries_page_number(self) -> None:
        """TP01-05: every emitted event carries page=page_number."""
        emit = EventCapture()
        svcs = _make_services(page_number=3)
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=3,
                beat=BEAT,
                page_history=["Page 1 summary.", "Page 2 summary."],
                emit=emit,
                **svcs,
            )

        for event in emit.events:
            assert event.get("page") == 3, f"Event missing page=3: {event}"

    @pytest.mark.anyio
    async def test_page_complete_flags_false_on_success(self) -> None:
        """Happy path: page_complete.illustration_failed=False, audio_failed=False."""
        emit = EventCapture()
        svcs = _make_services()
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        complete = emit.get("page_complete")[0]
        assert complete["illustration_failed"] is False
        assert complete["audio_failed"] is False


# ---------------------------------------------------------------------------
# TEST-P02: Asset failure tests
# ---------------------------------------------------------------------------


class TestAssetFailures:
    @pytest.mark.anyio
    async def test_image_fail_audio_succeeds(self) -> None:
        """TP02-01: image fails → page_asset_failed('illustration'), audio unaffected."""
        emit = EventCapture()
        svcs = _make_services(image_fails=True)
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        types = emit.types()
        failed = emit.get("page_asset_failed")
        assert any(f["asset_type"] == "illustration" for f in failed)
        assert "page_audio_ready" in types
        assert "page_image_ready" not in types
        complete = emit.get("page_complete")[0]
        assert complete["illustration_failed"] is True
        assert complete["audio_failed"] is False
        assert types[-1] == "page_complete"

    @pytest.mark.anyio
    async def test_audio_fail_image_succeeds(self) -> None:
        """TP02-02: audio fails → page_asset_failed('narration'), image unaffected."""
        emit = EventCapture()
        svcs = _make_services(audio_fails=True)
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        types = emit.types()
        failed = emit.get("page_asset_failed")
        assert any(f["asset_type"] == "narration" for f in failed)
        assert "page_image_ready" in types
        assert "page_audio_ready" not in types
        complete = emit.get("page_complete")[0]
        assert complete["illustration_failed"] is False
        assert complete["audio_failed"] is True
        assert types[-1] == "page_complete"

    @pytest.mark.anyio
    async def test_both_fail(self) -> None:
        """TP02-03: both fail → two page_asset_failed, page_complete with both flags True."""
        emit = EventCapture()
        svcs = _make_services(image_fails=True, audio_fails=True)
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=PAGE_NUMBER,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        types = emit.types()
        failed = emit.get("page_asset_failed")
        asset_types = {f["asset_type"] for f in failed}
        assert "illustration" in asset_types
        assert "narration" in asset_types
        assert "page_image_ready" not in types
        assert "page_audio_ready" not in types
        complete = emit.get("page_complete")[0]
        assert complete["illustration_failed"] is True
        assert complete["audio_failed"] is True
        assert types[-1] == "page_complete"

    @pytest.mark.anyio
    async def test_reference_image_set_on_page1_success(self) -> None:
        """TP02-04: set_reference_image called with GCS URI on page-1 success."""
        emit = EventCapture()
        svcs = _make_services(page_number=1)
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=1,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        svcs["character_bible_svc"].set_reference_image.assert_called_once_with(
            SESSION_ID, FAKE_GCS_URI
        )

    @pytest.mark.anyio
    async def test_reference_image_not_set_on_page1_failure(self) -> None:
        """TP02-05: set_reference_image NOT called when page-1 image fails."""
        emit = EventCapture()
        svcs = _make_services(image_fails=True, page_number=1)
        from app.services.tts_service import VoiceConfig

        fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        with patch(
            "app.websocket.page_orchestrator.default_voice_config",
            return_value=fake_voice,
        ):
            await run_page(
                session_id=SESSION_ID,
                page_number=1,
                beat=BEAT,
                page_history=PAGE_HISTORY,
                emit=emit,
                **svcs,
            )

        svcs["character_bible_svc"].set_reference_image.assert_not_called()
