"""
Pydantic v2 models for Page and PageAsset.

Data model reference: specs/001-voice-story-agent/data-model.md §6–7
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PageStatus(str, Enum):
    pending = "pending"
    text_ready = "text_ready"
    assets_generating = "assets_generating"
    complete = "complete"
    error = "error"


class AssetType(str, Enum):
    illustration = "illustration"
    narration = "narration"


class AssetStatus(str, Enum):
    pending = "pending"
    generating = "generating"
    ready = "ready"
    failed = "failed"


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


class Page(BaseModel):
    """
    One page of the generated story.

    Firestore path: sessions/{session_id}/pages/{page_number}
    page_number is stored as the string "1"–"5" in Firestore but is an int here.

    Asset failures do NOT set status=error; they set illustration_failed or
    audio_failed and status still advances to complete so the session continues.
    """

    model_config = ConfigDict(use_enum_values=True)

    page_number: int = Field(..., ge=1, le=5)
    status: PageStatus = PageStatus.pending
    beat: str
    # Set when status >= text_ready
    text: Optional[str] = None
    # TTS-optimised version of text; may differ from display text
    narration_script: Optional[str] = None
    illustration_failed: bool = False
    audio_failed: bool = False
    # IDs of VoiceCommand documents that influenced this page's generation
    steering_applied: list[str] = Field(default_factory=list)
    # Set when status = complete
    generated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# PageAsset
# ---------------------------------------------------------------------------


class PageAsset(BaseModel):
    """
    Binary asset (illustration or narration audio) for a single page.

    Firestore path: sessions/{session_id}/pages/{page_number}/assets/{asset_type}

    Once generation_status is ready or failed it MUST NOT be changed
    (terminal states enforced by application logic, not the model itself).
    """

    model_config = ConfigDict(use_enum_values=True)

    asset_id: UUID = Field(default_factory=uuid4)
    page_number: int = Field(..., ge=1, le=5)
    asset_type: AssetType
    generation_status: AssetStatus = AssetStatus.pending
    # gs://{bucket}/sessions/{session_id}/pages/{page_number}/{asset_type}.*
    gcs_uri: Optional[str] = None
    # Short-lived (1 hour) HTTPS read URL; sent to frontend via WebSocket
    signed_url: Optional[str] = None
    signed_url_expires_at: Optional[datetime] = None
    # Set when generation_status = failed
    error_detail: Optional[str] = None
    # Set when generation_status transitions to ready or failed
    generated_at: Optional[datetime] = None
