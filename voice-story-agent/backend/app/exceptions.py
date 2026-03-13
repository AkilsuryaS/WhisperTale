"""
Application-level custom exceptions.
"""

from __future__ import annotations


class SessionNotFoundError(Exception):
    """Raised when a Firestore session document does not exist."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
        self.session_id = session_id
