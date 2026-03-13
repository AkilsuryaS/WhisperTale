"""
Pydantic v2 model for VoiceCommand.

Data model reference: specs/001-voice-story-agent/data-model.md §9
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


class CommandType(str, Enum):
    tone_change = "tone_change"
    pacing_change = "pacing_change"
    element_reintroduction = "element_reintroduction"
    character_introduction = "character_introduction"


# ---------------------------------------------------------------------------
# VoiceCommand
# ---------------------------------------------------------------------------


class VoiceCommand(BaseModel):
    """
    A mid-story steering instruction derived from a user voice utterance.

    Firestore path: sessions/{session_id}/voice_commands/{command_id}

    Validation rules (enforced by application logic, not the model):
    - applied_to_pages must contain only page numbers > Session.current_page
      at the time the command was received.
    - If command_type = character_introduction, new_character_ref_id must be
      set and a CharacterRef must be written to CharacterBible in the same
      Firestore batch.
    - If safe = False, safety_decision_id must be set; the command must not
      influence any page until SafetyDecision.user_accepted = True.
    """

    model_config = ConfigDict(use_enum_values=True)

    command_id: UUID = Field(default_factory=uuid4)
    # ID of the UserTurn that produced this command
    turn_id: UUID
    raw_transcript: str
    # Agent's semantic interpretation of the utterance
    interpreted_intent: str
    command_type: CommandType
    # Page numbers not yet generated when the command was received
    applied_to_pages: list[int] = Field(default_factory=list)
    # Set when command_type = character_introduction
    new_character_ref_id: Optional[str] = None
    # True if the command passed the safety check; False if it was rewritten
    safe: bool = True
    # Set when safe = False; references SafetyDecision.decision_id
    safety_decision_id: Optional[UUID] = None
    received_at: datetime
