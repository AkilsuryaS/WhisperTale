"""
SessionStore — Firestore-backed persistence for Session, StoryBrief, and UserTurn.

Firestore paths (data-model.md):
    sessions/{session_id}                       ← Session document
    sessions/{session_id}/story_brief/main      ← StoryBrief document
    sessions/{session_id}/turns/{turn_id}       ← UserTurn document

All public methods accept and return Pydantic models; Firestore
serialisation/deserialisation is encapsulated here.

Usage:
    store = SessionStore()
    await store.create_session(session)
    session = await store.get_session(str(session_id))
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.async_client import AsyncClient

from app.config import settings
from app.exceptions import SessionNotFoundError
from app.models.session import Session, SessionStatus, StoryBrief, UserTurn


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _firestore_client() -> AsyncClient:
    """Return an async Firestore client using project from settings."""
    return firestore.AsyncClient(
        project=settings.require_gcp("SessionStore"),
        database=settings.FIRESTORE_DATABASE,
    )


def _to_firestore(model: Any) -> dict:
    """Serialise a Pydantic model to a Firestore-compatible dict.

    Uses mode='json' so UUIDs become strings and datetimes become ISO-8601
    strings, which Firestore stores and returns as plain strings.
    """
    return model.model_dump(mode="json")


class SessionStore:
    """
    Async CRUD store for Session, StoryBrief, and UserTurn documents.

    One instance per request is fine; the underlying Firestore client is
    thread-safe and reuses gRPC connections.
    """

    def __init__(self, client: AsyncClient | None = None) -> None:
        # Allow injecting a mock client in tests
        self._client = client or _firestore_client()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_ref(self, session_id: str):
        return self._client.collection("sessions").document(session_id)

    def _story_brief_ref(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("story_brief")
            .document("main")
        )

    def _turn_ref(self, session_id: str, turn_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("turns")
            .document(turn_id)
        )

    def _turns_collection(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("turns")
        )

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def create_session(self, session: Session) -> None:
        """Write a new session document. Overwrites if already exists."""
        data = _to_firestore(session)
        await self._session_ref(str(session.session_id)).set(data)

    async def get_session(self, session_id: str) -> Session:
        """
        Return the Session for session_id.
        Raises SessionNotFoundError if the document does not exist.
        """
        doc = await self._session_ref(session_id).get()
        if not doc.exists:
            raise SessionNotFoundError(session_id)
        return Session.model_validate(doc.to_dict())

    async def update_session_status(
        self, session_id: str, status: SessionStatus
    ) -> None:
        """Update status and updated_at on an existing session."""
        ref = self._session_ref(session_id)
        doc = await ref.get()
        if not doc.exists:
            raise SessionNotFoundError(session_id)
        await ref.update(
            {
                "status": status if isinstance(status, str) else status.value,
                "updated_at": _utc_now().isoformat(),
            }
        )

    async def update_story_arc(self, session_id: str, arc: list[str]) -> None:
        """Replace story_arc and bump updated_at on an existing session."""
        ref = self._session_ref(session_id)
        doc = await ref.get()
        if not doc.exists:
            raise SessionNotFoundError(session_id)
        await ref.update(
            {
                "story_arc": arc,
                "updated_at": _utc_now().isoformat(),
            }
        )

    # ------------------------------------------------------------------
    # StoryBrief
    # ------------------------------------------------------------------

    async def save_story_brief(self, session_id: str, brief: StoryBrief) -> None:
        """Write (or overwrite) the StoryBrief for a session."""
        data = _to_firestore(brief)
        await self._story_brief_ref(session_id).set(data)

    async def get_story_brief(self, session_id: str) -> StoryBrief | None:
        """
        Return the StoryBrief for session_id, or None if it has not been
        saved yet.  Does NOT raise SessionNotFoundError — callers that need
        the session to exist should call get_session first.
        """
        doc = await self._story_brief_ref(session_id).get()
        if not doc.exists:
            return None
        return StoryBrief.model_validate(doc.to_dict())

    # ------------------------------------------------------------------
    # UserTurn
    # ------------------------------------------------------------------

    async def save_user_turn(self, session_id: str, turn: UserTurn) -> None:
        """Write (or overwrite) a UserTurn document."""
        data = _to_firestore(turn)
        await self._turn_ref(session_id, str(turn.turn_id)).set(data)

    async def list_user_turns(self, session_id: str) -> list[UserTurn]:
        """
        Return all UserTurns for session_id, ordered by sequence ascending.
        Returns an empty list if no turns have been saved yet.
        """
        query = self._turns_collection(session_id).order_by("sequence")
        docs = await query.get()
        return [UserTurn.model_validate(doc.to_dict()) for doc in docs]
