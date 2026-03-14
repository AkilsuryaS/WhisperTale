"""
Tests for T-021: REST endpoints
    POST /sessions/{session_id}/voice-session
    POST /sessions/{session_id}/character-bible/generate

Uses FastAPI's synchronous TestClient (httpx) with SessionStore,
VoiceSessionService, and CharacterBibleService all injected as mocks
via app.dependency_overrides — no real Firestore, GCS, or Gemini calls.

Covers:
    voice-session
        - 200 {session_id, ready: True, voice_model} on a ``setup`` session
        - 409 when session status is not ``setup``
        - 404 for unknown session IDs
        - 502 when VoiceSessionService.start raises VoiceSessionError

    character-bible/generate
        - 200 full CharacterBible JSON when status is ``generating``
        - 409 when session status is not ``generating``
        - 409 when StoryBrief is not yet confirmed (None)
        - 404 for unknown session IDs
        - 502 when CharacterBibleService.initialise raises CharacterBibleServiceError
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_store, get_voice_service
from app.exceptions import (
    CharacterBibleServiceError,
    SessionNotFoundError,
    VoiceSessionError,
)
from app.main import app
from app.models.character_bible import (
    CharacterBible,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.models.session import Session, SessionStatus, StoryBrief, Tone

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())
VOICE_MODEL = "gemini-2.0-flash-live-001"


def _make_session(status: SessionStatus = SessionStatus.setup) -> Session:
    return Session(
        session_id=uuid.UUID(SESSION_ID),
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_story_brief() -> StoryBrief:
    return StoryBrief(
        protagonist_name="Pip",
        protagonist_description="a small blue rabbit with floppy ears",
        setting="the Enchanted Meadow",
        tone=Tone.warm,
        raw_setup_transcript="Pip is a small blue rabbit in the Enchanted Meadow",
        confirmed_at=NOW,
        confirmed_by_agent=True,
    )


def _make_character_bible() -> CharacterBible:
    return CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="rabbit",
            color="blue",
            attire=None,
            notable_traits=["curious", "brave"],
        ),
        style_bible=StyleBible(
            art_style="soft watercolour illustration",
            color_palette="warm pastels",
            mood="warm",
            negative_style_terms=["dark", "scary"],
        ),
        content_policy=ContentPolicy(
            exclusions=["no gore", "no character death"],
        ),
    )


def _mock_store(
    session: Session | None = None,
    session_error: bool = False,
    story_brief: StoryBrief | None = None,
) -> MagicMock:
    store = MagicMock()

    if session_error:
        store.get_session = AsyncMock(side_effect=SessionNotFoundError(SESSION_ID))
    else:
        store.get_session = AsyncMock(return_value=session or _make_session())

    store.get_story_brief = AsyncMock(return_value=story_brief)
    return store


def _mock_voice_svc(start_error: bool = False) -> MagicMock:
    svc = MagicMock()
    if start_error:
        svc.start = AsyncMock(
            side_effect=VoiceSessionError("Gemini Live unavailable")
        )
    else:
        svc.start = AsyncMock(return_value=None)
    return svc


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/voice-session
# ---------------------------------------------------------------------------


class TestVoiceSessionEndpoint:
    """Tests for POST /sessions/{session_id}/voice-session."""

    def test_200_on_setup_session(self) -> None:
        """Returns 200 with ready=True and voice_model for a setup-status session."""
        mock_store = _mock_store(session=_make_session(SessionStatus.setup))
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            resp = client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == SESSION_ID
        assert data["ready"] is True
        assert "voice_model" in data

    def test_200_response_calls_voice_svc_start(self) -> None:
        """VoiceSessionService.start is called once with the session_id."""
        mock_store = _mock_store(session=_make_session(SessionStatus.setup))
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        mock_voice.start.assert_called_once()
        call_args = mock_voice.start.call_args
        assert call_args[0][0] == SESSION_ID  # first positional arg is session_id

    def test_409_when_session_status_is_generating(self) -> None:
        """Returns 409 if the session is already in generating status."""
        mock_store = _mock_store(session=_make_session(SessionStatus.generating))
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            resp = client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        assert resp.status_code == 409

    def test_409_when_session_status_is_complete(self) -> None:
        """Returns 409 if the session is in complete status."""
        mock_store = _mock_store(session=_make_session(SessionStatus.complete))
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            resp = client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        assert resp.status_code == 409

    def test_404_for_unknown_session(self) -> None:
        """Returns 404 when the session does not exist."""
        mock_store = _mock_store(session_error=True)
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            resp = client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        assert resp.status_code == 404

    def test_502_when_voice_service_raises(self) -> None:
        """Returns 502 when VoiceSessionService.start raises VoiceSessionError."""
        mock_store = _mock_store(session=_make_session(SessionStatus.setup))
        mock_voice = _mock_voice_svc(start_error=True)

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            resp = client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        assert resp.status_code == 502

    def test_voice_svc_not_called_on_404(self) -> None:
        """VoiceSessionService.start is NOT called when the session is not found."""
        mock_store = _mock_store(session_error=True)
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        mock_voice.start.assert_not_called()

    def test_voice_svc_not_called_on_409(self) -> None:
        """VoiceSessionService.start is NOT called when session status is wrong."""
        mock_store = _mock_store(session=_make_session(SessionStatus.generating))
        mock_voice = _mock_voice_svc()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_voice_service] = lambda: mock_voice

        with TestClient(app) as client:
            client.post(f"/sessions/{SESSION_ID}/voice-session")

        app.dependency_overrides.clear()

        mock_voice.start.assert_not_called()


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/character-bible/generate
# ---------------------------------------------------------------------------


class TestCharacterBibleGenerateEndpoint:
    """Tests for POST /sessions/{session_id}/character-bible/generate."""

    def test_200_returns_character_bible(self) -> None:
        """Returns 200 with full CharacterBible JSON for a generating-status session."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.generating),
            story_brief=_make_story_brief(),
        )
        bible = _make_character_bible()

        app.dependency_overrides[get_store] = lambda: mock_store

        with patch(
            "app.routers.sessions.CharacterBibleService"
        ) as MockBibleSvc:
            mock_svc_instance = MagicMock()
            mock_svc_instance.initialise = AsyncMock(return_value=bible)
            MockBibleSvc.return_value = mock_svc_instance

            with TestClient(app) as client:
                resp = client.post(
                    f"/sessions/{SESSION_ID}/character-bible/generate"
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert data["protagonist"]["name"] == "Pip"
        assert data["protagonist"]["color"] == "blue"
        assert data["style_bible"]["mood"] == "warm"

    def test_200_calls_initialise_with_session_id_and_brief(self) -> None:
        """CharacterBibleService.initialise is called with session_id and brief."""
        brief = _make_story_brief()
        mock_store = _mock_store(
            session=_make_session(SessionStatus.generating),
            story_brief=brief,
        )
        bible = _make_character_bible()

        app.dependency_overrides[get_store] = lambda: mock_store

        with patch(
            "app.routers.sessions.CharacterBibleService"
        ) as MockBibleSvc:
            mock_svc_instance = MagicMock()
            mock_svc_instance.initialise = AsyncMock(return_value=bible)
            MockBibleSvc.return_value = mock_svc_instance

            with TestClient(app) as client:
                client.post(f"/sessions/{SESSION_ID}/character-bible/generate")

            mock_svc_instance.initialise.assert_called_once_with(SESSION_ID, brief)

        app.dependency_overrides.clear()

    def test_409_when_session_status_is_setup(self) -> None:
        """Returns 409 if the session is still in setup status."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.setup),
            story_brief=_make_story_brief(),
        )
        app.dependency_overrides[get_store] = lambda: mock_store

        with TestClient(app) as client:
            resp = client.post(
                f"/sessions/{SESSION_ID}/character-bible/generate"
            )

        app.dependency_overrides.clear()

        assert resp.status_code == 409

    def test_409_when_story_brief_is_none(self) -> None:
        """Returns 409 if the StoryBrief has not been confirmed yet."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.generating),
            story_brief=None,
        )
        app.dependency_overrides[get_store] = lambda: mock_store

        with TestClient(app) as client:
            resp = client.post(
                f"/sessions/{SESSION_ID}/character-bible/generate"
            )

        app.dependency_overrides.clear()

        assert resp.status_code == 409

    def test_404_for_unknown_session(self) -> None:
        """Returns 404 when the session does not exist."""
        mock_store = _mock_store(session_error=True)
        app.dependency_overrides[get_store] = lambda: mock_store

        with TestClient(app) as client:
            resp = client.post(
                f"/sessions/{SESSION_ID}/character-bible/generate"
            )

        app.dependency_overrides.clear()

        assert resp.status_code == 404

    def test_502_when_bible_service_raises(self) -> None:
        """Returns 502 when CharacterBibleService.initialise raises CharacterBibleServiceError."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.generating),
            story_brief=_make_story_brief(),
        )
        app.dependency_overrides[get_store] = lambda: mock_store

        with patch(
            "app.routers.sessions.CharacterBibleService"
        ) as MockBibleSvc:
            mock_svc_instance = MagicMock()
            mock_svc_instance.initialise = AsyncMock(
                side_effect=CharacterBibleServiceError("Gemini call failed")
            )
            MockBibleSvc.return_value = mock_svc_instance

            with TestClient(app) as client:
                resp = client.post(
                    f"/sessions/{SESSION_ID}/character-bible/generate"
                )

        app.dependency_overrides.clear()

        assert resp.status_code == 502

    def test_bible_svc_not_called_on_409_status(self) -> None:
        """CharacterBibleService.initialise is NOT called when session status is wrong."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.setup),
            story_brief=_make_story_brief(),
        )
        app.dependency_overrides[get_store] = lambda: mock_store

        with patch(
            "app.routers.sessions.CharacterBibleService"
        ) as MockBibleSvc:
            mock_svc_instance = MagicMock()
            mock_svc_instance.initialise = AsyncMock()
            MockBibleSvc.return_value = mock_svc_instance

            with TestClient(app) as client:
                client.post(f"/sessions/{SESSION_ID}/character-bible/generate")

            mock_svc_instance.initialise.assert_not_called()

        app.dependency_overrides.clear()

    def test_bible_svc_not_called_on_missing_brief(self) -> None:
        """CharacterBibleService.initialise is NOT called when StoryBrief is None."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.generating),
            story_brief=None,
        )
        app.dependency_overrides[get_store] = lambda: mock_store

        with patch(
            "app.routers.sessions.CharacterBibleService"
        ) as MockBibleSvc:
            mock_svc_instance = MagicMock()
            mock_svc_instance.initialise = AsyncMock()
            MockBibleSvc.return_value = mock_svc_instance

            with TestClient(app) as client:
                client.post(f"/sessions/{SESSION_ID}/character-bible/generate")

            mock_svc_instance.initialise.assert_not_called()

        app.dependency_overrides.clear()

    def test_character_bible_response_structure(self) -> None:
        """Response JSON has protagonist, style_bible, content_policy, and character_refs."""
        mock_store = _mock_store(
            session=_make_session(SessionStatus.generating),
            story_brief=_make_story_brief(),
        )
        bible = _make_character_bible()

        app.dependency_overrides[get_store] = lambda: mock_store

        with patch(
            "app.routers.sessions.CharacterBibleService"
        ) as MockBibleSvc:
            mock_svc_instance = MagicMock()
            mock_svc_instance.initialise = AsyncMock(return_value=bible)
            MockBibleSvc.return_value = mock_svc_instance

            with TestClient(app) as client:
                resp = client.post(
                    f"/sessions/{SESSION_ID}/character-bible/generate"
                )

        app.dependency_overrides.clear()

        data = resp.json()
        assert "protagonist" in data
        assert "style_bible" in data
        assert "content_policy" in data
        assert "character_refs" in data
