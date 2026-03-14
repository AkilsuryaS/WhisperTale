"""
FastAPI dependency providers shared across routers.

Usage in a route:
    from app.dependencies import get_store, get_voice_service
    ...
    async def my_endpoint(store: SessionStore = Depends(get_store)):
        ...

In tests override with:
    app.dependency_overrides[get_store] = lambda: mock_store
    app.dependency_overrides[get_voice_service] = lambda: mock_voice_svc
"""

from __future__ import annotations

from app.services.session_store import SessionStore
from app.services.adk_voice_service import VoiceSessionService

# Module-level singleton so all WebSocket connections share the same session registry.
# Tests override via app.dependency_overrides[get_voice_service].
_voice_service_singleton: VoiceSessionService | None = None


def get_store() -> SessionStore:
    """Return a new SessionStore backed by the configured Firestore client."""
    return SessionStore()


def get_voice_service() -> VoiceSessionService:
    """Return the process-wide VoiceSessionService singleton."""
    global _voice_service_singleton
    if _voice_service_singleton is None:
        _voice_service_singleton = VoiceSessionService()
    return _voice_service_singleton
