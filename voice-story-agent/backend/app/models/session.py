"""
Pydantic v2 models for Session, UserTurn, and StoryBrief.

Data model reference: specs/001-voice-story-agent/data-model.md §1–3
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    setup = "setup"
    generating = "generating"
    complete = "complete"
    error = "error"


class TurnPhase(str, Enum):
    setup = "setup"
    steering = "steering"
    narration = "narration"


class Speaker(str, Enum):
    user = "user"
    agent = "agent"


class Tone(str, Enum):
    silly = "silly"
    sleepy = "sleepy"
    adventurous = "adventurous"
    warm = "warm"
    curious = "curious"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session(BaseModel):
    """Top-level session document stored at Firestore path sessions/{session_id}."""

    model_config = ConfigDict(use_enum_values=True)

    session_id: UUID = Field(default_factory=uuid4)
    status: SessionStatus = SessionStatus.setup
    created_at: datetime
    updated_at: datetime
    # page_count is always 5 for MVP; validated below
    page_count: int = Field(default=5, ge=1)
    # 0 during setup; 1–5 during generation; 5 when complete
    current_page: int = Field(default=0, ge=0, le=5)
    # 5-element beat summaries; empty list is valid during setup phase
    story_arc: list[str] = Field(default_factory=list)
    # Set only when status = error
    error_message: Optional[str] = None

    @field_validator("page_count")
    @classmethod
    def page_count_must_be_five(cls, v: int) -> int:
        if v != 5:
            raise ValueError("page_count must equal 5 for MVP")
        return v

    @field_validator("story_arc")
    @classmethod
    def story_arc_elements_non_empty(cls, v: list[str]) -> list[str]:
        """Each element that is present must be a non-empty string."""
        for i, beat in enumerate(v):
            if not beat or not beat.strip():
                raise ValueError(f"story_arc[{i}] must be a non-empty string")
        return v

    def is_ready_to_generate(self) -> bool:
        """True once story_arc contains exactly 5 non-empty beats."""
        return len(self.story_arc) == 5 and all(b.strip() for b in self.story_arc)


# ---------------------------------------------------------------------------
# UserTurn
# ---------------------------------------------------------------------------


class UserTurn(BaseModel):
    """
    Single voice exchange stored at
    Firestore path sessions/{session_id}/turns/{turn_id}.
    """

    model_config = ConfigDict(use_enum_values=True)

    turn_id: UUID = Field(default_factory=uuid4)
    # 1-based monotonically increasing counter within the session
    sequence: int = Field(..., ge=1)
    phase: TurnPhase
    speaker: Speaker
    raw_transcript: str
    caption_text: str
    # Set if this user turn produced a VoiceCommand
    voice_command_id: Optional[UUID] = None
    # Set if this user turn triggered a SafetyDecision
    safety_decision_id: Optional[UUID] = None
    # Page number active when this turn occurred; null during setup
    page_context: Optional[int] = Field(default=None, ge=1, le=5)
    timestamp: datetime


# ---------------------------------------------------------------------------
# StoryBrief
# ---------------------------------------------------------------------------


class StoryBrief(BaseModel):
    """
    Confirmed story parameters stored at
    Firestore path sessions/{session_id}/story_brief/main.
    """

    model_config = ConfigDict(use_enum_values=True)

    protagonist_name: str = Field(..., max_length=80)
    protagonist_description: str
    setting: str = Field(..., max_length=200)
    tone: Tone
    additional_constraints: Optional[list[str]] = None
    # Audit only — MUST NOT appear in any UI response
    raw_setup_transcript: str
    confirmed_at: datetime
    confirmed_by_agent: bool = False
