"""
Pages router — REST endpoints for story pages and their assets.

Endpoints:
    GET  /sessions/{session_id}/pages/{page_number}
    GET  /sessions/{session_id}/pages/{page_number}/assets
    GET  /sessions/{session_id}/pages/{page_number}/assets/{asset_type}
    POST /sessions/{session_id}/pages/generate
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel

from app.dependencies import (
    get_character_bible_svc,
    get_image_svc,
    get_media_svc,
    get_story_planner,
    get_store,
    get_tts_svc,
)
from app.exceptions import SessionNotFoundError
from app.models.page import AssetType, Page, PageAsset, PageStatus
from app.models.session import SessionStatus
from app.schemas import ErrorResponse
from app.services.character_bible_service import CharacterBibleService
from app.services.image_generation import ImageGenerationService
from app.services.media_persistence import MediaPersistenceService
from app.services.session_store import SessionStore
from app.services.story_planner import StoryPlannerService
from app.services.tts_service import TTSService

router = APIRouter(prefix="/sessions", tags=["pages"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PageAssetsResponse(BaseModel):
    page_number: int
    assets: list[PageAsset]


class GeneratePageResponse(BaseModel):
    session_id: str
    page_number: int
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_session(session_id: str, store: SessionStore):
    """Raise 404 if the session does not exist; return the Session."""
    try:
        return await store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


def _make_emit(ws_emit_list: list):
    """Return a no-op emit function for background page generation (no WS context)."""

    async def _emit(event_type: str, **fields) -> None:
        ws_emit_list.append({"type": event_type, **fields})

    return _emit


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/pages/{page_number}
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/pages/{page_number}",
    response_model=Page,
    responses={404: {"model": ErrorResponse}},
    summary="Get a single story page",
)
async def get_page(
    session_id: str,
    page_number: int,
    store: SessionStore = Depends(get_store),
) -> Page:
    await _require_session(session_id, store)

    page = await store.get_page(session_id, page_number)
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Page {page_number} not yet generated for session {session_id}",
        )
    return page


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/pages/{page_number}/assets
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/pages/{page_number}/assets",
    response_model=PageAssetsResponse,
    responses={404: {"model": ErrorResponse}},
    summary="List all assets for a page",
)
async def list_page_assets(
    session_id: str,
    page_number: int,
    store: SessionStore = Depends(get_store),
) -> PageAssetsResponse:
    await _require_session(session_id, store)

    assets = await store.list_page_assets(session_id, page_number)
    return PageAssetsResponse(page_number=page_number, assets=assets)


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/pages/{page_number}/assets/{asset_type}
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}/pages/{page_number}/assets/{asset_type}",
    response_model=PageAsset,
    responses={404: {"model": ErrorResponse}},
    summary="Get a single page asset",
)
async def get_page_asset(
    session_id: str,
    page_number: int,
    asset_type: AssetType,
    store: SessionStore = Depends(get_store),
) -> PageAsset:
    await _require_session(session_id, store)

    asset = await store.get_page_asset(session_id, page_number, asset_type)
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Asset '{asset_type}' not found for page {page_number} "
                f"in session {session_id}"
            ),
        )
    return asset


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/pages/generate
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/pages/generate",
    response_model=GeneratePageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    summary="Enqueue next page generation",
)
async def generate_next_page(
    session_id: str,
    background_tasks: BackgroundTasks,
    store: SessionStore = Depends(get_store),
    story_planner: StoryPlannerService = Depends(get_story_planner),
    character_bible_svc: CharacterBibleService = Depends(get_character_bible_svc),
    image_svc: ImageGenerationService = Depends(get_image_svc),
    tts_svc: TTSService = Depends(get_tts_svc),
    media_svc: MediaPersistenceService = Depends(get_media_svc),
) -> GeneratePageResponse:
    """
    Validate preconditions and enqueue generation of the next page.

    Preconditions:
    - Session must exist (404 otherwise).
    - Session.status must be ``generating`` (409 otherwise).
    - No page must currently be in-progress / pending state (409 if steering
      window is open — i.e. the most recently saved page is still pending).

    Returns 202 with {session_id, page_number, status: "generating"} and
    enqueues ``run_page`` as a background task.
    """
    from app.websocket.page_orchestrator import run_page

    session = await _require_session(session_id, store)

    # Check session status
    if session.status != SessionStatus.generating:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session {session_id} is not in 'generating' status "
                f"(current: {session.status})"
            ),
        )

    # Determine next page number
    next_page = session.current_page + 1
    if next_page > 5:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"All 5 pages already generated for session {session_id}",
        )

    # Check for open steering window: if the current page is still pending/generating
    if session.current_page > 0:
        current_page_doc = await store.get_page(session_id, session.current_page)
        if current_page_doc is not None and current_page_doc.status in (
            PageStatus.pending,
            PageStatus.assets_generating,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Steering window is open for page {session.current_page}; "
                    "cannot start next page yet"
                ),
            )

    # Retrieve story arc to get the beat for this page
    if not session.story_arc or len(session.story_arc) < next_page:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Story arc not ready for page {next_page}",
        )

    beat = session.story_arc[next_page - 1]

    # Build page_history from previously completed pages
    page_history: list[str] = []
    for pn in range(1, next_page):
        pg = await store.get_page(session_id, pn)
        if pg is not None and pg.text:
            # Use a one-sentence summary: first sentence of the page text
            sentences = pg.text.split(".")
            summary = sentences[0].strip() + "." if sentences else pg.text
            page_history.append(summary)

    # Emit sink for background REST-triggered generation (no WebSocket here)
    events: list[dict] = []
    emit_fn = _make_emit(events)

    async def _run_page_background() -> None:
        try:
            await run_page(
                session_id=session_id,
                page_number=next_page,
                beat=beat,
                page_history=page_history,
                emit=emit_fn,
                story_planner=story_planner,
                character_bible_svc=character_bible_svc,
                image_svc=image_svc,
                tts_svc=tts_svc,
                media_svc=media_svc,
                session_store=store,
            )
            # Advance current_page on the session document
            await store.update_story_arc(session_id, session.story_arc)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).error(
                "Background page generation failed (session=%s, page=%d): %s",
                session_id,
                next_page,
                exc,
            )

    background_tasks.add_task(_run_page_background)

    return GeneratePageResponse(
        session_id=session_id,
        page_number=next_page,
        status="generating",
    )
