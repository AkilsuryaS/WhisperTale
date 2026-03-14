"""
Application-level custom exceptions.
"""

from __future__ import annotations


class SessionNotFoundError(Exception):
    """Raised when a Firestore session document does not exist."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
        self.session_id = session_id


class MediaPersistenceError(Exception):
    """Raised when a GCS API operation fails in MediaPersistenceService."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class VoiceSessionNotFoundError(Exception):
    """Raised when send_audio/end is called for a session that is not open."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Voice session not open: {session_id}")
        self.session_id = session_id


class VoiceSessionError(Exception):
    """Raised when an ADK / Gemini Live API call fails in VoiceSessionService."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause
