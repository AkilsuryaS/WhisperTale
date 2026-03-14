"""
Pages router — REST endpoints for story pages and their assets.

Endpoints:
    GET  /sessions/{session_id}/pages/{page_number}
    GET  /sessions/{session_id}/pages/{page_number}/assets
    GET  /sessions/{session_id}/pages/{page_number}/assets/{asset_type}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.dependencies import get_store
from app.exceptions import SessionNotFoundError
from app.models.page import AssetType, Page, PageAsset
from app.schemas import ErrorResponse
from app.services.session_store import SessionStore

router = APIRouter(prefix="/sessions", tags=["pages"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PageAssetsResponse(BaseModel):
    page_number: int
    assets: list[PageAsset]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_session(session_id: str, store: SessionStore) -> None:
    """Raise 404 if the session does not exist."""
    try:
        await store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


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
