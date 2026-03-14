"""
Pydantic v2 models and dataclasses for safety classification.

Data model reference: specs/001-voice-story-agent/data-model.md §8
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SafetyPhase(str, Enum):
    """Phase in which a SafetyDecision can be triggered (never narration)."""

    setup = "setup"
    steering = "steering"


# ---------------------------------------------------------------------------
# Constant
# ---------------------------------------------------------------------------

# Used as the fallback rewrite whenever the safety classifier fails or is
# unavailable. Child-safe, warm, and actionable as a story premise.
SAFE_FALLBACK_REWRITE = (
    "How about a story where our character goes on a fun adventure "
    "and helps a friend along the way?"
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SafetyCategory(str, Enum):
    physical_harm = "physical_harm"
    character_death = "character_death"
    gore = "gore"
    destruction = "destruction"
    sexual_content = "sexual_content"
    fear_escalation = "fear_escalation"


# ---------------------------------------------------------------------------
# SafetyDecision
# ---------------------------------------------------------------------------


class SafetyDecision(BaseModel):
    """
    Active decision record created when an unsafe utterance is detected.

    Firestore path: sessions/{session_id}/safety_decisions/{decision_id}

    raw_input is stored for audit only and MUST NOT appear in any UI response,
    caption, illustration prompt, or narration script.
    """

    model_config = ConfigDict(use_enum_values=True)

    decision_id: UUID = Field(default_factory=uuid4)
    # ID of the UserTurn that triggered this decision
    turn_id: UUID
    # setup or steering — never narration
    phase: SafetyPhase
    # Verbatim unsafe utterance — audit only, never surfaced in UI
    raw_input: str
    # None when the classifier itself failed (fail-safe path)
    detected_category: Optional[SafetyCategory] = None
    # Child-safe alternative voiced to the user
    proposed_rewrite: str
    # True if the user acknowledged / redirected; False if session was abandoned
    user_accepted: bool
    # Premise used for generation after acknowledgment (may extend proposed_rewrite)
    final_premise: Optional[str] = None
    # Exclusion string added to ContentPolicy.exclusions upon acceptance
    exclusion_added: Optional[str] = None
    triggered_at: datetime


# ---------------------------------------------------------------------------
# SafetyResult
# ---------------------------------------------------------------------------


@dataclass
class SafetyResult:
    """
    Lightweight result returned by the safety classifier service.

    safe=True  → utterance passed; category and rewrite are None.
    safe=False → utterance failed; category identifies the violation;
                 rewrite is the child-safe alternative (or SAFE_FALLBACK_REWRITE
                 when the classifier itself is unavailable).
    """

    safe: bool
    category: SafetyCategory | None = None
    rewrite: str | None = None
