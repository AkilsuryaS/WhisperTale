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
    app.dependency_overrides[get_setup_handler] = lambda: mock_setup_handler
"""

from __future__ import annotations

from app.services.session_store import SessionStore
from app.services.adk_voice_service import VoiceSessionService
from app.services.character_bible_service import CharacterBibleService
from app.services.image_generation import ImageGenerationService
from app.services.media_persistence import MediaPersistenceService
from app.services.safety_service import SafetyService
from app.services.story_planner import StoryPlannerService
from app.services.tts_service import TTSService
from app.websocket.setup_handler import SetupHandler

# Module-level singletons so all WebSocket connections share the same registry.
# Tests override via app.dependency_overrides[get_*].
_voice_service_singleton: VoiceSessionService | None = None
_safety_service_singleton: SafetyService | None = None
_setup_handler_singleton: SetupHandler | None = None
_story_planner_singleton: StoryPlannerService | None = None
_character_bible_svc_singleton: CharacterBibleService | None = None
_image_svc_singleton: ImageGenerationService | None = None
_tts_svc_singleton: TTSService | None = None
_media_svc_singleton: MediaPersistenceService | None = None


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


def get_setup_handler() -> SetupHandler:
    """Return the process-wide SetupHandler singleton."""
    global _setup_handler_singleton
    if _setup_handler_singleton is None:
        _setup_handler_singleton = SetupHandler()
    return _setup_handler_singleton


def get_story_planner() -> StoryPlannerService:
    """Return the process-wide StoryPlannerService singleton."""
    global _story_planner_singleton
    if _story_planner_singleton is None:
        _story_planner_singleton = StoryPlannerService()
    return _story_planner_singleton


def get_character_bible_svc() -> CharacterBibleService:
    """Return the process-wide CharacterBibleService singleton."""
    global _character_bible_svc_singleton
    if _character_bible_svc_singleton is None:
        _character_bible_svc_singleton = CharacterBibleService()
    return _character_bible_svc_singleton


def get_image_svc() -> ImageGenerationService:
    """Return the process-wide ImageGenerationService singleton."""
    global _image_svc_singleton
    if _image_svc_singleton is None:
        _image_svc_singleton = ImageGenerationService()
    return _image_svc_singleton


def get_tts_svc() -> TTSService:
    """Return the process-wide TTSService singleton."""
    global _tts_svc_singleton
    if _tts_svc_singleton is None:
        _tts_svc_singleton = TTSService()
    return _tts_svc_singleton


def get_media_svc() -> MediaPersistenceService:
    """Return the process-wide MediaPersistenceService singleton."""
    global _media_svc_singleton
    if _media_svc_singleton is None:
        _media_svc_singleton = MediaPersistenceService()
    return _media_svc_singleton
