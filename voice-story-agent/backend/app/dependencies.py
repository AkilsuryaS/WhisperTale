"""
FastAPI dependency providers shared across routers.

Usage in a route:
    from app.dependencies import get_store, get_voice_service, get_safety_service
    ...
    async def my_endpoint(store: SessionStore = Depends(get_store)):
        ...

In tests override with:
    app.dependency_overrides[get_store] = lambda: mock_store
    app.dependency_overrides[get_voice_service] = lambda: mock_voice_svc
    app.dependency_overrides[get_safety_service] = lambda: mock_safety_svc
"""

from __future__ import annotations

from app.services.session_store import SessionStore
from app.services.adk_voice_service import VoiceSessionService
from app.services.safety_service import SafetyService

# Module-level singletons so all WebSocket connections share the same registry.
# Tests override via app.dependency_overrides[get_*].
_voice_service_singleton: VoiceSessionService | None = None
_safety_service_singleton: SafetyService | None = None


def get_store() -> SessionStore:
    """Return a new SessionStore backed by the configured Firestore client."""
    return SessionStore()


def get_voice_service() -> VoiceSessionService:
    """Return the process-wide VoiceSessionService singleton."""
    global _voice_service_singleton
    if _voice_service_singleton is None:
        _voice_service_singleton = VoiceSessionService()
    return _voice_service_singleton


def get_safety_service() -> SafetyService:
    """Return the process-wide SafetyService singleton."""
    global _safety_service_singleton
    if _safety_service_singleton is None:
        _safety_service_singleton = SafetyService()
    return _safety_service_singleton
