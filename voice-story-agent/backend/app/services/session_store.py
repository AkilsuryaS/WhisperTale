"""
SessionStore — Firestore-backed persistence for all session sub-documents.

Firestore paths (data-model.md):
    sessions/{session_id}                                       ← Session
    sessions/{session_id}/story_brief/main                      ← StoryBrief
    sessions/{session_id}/turns/{turn_id}                       ← UserTurn
    sessions/{session_id}/pages/{page_number}                   ← Page
    sessions/{session_id}/pages/{page_number}/assets/{type}     ← PageAsset
    sessions/{session_id}/voice_commands/{command_id}           ← VoiceCommand
    sessions/{session_id}/safety_decisions/{decision_id}        ← SafetyDecision
    sessions/{session_id}/character_bible/main                  ← CharacterBible
    sessions/{session_id}/style_bible/main                      ← StyleBible

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
from app.models.character_bible import CharacterBible, StyleBible
from app.models.page import AssetStatus, AssetType, Page, PageAsset
from app.models.safety import SafetyDecision
from app.models.session import Session, SessionStatus, StoryBrief, UserTurn
from app.models.voice_command import VoiceCommand


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

    def _page_ref(self, session_id: str, page_number: int):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("pages")
            .document(str(page_number))
        )

    def _pages_collection(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("pages")
        )

    def _asset_ref(self, session_id: str, page_number: int, asset_type: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("pages")
            .document(str(page_number))
            .collection("assets")
            .document(asset_type)
        )

    def _assets_collection(self, session_id: str, page_number: int):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("pages")
            .document(str(page_number))
            .collection("assets")
        )

    def _voice_command_ref(self, session_id: str, command_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("voice_commands")
            .document(command_id)
        )

    def _voice_commands_collection(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("voice_commands")
        )

    def _safety_decision_ref(self, session_id: str, decision_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("safety_decisions")
            .document(decision_id)
        )

    def _safety_decisions_collection(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("safety_decisions")
        )

    def _character_bible_ref(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("character_bible")
            .document("main")
        )

    def _style_bible_ref(self, session_id: str):
        return (
            self._client.collection("sessions")
            .document(session_id)
            .collection("style_bible")
            .document("main")
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

    async def update_page_history(
        self, session_id: str, page_history: list[str]
    ) -> None:
        """
        Persist the current page_history list on the Session document (T-032).

        Called after each page_complete event so that a reconnecting client can
        resume the story loop with the accumulated history entries.
        """
        ref = self._session_ref(session_id)
        doc = await ref.get()
        if not doc.exists:
            raise SessionNotFoundError(session_id)
        await ref.update(
            {
                "page_history": page_history,
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

    # ------------------------------------------------------------------
    # Page
    # ------------------------------------------------------------------

    async def save_page(self, session_id: str, page: Page) -> None:
        """Write (or overwrite) a Page document. Document ID is the page_number string."""
        data = _to_firestore(page)
        await self._page_ref(session_id, page.page_number).set(data)

    async def get_page(self, session_id: str, page_number: int) -> Page | None:
        """Return the Page for page_number, or None if it has not been saved yet."""
        doc = await self._page_ref(session_id, page_number).get()
        if not doc.exists:
            return None
        return Page.model_validate(doc.to_dict())

    async def list_pages(self, session_id: str) -> list[Page]:
        """
        Return all Pages for session_id, ordered by page_number ascending.
        Returns an empty list if no pages have been saved yet.
        """
        query = self._pages_collection(session_id).order_by("page_number")
        docs = await query.get()
        return [Page.model_validate(doc.to_dict()) for doc in docs]

    # ------------------------------------------------------------------
    # PageAsset
    # ------------------------------------------------------------------

    async def save_page_asset(self, session_id: str, asset: PageAsset) -> None:
        """Write (or overwrite) a PageAsset document. Document ID is the asset_type string."""
        data = _to_firestore(asset)
        asset_type = asset.asset_type if isinstance(asset.asset_type, str) else asset.asset_type.value
        await self._asset_ref(session_id, asset.page_number, asset_type).set(data)

    async def get_page_asset(
        self, session_id: str, page_number: int, asset_type: AssetType
    ) -> PageAsset | None:
        """Return the PageAsset for (page_number, asset_type), or None if not saved yet."""
        type_str = asset_type if isinstance(asset_type, str) else asset_type.value
        doc = await self._asset_ref(session_id, page_number, type_str).get()
        if not doc.exists:
            return None
        return PageAsset.model_validate(doc.to_dict())

    async def list_page_assets(
        self, session_id: str, page_number: int
    ) -> list[PageAsset]:
        """
        Return both PageAssets (illustration + narration) for a page.
        Returns an empty list if no assets have been saved yet.
        """
        docs = await self._assets_collection(session_id, page_number).get()
        return [PageAsset.model_validate(doc.to_dict()) for doc in docs]

    async def update_page_asset_status(
        self,
        session_id: str,
        page_number: int,
        asset_type: AssetType,
        status: AssetStatus,
        gcs_uri: str | None = None,
    ) -> None:
        """
        Update generation_status (and optionally gcs_uri + generated_at) on a PageAsset.
        Sets generated_at to UTC now when transitioning to ready or failed.
        """
        type_str = asset_type if isinstance(asset_type, str) else asset_type.value
        status_str = status if isinstance(status, str) else status.value
        payload: dict = {
            "generation_status": status_str,
        }
        if gcs_uri is not None:
            payload["gcs_uri"] = gcs_uri
        if status_str in ("ready", "failed"):
            payload["generated_at"] = _utc_now().isoformat()
        await self._asset_ref(session_id, page_number, type_str).update(payload)

    # ------------------------------------------------------------------
    # VoiceCommand
    # ------------------------------------------------------------------

    async def save_voice_command(self, session_id: str, cmd: VoiceCommand) -> None:
        """Write (or overwrite) a VoiceCommand document. Document ID = command_id."""
        data = _to_firestore(cmd)
        await self._voice_command_ref(session_id, str(cmd.command_id)).set(data)

    async def list_voice_commands(self, session_id: str) -> list[VoiceCommand]:
        """
        Return all VoiceCommands for session_id ordered by received_at ascending.
        Returns an empty list if none have been saved yet.
        """
        query = self._voice_commands_collection(session_id).order_by("received_at")
        docs = await query.get()
        return [VoiceCommand.model_validate(doc.to_dict()) for doc in docs]

    # ------------------------------------------------------------------
    # SafetyDecision
    # ------------------------------------------------------------------

    async def save_safety_decision(
        self, session_id: str, decision: SafetyDecision
    ) -> None:
        """Write (or overwrite) a SafetyDecision document. Document ID = decision_id."""
        data = _to_firestore(decision)
        await self._safety_decision_ref(session_id, str(decision.decision_id)).set(data)

    async def list_safety_decisions(self, session_id: str) -> list[SafetyDecision]:
        """
        Return all SafetyDecisions for session_id ordered by triggered_at ascending.
        Returns an empty list if none have been saved yet.
        """
        query = self._safety_decisions_collection(session_id).order_by("triggered_at")
        docs = await query.get()
        return [SafetyDecision.model_validate(doc.to_dict()) for doc in docs]

    # ------------------------------------------------------------------
    # CharacterBible + StyleBible
    # ------------------------------------------------------------------

    async def save_character_bible(
        self, session_id: str, bible: CharacterBible
    ) -> None:
        """
        Write CharacterBible and StyleBible atomically in a single Firestore batch.

        Paths written:
            sessions/{id}/character_bible/main  ← full CharacterBible (including embedded style_bible)
            sessions/{id}/style_bible/main      ← standalone StyleBible (kept in sync for independent reads)
        """
        batch = self._client.batch()
        batch.set(self._character_bible_ref(session_id), _to_firestore(bible))
        batch.set(self._style_bible_ref(session_id), _to_firestore(bible.style_bible))
        await batch.commit()

    async def get_character_bible(self, session_id: str) -> CharacterBible | None:
        """Return the CharacterBible for session_id, or None if not yet saved."""
        doc = await self._character_bible_ref(session_id).get()
        if not doc.exists:
            return None
        return CharacterBible.model_validate(doc.to_dict())

    async def update_character_bible_field(
        self, session_id: str, field: str, value: Any
    ) -> None:
        """
        Update a single field (or nested field) on the CharacterBible document.

        `field` accepts dot-notation for nested maps, e.g.:
            "content_policy.exclusions"
            "protagonist.reference_image_gcs_uri"

        Firestore merges the update so unrelated fields are preserved.
        """
        await self._character_bible_ref(session_id).update({field: value})

    async def save_style_bible(self, session_id: str, style: StyleBible) -> None:
        """
        Write (or overwrite) the standalone StyleBible document.
        Use this when only mood is being updated (tone-change VoiceCommand).
        For initial creation, prefer save_character_bible which writes both atomically.
        """
        data = _to_firestore(style)
        await self._style_bible_ref(session_id).set(data)

    async def get_style_bible(self, session_id: str) -> StyleBible | None:
        """Return the StyleBible for session_id, or None if not yet saved."""
        doc = await self._style_bible_ref(session_id).get()
        if not doc.exists:
            return None
        return StyleBible.model_validate(doc.to_dict())
