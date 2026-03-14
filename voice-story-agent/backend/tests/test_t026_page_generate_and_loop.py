"""
Tests for T-026: POST /sessions/{id}/pages/generate REST endpoint
and WebSocket _page_generation_loop.

Strategy
--------
All external services are mocked. FastAPI's TestClient is used for REST tests.
The page generation loop is tested via direct coroutine calls with mocked
services and a zero-length steering window.

T026-REST tests — POST /sessions/{id}/pages/generate:
    T26R-01  returns 202 with {session_id, page_number, status:"generating"}
    T26R-02  returns 404 for unknown session_id
    T26R-03  returns 409 if session status is not "generating"
    T26R-04  returns 409 if all 5 pages already generated (current_page=5)
    T26R-05  returns 409 if steering window is open (current page still pending)
    T26R-06  returns 409 if story arc is not ready for next page
    T26R-07  page_number in response equals current_page + 1

T026-LOOP tests — _page_generation_loop:
    T26L-01  happy path: story_complete emitted as final event after 5 pages
    T26L-02  session status set to "complete" after loop completes
    T26L-03  run_page called 5 times (once per page)
    T26L-04  story_complete event carries session_id
    T26L-05  steering_window_open emitted between pages (4 times for 5 pages)
    T26L-06  steering_window_closed emitted between pages (4 times for 5 pages)
    T26L-07  loop aborts gracefully when session not found
    T26L-08  loop aborts gracefully when story arc too short
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.page import Page, PageStatus
from app.models.session import Session, SessionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = str(uuid4())
STORY_ARC = [
    "Pip discovers a hidden door.",
    "Pip enters a magical world.",
    "Pip meets a friendly dragon.",
    "Pip and dragon go on a quest.",
    "Pip returns home with a gift.",
]


def _make_session(
    *,
    session_id: str = SESSION_ID,
    status: SessionStatus = SessionStatus.generating,
    current_page: int = 0,
    story_arc: list[str] | None = None,
) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id=session_id,
        status=status,
        created_at=now,
        updated_at=now,
        current_page=current_page,
        story_arc=story_arc if story_arc is not None else STORY_ARC,
    )


def _make_page(page_number: int, status: PageStatus = PageStatus.complete) -> Page:
    return Page(
        page_number=page_number,
        beat=STORY_ARC[page_number - 1],
        status=status,
        text=f"Page {page_number} text. Pip went further.",
    )


# ---------------------------------------------------------------------------
# REST endpoint tests — POST /sessions/{id}/pages/generate
# ---------------------------------------------------------------------------


class TestGenerateNextPageEndpoint:
    def _override(self, session: Session, pages: dict[int, Page | None] | None = None):
        """Apply dependency overrides and return a context-managed cleanup."""
        from app.dependencies import (
            get_character_bible_svc,
            get_image_svc,
            get_media_svc,
            get_story_planner,
            get_store,
            get_tts_svc,
        )

        mock_store = MagicMock()
        mock_store.get_session = AsyncMock(return_value=session)
        mock_store.get_page = AsyncMock(
            side_effect=lambda sid, pn: (pages or {}).get(pn)
        )
        mock_store.save_page = AsyncMock()
        mock_store.update_story_arc = AsyncMock()

        app.dependency_overrides[get_store] = lambda: mock_store
        app.dependency_overrides[get_story_planner] = lambda: MagicMock()
        app.dependency_overrides[get_character_bible_svc] = lambda: MagicMock()
        app.dependency_overrides[get_image_svc] = lambda: MagicMock()
        app.dependency_overrides[get_tts_svc] = lambda: MagicMock()
        app.dependency_overrides[get_media_svc] = lambda: MagicMock()
        return mock_store

    def _clear(self) -> None:
        from app.dependencies import (
            get_character_bible_svc,
            get_image_svc,
            get_media_svc,
            get_story_planner,
            get_store,
            get_tts_svc,
        )
        for dep in (
            get_store,
            get_story_planner,
            get_character_bible_svc,
            get_image_svc,
            get_tts_svc,
            get_media_svc,
        ):
            app.dependency_overrides.pop(dep, None)

    def test_returns_202_generating(self) -> None:
        """T26R-01: returns 202 with correct response body."""
        session = _make_session(current_page=0)
        self._override(session)
        try:
            with patch("fastapi.BackgroundTasks.add_task"):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(f"/sessions/{SESSION_ID}/pages/generate")
        finally:
            self._clear()

        assert resp.status_code == 202
        body = resp.json()
        assert body["session_id"] == SESSION_ID
        assert body["page_number"] == 1
        assert body["status"] == "generating"

    def test_returns_404_unknown_session(self) -> None:
        """T26R-02: returns 404 for unknown session_id."""
        from app.dependencies import get_store
        from app.exceptions import SessionNotFoundError

        mock_store = MagicMock()
        mock_store.get_session = AsyncMock(
            side_effect=SessionNotFoundError("unknown-session")
        )
        app.dependency_overrides[get_store] = lambda: mock_store
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/sessions/unknown-session/pages/generate")
        finally:
            app.dependency_overrides.pop(get_store, None)

        assert resp.status_code == 404

    def test_returns_409_if_not_generating_status(self) -> None:
        """T26R-03: returns 409 if session status is not 'generating'."""
        session = _make_session(status=SessionStatus.setup)
        self._override(session)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(f"/sessions/{SESSION_ID}/pages/generate")
        finally:
            self._clear()

        assert resp.status_code == 409
        assert "generating" in resp.json()["error"].lower()

    def test_returns_409_if_all_pages_done(self) -> None:
        """T26R-04: returns 409 if current_page=5 (all pages generated)."""
        session = _make_session(current_page=5)
        self._override(session)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(f"/sessions/{SESSION_ID}/pages/generate")
        finally:
            self._clear()

        assert resp.status_code == 409
        assert "5" in resp.json()["error"]

    def test_returns_409_if_steering_window_open(self) -> None:
        """T26R-05: returns 409 if current page is still pending (steering window open)."""
        session = _make_session(current_page=1)
        pending_page = _make_page(1, status=PageStatus.pending)
        self._override(session, pages={1: pending_page})
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(f"/sessions/{SESSION_ID}/pages/generate")
        finally:
            self._clear()

        assert resp.status_code == 409
        assert "steering" in resp.json()["error"].lower()

    def test_returns_409_if_arc_not_ready(self) -> None:
        """T26R-06: returns 409 if story arc is empty."""
        session = _make_session(current_page=0, story_arc=[])
        self._override(session)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(f"/sessions/{SESSION_ID}/pages/generate")
        finally:
            self._clear()

        assert resp.status_code == 409

    def test_page_number_is_current_plus_one(self) -> None:
        """T26R-07: page_number in response equals current_page + 1."""
        session = _make_session(current_page=2, story_arc=STORY_ARC)
        pages = {1: _make_page(1), 2: _make_page(2)}
        self._override(session, pages=pages)
        try:
            with patch("fastapi.BackgroundTasks.add_task"):
                client = TestClient(app, raise_server_exceptions=False)
                resp = client.post(f"/sessions/{SESSION_ID}/pages/generate")
        finally:
            self._clear()

        assert resp.status_code == 202
        assert resp.json()["page_number"] == 3


# ---------------------------------------------------------------------------
# Page generation loop tests — _page_generation_loop
# ---------------------------------------------------------------------------


def _make_loop_services(pages_text: dict[int, str] | None = None):
    """Build mocked services for the page generation loop."""
    pages_text = pages_text or {pn: f"Page {pn}." for pn in range(1, 6)}

    mock_story_planner = MagicMock()
    mock_story_planner.expand_page = AsyncMock(
        side_effect=lambda beat, history, bible: (beat + " expanded.", beat + " narration.")
    )

    mock_character_bible_svc = MagicMock()
    mock_character_bible_svc.build_image_prompt = MagicMock(
        return_value=MagicMock(text_prompt="test", reference_urls=[])
    )
    mock_character_bible_svc.set_reference_image = AsyncMock()

    mock_image_svc = MagicMock()
    mock_image_svc.generate = AsyncMock(return_value=b"\x89PNG" + b"\x00" * 50)

    mock_tts_svc = MagicMock()
    mock_tts_svc.synthesize = AsyncMock(return_value=b"\xff\xfb" + b"\x00" * 50)

    mock_media_svc = MagicMock()
    mock_media_svc.store_illustration = AsyncMock(
        side_effect=lambda sid, pn, _: f"gs://bucket/pages/{pn}/illustration.png"
    )
    mock_media_svc.store_narration = AsyncMock(
        side_effect=lambda sid, pn, _: f"gs://bucket/pages/{pn}/narration.mp3"
    )
    mock_media_svc.get_signed_url = AsyncMock(
        side_effect=lambda uri: f"https://signed.example.com/{uri}"
    )

    from app.models.character_bible import (
        CharacterBible,
        ContentPolicy,
        ProtagonistProfile,
        StyleBible,
    )

    bible = CharacterBible(
        protagonist=ProtagonistProfile(
            name="Pip",
            species_or_type="rabbit",
            color="blue",
            notable_traits=["floppy ears", "small size"],
        ),
        style_bible=StyleBible(
            art_style="watercolour",
            color_palette="warm pastels",
            mood="cosy",
            negative_style_terms=["dark", "scary"],
        ),
        content_policy=ContentPolicy(exclusions=["no gore"]),
    )

    session = _make_session(current_page=0, story_arc=STORY_ARC)

    mock_store = MagicMock()
    mock_store.get_session = AsyncMock(return_value=session)
    mock_store.get_character_bible = AsyncMock(return_value=bible)
    mock_store.get_page = AsyncMock(return_value=None)
    mock_store.save_page = AsyncMock()
    mock_store.update_session_status = AsyncMock()

    return dict(
        story_planner=mock_story_planner,
        character_bible_svc=mock_character_bible_svc,
        image_svc=mock_image_svc,
        tts_svc=mock_tts_svc,
        media_svc=mock_media_svc,
        session_store=mock_store,
    )


class EventCapture:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event_type: str, **fields: Any) -> None:
        self.events.append({"type": event_type, **fields})

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def get(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e["type"] == event_type]

    def last(self) -> dict | None:
        return self.events[-1] if self.events else None


async def _run_loop(
    emit: EventCapture,
    svcs: dict,
    steering_window_seconds: float = 0.0,
) -> None:
    """Run the page generation loop with a zero-second steering window."""
    from app.websocket.story_ws import _page_generation_loop
    from app.services.tts_service import VoiceConfig

    ws = MagicMock()
    ws.send_json = AsyncMock(side_effect=lambda data: emit.events.append(data))

    fake_voice = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
    with patch(
        "app.websocket.page_orchestrator.default_voice_config",
        return_value=fake_voice,
    ):
        await _page_generation_loop(
            ws=ws,
            session_id=SESSION_ID,
            store=svcs["session_store"],
            story_planner=svcs["story_planner"],
            character_bible_svc=svcs["character_bible_svc"],
            image_svc=svcs["image_svc"],
            tts_svc=svcs["tts_svc"],
            media_svc=svcs["media_svc"],
            steering_window_seconds=steering_window_seconds,
        )


class TestPageGenerationLoop:
    @pytest.mark.anyio
    async def test_story_complete_is_final_event(self) -> None:
        """T26L-01: story_complete is emitted as the final event."""
        emit = EventCapture()
        svcs = _make_loop_services()

        await _run_loop(emit, svcs)

        assert emit.last() is not None
        assert emit.last()["type"] == "story_complete"

    @pytest.mark.anyio
    async def test_session_status_set_complete(self) -> None:
        """T26L-02: update_session_status called with 'complete' after loop."""
        emit = EventCapture()
        svcs = _make_loop_services()

        await _run_loop(emit, svcs)

        svcs["session_store"].update_session_status.assert_called_once()
        call_args = svcs["session_store"].update_session_status.call_args
        assert call_args[0][0] == SESSION_ID
        status_arg = call_args[0][1]
        status_str = status_arg if isinstance(status_arg, str) else status_arg.value
        assert status_str == "complete"

    @pytest.mark.anyio
    async def test_run_page_called_five_times(self) -> None:
        """T26L-03: run_page is called once for each of the 5 pages."""
        emit = EventCapture()
        svcs = _make_loop_services()

        page_complete_events = []
        orig_emit = emit.__call__

        async def capture(event_type: str, **fields: Any) -> None:
            await orig_emit(event_type, **fields)
            if event_type == "page_complete":
                page_complete_events.append(fields)

        emit.__class__.__call__ = capture  # type: ignore[method-assign]

        await _run_loop(emit, svcs)

        # 5 page_complete events means run_page was called 5 times
        page_complete_count = sum(1 for e in emit.events if e.get("type") == "page_complete")
        assert page_complete_count == 5

    @pytest.mark.anyio
    async def test_story_complete_carries_session_id(self) -> None:
        """T26L-04: story_complete event carries the session_id."""
        emit = EventCapture()
        svcs = _make_loop_services()

        await _run_loop(emit, svcs)

        story_complete = emit.get("story_complete")
        assert len(story_complete) == 1
        assert story_complete[0]["session_id"] == SESSION_ID

    @pytest.mark.anyio
    async def test_steering_window_open_emitted_four_times(self) -> None:
        """T26L-05: steering_window_open emitted 4 times (between 5 pages)."""
        emit = EventCapture()
        svcs = _make_loop_services()

        await _run_loop(emit, svcs, steering_window_seconds=0.0)

        open_events = emit.get("steering_window_open")
        assert len(open_events) == 4

    @pytest.mark.anyio
    async def test_steering_window_closed_emitted_four_times(self) -> None:
        """T26L-06: steering_window_closed emitted 4 times."""
        emit = EventCapture()
        svcs = _make_loop_services()

        await _run_loop(emit, svcs, steering_window_seconds=0.0)

        closed_events = emit.get("steering_window_closed")
        assert len(closed_events) == 4

    @pytest.mark.anyio
    async def test_loop_aborts_gracefully_on_session_not_found(self) -> None:
        """T26L-07: loop aborts without exception when session not found on first page."""
        from app.exceptions import SessionNotFoundError

        emit = EventCapture()
        svcs = _make_loop_services()
        svcs["session_store"].get_session = AsyncMock(
            side_effect=SessionNotFoundError(SESSION_ID)
        )

        # Should not raise — loop catches the error and breaks
        await _run_loop(emit, svcs)

        # No page events should have been emitted
        page_complete_count = sum(1 for e in emit.events if e.get("type") == "page_complete")
        assert page_complete_count == 0

    @pytest.mark.anyio
    async def test_loop_aborts_gracefully_on_short_arc(self) -> None:
        """T26L-08: loop aborts without exception when story arc is too short."""
        from app.models.session import Session

        emit = EventCapture()
        svcs = _make_loop_services()
        now = datetime.now(timezone.utc)

        # Arc with only 1 beat: page 1 succeeds, page 2 fails arc check
        short_arc_session = Session(
            session_id=SESSION_ID,
            status=SessionStatus.generating,
            created_at=now,
            updated_at=now,
            current_page=0,
            story_arc=["Only one beat."],
        )
        svcs["session_store"].get_session = AsyncMock(return_value=short_arc_session)

        # Should not raise
        await _run_loop(emit, svcs)

        # Only 1 page_complete (page 1 worked, page 2 aborted arc check)
        page_complete_count = sum(1 for e in emit.events if e.get("type") == "page_complete")
        assert page_complete_count == 1
