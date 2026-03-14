"""
Sessions router — REST endpoints for session lifecycle and voice commands.

Endpoints:
    POST   /sessions                          create session → {session_id, ws_url}
    GET    /sessions/{session_id}             fetch full Session document
    POST   /sessions/{session_id}/voice-commands  stub voice command (no generation)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.dependencies import get_store
from app.exceptions import SessionNotFoundError
from app.models.session import Session
from app.models.voice_command import CommandType, VoiceCommand
from app.schemas import ErrorResponse
from app.services.session_store import SessionStore
from pydantic import BaseModel

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class CreateSessionResponse(BaseModel):
    session_id: str
    ws_url: str


class VoiceCommandCreate(BaseModel):
    """Request body for POST /sessions/{session_id}/voice-commands."""

    turn_id: UUID
    raw_transcript: str
    interpreted_intent: str
    command_type: CommandType
    applied_to_pages: list[int] = []
    new_character_ref_id: Optional[str] = None
    safe: bool = True
    safety_decision_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# POST /sessions
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateSessionResponse,
    summary="Create a new story session",
)
async def create_session(
    request: Request,
    store: SessionStore = Depends(get_store),
) -> CreateSessionResponse:
    """
    Creates a Session document (status=setup) and returns the session ID
    plus the WebSocket URL the client should connect to.
    """
    now = datetime.now(timezone.utc)
    session = Session(created_at=now, updated_at=now)
    await store.create_session(session)

    host = request.headers.get("host", "localhost:8000")
    session_id = str(session.session_id)
    ws_url = f"wss://{host}/ws/story/{session_id}"
    return CreateSessionResponse(session_id=session_id, ws_url=ws_url)


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{session_id}",
    response_model=Session,
    responses={404: {"model": ErrorResponse}},
    summary="Get session by ID",
)
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> Session:
    try:
        return await store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# POST /sessions/{session_id}/voice-commands
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/voice-commands",
    status_code=status.HTTP_201_CREATED,
    response_model=VoiceCommand,
    responses={404: {"model": ErrorResponse}},
    summary="Submit a voice command (stub — no generation)",
)
async def create_voice_command(
    session_id: str,
    body: VoiceCommandCreate,
    store: SessionStore = Depends(get_store),
) -> VoiceCommand:
    """
    Validates that the session exists, persists the VoiceCommand, and returns it.
    Generation / command routing is a stub in T-011; wired up in later tasks.
    """
    try:
        await store.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    cmd = VoiceCommand(
        turn_id=body.turn_id,
        raw_transcript=body.raw_transcript,
        interpreted_intent=body.interpreted_intent,
        command_type=body.command_type,
        applied_to_pages=body.applied_to_pages,
        new_character_ref_id=body.new_character_ref_id,
        safe=body.safe,
        safety_decision_id=body.safety_decision_id,
        received_at=datetime.now(timezone.utc),
    )
    await store.save_voice_command(session_id, cmd)
    return cmd
