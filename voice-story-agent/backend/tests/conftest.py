"""Shared pytest fixtures for the Voice Story Agent backend test suite."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Synchronous HTTPX test client wrapping the FastAPI app."""
    with TestClient(app) as c:
        yield c
