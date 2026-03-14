"""
Tests for T-011: REST endpoints.

Uses FastAPI's synchronous TestClient (httpx) with SessionStore
injected as a mock via app.dependency_overrides so no real Firestore
or GCS calls are made.

Covers:
    POST   /sessions                           → 201 {session_id, ws_url}
    GET    /sessions/{session_id}              → 200 Session | 404
    GET    /sessions/{session_id}/pages/{n}    → 200 Page | 404
    GET    /sessions/{session_id}/pages/{n}/assets          → 200
    GET    /sessions/{session_id}/pages/{n}/assets/{type}   → 200 | 404
    POST   /sessions/{session_id}/voice-commands            → 201 | 404
    Error schema: all 4xx responses return {"error": str}
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.dependencies import get_store
from app.exceptions import SessionNotFoundError
from app.main import app
from app.models.page import AssetStatus, AssetType, Page, PageAsset, PageStatus
from app.models.session import Session, SessionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())


def _make_session(session_id: str | None = None) -> Session:
    sid = uuid.UUID(session_id) if session_id else uuid.uuid4()
    return Session(
        session_id=sid,
        status=SessionStatus.setup,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_page(page_number: int = 1) -> Page:
    return Page(
        page_number=page_number,
        status=PageStatus.complete,
        beat="The hero begins the journey",
        text="Once upon a time...",
        generated_at=NOW,
    )


def _make_asset(page_number: int = 1, asset_type: AssetType = AssetType.illustration) -> PageAsset:
    return PageAsset(
        page_number=page_number,
        asset_type=asset_type,
        generation_status=AssetStatus.ready,
        gcs_uri=f"gs://bucket/sessions/{SESSION_ID}/pages/{page_number}/illustration.png",
    )


def _mock_store(
    session: Session | None = None,
    session_error: bool = False,
    page: Page | None = None,
    assets: list[PageAsset] | None = None,
    asset: PageAsset | None = None,
) -> MagicMock:
    """Build a mock SessionStore with configurable return values."""
    store = MagicMock()
    store.create_session = AsyncMock()

    if session_error:
        store.get_session = AsyncMock(
            side_effect=SessionNotFoundError(SESSION_ID)
        )
    else:
        store.get_session = AsyncMock(return_value=session or _make_session(SESSION_ID))

    store.get_page = AsyncMock(return_value=page)
    store.list_page_assets = AsyncMock(return_value=assets or [])
    store.get_page_asset = AsyncMock(return_value=asset)
    store.save_voice_command = AsyncMock()
    return store


def _override(store: MagicMock) -> TestClient:
    """Return a TestClient with get_store overridden to return *store*."""
    app.dependency_overrides[get_store] = lambda: store
    client = TestClient(app, raise_server_exceptions=False)
    return client


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /sessions
# ---------------------------------------------------------------------------


class TestCreateSession:
    def setup_method(self):
        _clear_overrides()

    def test_returns_201(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post("/sessions")
        assert resp.status_code == 201

    def test_body_contains_session_id(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post("/sessions")
        body = resp.json()
        assert "session_id" in body
        # Verify it is a valid UUID string
        uuid.UUID(body["session_id"])

    def test_body_contains_ws_url(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post("/sessions")
        body = resp.json()
        assert "ws_url" in body
        assert body["ws_url"].startswith("wss://")

    def test_ws_url_contains_session_id(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post("/sessions")
        body = resp.json()
        assert body["session_id"] in body["ws_url"]

    def test_ws_url_path_format(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post("/sessions")
        body = resp.json()
        session_id = body["session_id"]
        assert body["ws_url"].endswith(f"/ws/story/{session_id}")

    def test_calls_create_session_on_store(self):
        store = _mock_store()
        client = _override(store)
        client.post("/sessions")
        store.create_session.assert_called_once()

    def test_create_session_called_with_session_model(self):
        store = _mock_store()
        client = _override(store)
        client.post("/sessions")
        call_arg = store.create_session.call_args[0][0]
        assert isinstance(call_arg, Session)
        assert call_arg.status == "setup"


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------


class TestGetSession:
    def setup_method(self):
        _clear_overrides()

    def test_returns_200_when_found(self):
        store = _mock_store(session=_make_session(SESSION_ID))
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}")
        assert resp.status_code == 200

    def test_returns_session_json(self):
        session = _make_session(SESSION_ID)
        store = _mock_store(session=session)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}")
        body = resp.json()
        assert body["session_id"] == SESSION_ID
        assert body["status"] == "setup"

    def test_returns_404_when_not_found(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}")
        assert resp.status_code == 404

    def test_404_uses_error_schema(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}")
        body = resp.json()
        assert "error" in body
        assert SESSION_ID in body["error"]


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/pages/{page_number}
# ---------------------------------------------------------------------------


class TestGetPage:
    def setup_method(self):
        _clear_overrides()

    def test_returns_200_when_page_exists(self):
        store = _mock_store(page=_make_page(1))
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1")
        assert resp.status_code == 200

    def test_returns_page_json(self):
        page = _make_page(3)
        store = _mock_store(page=page)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/3")
        body = resp.json()
        assert body["page_number"] == 3
        assert body["beat"] == page.beat

    def test_returns_404_when_page_not_generated(self):
        store = _mock_store(page=None)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/2")
        assert resp.status_code == 404

    def test_404_page_uses_error_schema(self):
        store = _mock_store(page=None)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/2")
        body = resp.json()
        assert "error" in body

    def test_returns_404_when_session_not_found(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1")
        assert resp.status_code == 404

    def test_calls_get_page_with_correct_args(self):
        store = _mock_store(page=_make_page(4))
        client = _override(store)
        client.get(f"/sessions/{SESSION_ID}/pages/4")
        store.get_page.assert_called_once_with(SESSION_ID, 4)


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/pages/{page_number}/assets
# ---------------------------------------------------------------------------


class TestListPageAssets:
    def setup_method(self):
        _clear_overrides()

    def test_returns_200(self):
        store = _mock_store(assets=[])
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets")
        assert resp.status_code == 200

    def test_returns_page_number_and_assets(self):
        asset = _make_asset(page_number=2)
        store = _mock_store(assets=[asset])
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/2/assets")
        body = resp.json()
        assert body["page_number"] == 2
        assert isinstance(body["assets"], list)
        assert len(body["assets"]) == 1

    def test_empty_assets_list_is_valid(self):
        store = _mock_store(assets=[])
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets")
        body = resp.json()
        assert body["assets"] == []

    def test_returns_404_when_session_not_found(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets")
        assert resp.status_code == 404

    def test_calls_list_page_assets_with_correct_args(self):
        store = _mock_store(assets=[])
        client = _override(store)
        client.get(f"/sessions/{SESSION_ID}/pages/3/assets")
        store.list_page_assets.assert_called_once_with(SESSION_ID, 3)


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/pages/{page_number}/assets/{asset_type}
# ---------------------------------------------------------------------------


class TestGetPageAsset:
    def setup_method(self):
        _clear_overrides()

    def test_returns_200_when_asset_exists(self):
        asset = _make_asset(asset_type=AssetType.illustration)
        store = _mock_store(asset=asset)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets/illustration")
        assert resp.status_code == 200

    def test_returns_asset_json(self):
        asset = _make_asset(page_number=1, asset_type=AssetType.narration)
        store = _mock_store(asset=asset)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets/narration")
        body = resp.json()
        assert body["asset_type"] == "narration"
        assert body["generation_status"] == "ready"

    def test_returns_404_when_asset_missing(self):
        store = _mock_store(asset=None)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets/illustration")
        assert resp.status_code == 404

    def test_404_asset_uses_error_schema(self):
        store = _mock_store(asset=None)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets/illustration")
        body = resp.json()
        assert "error" in body

    def test_returns_404_when_session_not_found(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets/illustration")
        assert resp.status_code == 404

    def test_invalid_asset_type_returns_422(self):
        store = _mock_store()
        client = _override(store)
        resp = client.get(f"/sessions/{SESSION_ID}/pages/1/assets/video")
        assert resp.status_code == 422

    def test_calls_get_page_asset_with_correct_args(self):
        asset = _make_asset(page_number=2, asset_type=AssetType.narration)
        store = _mock_store(asset=asset)
        client = _override(store)
        client.get(f"/sessions/{SESSION_ID}/pages/2/assets/narration")
        store.get_page_asset.assert_called_once_with(SESSION_ID, 2, AssetType.narration)


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/voice-commands
# ---------------------------------------------------------------------------

VOICE_COMMAND_PAYLOAD = {
    "turn_id": str(uuid.uuid4()),
    "raw_transcript": "Make it more exciting",
    "interpreted_intent": "increase pacing",
    "command_type": "pacing_change",
    "applied_to_pages": [3, 4, 5],
    "safe": True,
}


class TestCreateVoiceCommand:
    def setup_method(self):
        _clear_overrides()

    def test_returns_201(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        assert resp.status_code == 201

    def test_returns_voice_command_json(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        body = resp.json()
        assert body["command_type"] == "pacing_change"
        assert body["raw_transcript"] == "Make it more exciting"
        assert body["applied_to_pages"] == [3, 4, 5]
        assert body["safe"] is True

    def test_returns_command_id(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        body = resp.json()
        assert "command_id" in body
        uuid.UUID(body["command_id"])

    def test_calls_save_voice_command(self):
        store = _mock_store()
        client = _override(store)
        client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        store.save_voice_command.assert_called_once()

    def test_save_called_with_correct_session_id(self):
        store = _mock_store()
        client = _override(store)
        client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        call_session_id = store.save_voice_command.call_args[0][0]
        assert call_session_id == SESSION_ID

    def test_returns_404_when_session_not_found(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        assert resp.status_code == 404

    def test_404_uses_error_schema(self):
        store = _mock_store(session_error=True)
        client = _override(store)
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=VOICE_COMMAND_PAYLOAD,
        )
        body = resp.json()
        assert "error" in body

    def test_returns_422_on_missing_required_fields(self):
        store = _mock_store()
        client = _override(store)
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json={"raw_transcript": "only this"},
        )
        assert resp.status_code == 422

    def test_optional_fields_have_defaults(self):
        store = _mock_store()
        client = _override(store)
        minimal = {
            "turn_id": str(uuid.uuid4()),
            "raw_transcript": "hello",
            "interpreted_intent": "greeting",
            "command_type": "tone_change",
        }
        resp = client.post(
            f"/sessions/{SESSION_ID}/voice-commands",
            json=minimal,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["applied_to_pages"] == []
        assert body["safe"] is True
