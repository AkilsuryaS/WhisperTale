"""
test_t032_page_history_accumulation.py

Unit tests for T-032: Page history accumulation.

Tests cover:
  1. page_history has length N after page N completes
  2. expand_page call for page 3 receives page_history of length 2
  3. page_history is seeded from Session.page_history on reconnect (initial_page_history)
  4. First-25-word snippet is extracted correctly from page text
  5. Session.page_history persisted via update_page_history after each page
  6. page_history not updated when page text is empty
  7. Session model accepts page_history field
  8. story_complete remains the final event after history accumulation

Depends: T-026, T-032
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.page import Page, PageStatus
from app.models.session import Session, SessionStatus
from app.websocket.story_ws import _PageLoopState, _page_generation_loop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    arc: list[str] | None = None,
    page_history: list[str] | None = None,
    status: SessionStatus = SessionStatus.generating,
) -> Session:
    return Session(
        story_arc=arc or [f"beat {i}" for i in range(1, 6)],
        page_history=page_history or [],
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_page(page_number: int, text: str) -> Page:
    p = Page(
        page_number=page_number,
        beat=f"beat {page_number}",
        status=PageStatus.complete,
    )
    p.text = text
    return p


def _long_text(word_count: int = 60) -> str:
    return " ".join(f"word{i}" for i in range(word_count))


def _make_store(
    session: Session,
    pages: dict[int, Page | None] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.get_session = AsyncMock(return_value=session)
    store.get_page = AsyncMock(
        side_effect=lambda sid, pn: (pages or {}).get(pn)
    )
    store.update_page_history = AsyncMock()
    store.update_session_status = AsyncMock()
    return store


async def _run_loop(
    store: MagicMock,
    fake_run_page,
    initial_page_history: list[str] | None = None,
) -> MagicMock:
    """Run _page_generation_loop with a patched page_orchestrator.run_page."""
    import app.websocket.page_orchestrator as po

    ws = MagicMock()
    ws.send_json = AsyncMock()

    orig = po.run_page
    po.run_page = fake_run_page  # type: ignore[assignment]
    try:
        await _page_generation_loop(
            ws=ws,
            session_id="test-session",
            store=store,
            story_planner=MagicMock(),
            character_bible_svc=MagicMock(),
            image_svc=MagicMock(),
            tts_svc=MagicMock(),
            media_svc=MagicMock(),
            steering_window_seconds=0,
            page_loop_state=_PageLoopState(),
            initial_page_history=initial_page_history,
        )
    finally:
        po.run_page = orig  # type: ignore[assignment]

    return ws


def _make_fake_run_page(histories_at_call: dict[int, list[str]] | None = None):
    """Factory: returns a fake run_page coroutine that records page_history per page."""

    async def fake_run_page(
        session_id,
        page_number,
        beat,
        page_history,
        emit,
        story_planner,
        character_bible_svc,
        image_svc,
        tts_svc,
        media_svc,
        session_store,
    ):
        if histories_at_call is not None:
            histories_at_call[page_number] = list(page_history)
        await emit("page_complete", page=page_number)

    return fake_run_page


# ---------------------------------------------------------------------------
# T-032: Session model tests
# ---------------------------------------------------------------------------


class TestSessionModelPageHistory:
    """Session model carries page_history field (T-032)."""

    def test_default_page_history_is_empty(self) -> None:
        session = _make_session()
        assert session.page_history == []

    def test_accepts_populated_page_history(self) -> None:
        history = ["First page snippet.", "Second page snippet."]
        session = _make_session(page_history=history)
        assert session.page_history == history

    def test_page_history_serialises_in_model_dump(self) -> None:
        history = ["Some page text here."]
        session = _make_session(page_history=history)
        data = session.model_dump(mode="json")
        assert data["page_history"] == history


# ---------------------------------------------------------------------------
# T-032: First-25-word snippet extraction
# ---------------------------------------------------------------------------


class TestTwentyFiveWordSnippet:
    """First 25 words extracted from page text."""

    def test_exactly_25_words_kept_from_long_text(self) -> None:
        text = _long_text(60)
        snippet = " ".join(text.split()[:25])
        assert len(snippet.split()) == 25

    def test_short_text_uses_all_available_words(self) -> None:
        text = "Short text with only six words."
        snippet = " ".join(text.split()[:25])
        assert snippet == text.strip()

    def test_exactly_25_word_text_is_unchanged(self) -> None:
        text = " ".join(f"w{i}" for i in range(25))
        snippet = " ".join(text.split()[:25])
        assert snippet == text


# ---------------------------------------------------------------------------
# T-032: Loop accumulation behaviour
# ---------------------------------------------------------------------------


class TestPageHistoryAccumulation:

    @pytest.mark.asyncio
    async def test_page1_receives_empty_history(self) -> None:
        """Page 1 expand_page call receives empty history."""
        histories: dict[int, list[str]] = {}
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(store, _make_fake_run_page(histories))
        assert histories[1] == []

    @pytest.mark.asyncio
    async def test_page2_receives_history_of_length_1(self) -> None:
        """Page 2 expand_page call receives exactly 1 history entry."""
        histories: dict[int, list[str]] = {}
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(store, _make_fake_run_page(histories))
        assert len(histories[2]) == 1

    @pytest.mark.asyncio
    async def test_page3_receives_history_of_length_2(self) -> None:
        """expand_page call for page 3 receives page_history of length 2 (spec)."""
        histories: dict[int, list[str]] = {}
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(store, _make_fake_run_page(histories))
        assert len(histories[3]) == 2

    @pytest.mark.asyncio
    async def test_page5_receives_history_of_length_4(self) -> None:
        """Page 5 expand_page call receives exactly 4 history entries."""
        histories: dict[int, list[str]] = {}
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(store, _make_fake_run_page(histories))
        assert len(histories[5]) == 4

    @pytest.mark.asyncio
    async def test_update_page_history_called_once_per_page(self) -> None:
        """store.update_page_history is called exactly 5 times — once per page."""
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(store, _make_fake_run_page())
        assert store.update_page_history.call_count == 5

    @pytest.mark.asyncio
    async def test_snippet_is_first_25_words_of_page_text(self) -> None:
        """Persisted history entry for page 1 contains first 25 words of its text."""
        long_text = _long_text(60)
        expected_snippet = " ".join(long_text.split()[:25])
        first_call_snapshot: list[list[str]] = []

        async def capturing_update(sid: str, history: list[str]) -> None:
            if not first_call_snapshot:
                first_call_snapshot.append(list(history))

        pages = {i: _make_page(i, long_text) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        store.update_page_history = AsyncMock(side_effect=capturing_update)
        await _run_loop(store, _make_fake_run_page())

        assert first_call_snapshot[0][0] == expected_snippet

    @pytest.mark.asyncio
    async def test_update_page_history_list_grows_monotonically(self) -> None:
        """Each successive update_page_history call receives a longer list."""
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        history_snapshots: list[list[str]] = []

        async def capturing_update(sid: str, history: list[str]) -> None:
            history_snapshots.append(list(history))  # snapshot at call time

        store = _make_store(_make_session(), pages)
        store.update_page_history = AsyncMock(side_effect=capturing_update)
        await _run_loop(store, _make_fake_run_page())

        assert len(history_snapshots) == 5
        for i, snapshot in enumerate(history_snapshots):
            assert len(snapshot) == i + 1

    @pytest.mark.asyncio
    async def test_history_not_updated_for_empty_page_text(self) -> None:
        """No history entry added and update_page_history skipped for empty page text."""
        pages: dict[int, Page | None] = {
            1: _make_page(1, ""),  # empty text — should not add entry
            **{i: _make_page(i, _long_text(60)) for i in range(2, 6)},
        }
        history_lengths: list[int] = []

        async def capturing_update(sid: str, history: list[str]) -> None:
            history_lengths.append(len(history))

        store = _make_store(_make_session(), pages)
        store.update_page_history = AsyncMock(side_effect=capturing_update)
        await _run_loop(store, _make_fake_run_page())

        # Page 1 had empty text → skipped; pages 2–5 produce 4 calls
        assert store.update_page_history.call_count == 4
        # After page 2: history length is 1 (only page 2 snippet)
        assert history_lengths[0] == 1

    @pytest.mark.asyncio
    async def test_initial_page_history_seeds_first_call(self) -> None:
        """initial_page_history is passed directly to page 1 expand_page (reconnect)."""
        pre_existing = ["Reconnect snippet 1.", "Reconnect snippet 2."]
        histories: dict[int, list[str]] = {}
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(
            store, _make_fake_run_page(histories), initial_page_history=pre_existing
        )
        assert histories[1] == pre_existing

    @pytest.mark.asyncio
    async def test_initial_history_combined_with_new_entries_for_page2(self) -> None:
        """After reconnect, page 2 receives seeded history + new page 1 snippet."""
        pre_existing = ["Old page 1 snippet."]
        histories: dict[int, list[str]] = {}
        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)
        await _run_loop(
            store, _make_fake_run_page(histories), initial_page_history=pre_existing
        )
        assert len(histories[2]) == 2
        assert histories[2][0] == "Old page 1 snippet."

    @pytest.mark.asyncio
    async def test_story_complete_is_final_event(self) -> None:
        """story_complete is still the last WS event emitted after T-032 changes."""
        import app.websocket.page_orchestrator as po

        pages = {i: _make_page(i, _long_text(60)) for i in range(1, 6)}
        store = _make_store(_make_session(), pages)

        emitted: list[str] = []

        async def tracking_run_page(
            session_id,
            page_number,
            beat,
            page_history,
            emit,
            story_planner,
            character_bible_svc,
            image_svc,
            tts_svc,
            media_svc,
            session_store,
        ):
            await emit("page_complete", page=page_number)

        ws = MagicMock()

        async def capturing_send_json(payload: dict) -> None:
            emitted.append(payload.get("type", ""))

        ws.send_json = AsyncMock(side_effect=capturing_send_json)

        orig = po.run_page
        po.run_page = tracking_run_page  # type: ignore[assignment]
        try:
            await _page_generation_loop(
                ws=ws,
                session_id="test-final",
                store=store,
                story_planner=MagicMock(),
                character_bible_svc=MagicMock(),
                image_svc=MagicMock(),
                tts_svc=MagicMock(),
                media_svc=MagicMock(),
                steering_window_seconds=0,
                page_loop_state=_PageLoopState(),
            )
        finally:
            po.run_page = orig  # type: ignore[assignment]

        assert emitted[-1] == "story_complete"
