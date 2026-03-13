"""
FastAPI dependency providers shared across routers.

Usage in a route:
    from app.dependencies import get_store
    ...
    async def my_endpoint(store: SessionStore = Depends(get_store)):
        ...

In tests override with:
    app.dependency_overrides[get_store] = lambda: mock_store
"""

from __future__ import annotations

from app.services.session_store import SessionStore


def get_store() -> SessionStore:
    """Return a new SessionStore backed by the configured Firestore client."""
    return SessionStore()
