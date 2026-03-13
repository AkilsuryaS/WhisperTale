"""
Shared Pydantic request/response schemas used across multiple routers.
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error envelope returned on 4xx/5xx responses."""

    error: str
