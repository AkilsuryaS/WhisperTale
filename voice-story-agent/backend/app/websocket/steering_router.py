"""
SteeringRouter — classify_steering

T-028: Pure synchronous function that classifies a steering utterance using
regex/keyword heuristics only (no Gemini call).

Public interface:
    def classify_steering(
        utterance: str,
        safety_result: SafetyResult,
    ) -> SteeringClassification

SteeringClassification:
    type:       CommandType | Literal["ambiguous", "unsafe"]
    confidence: float  (0.0 – 1.0)
    detail:     str | None

Classification rules (priority order):
1. unsafe        — safety_result.safe == False (always wins, checked first)
2. tone_change   — keywords: "funnier", "sillier", "calmer", "scarier",
                              "more exciting", "sleepier"
3. pacing_change — keywords: "faster", "slower", "shorter", "longer",
                              "more detail"
4. element_reintroduction — phrases: "bring back", "remember the",
                                      "what happened to"
5. character_introduction — phrases: "add a", "give him a", "give her a",
                                      "give them a", "introduce", "new friend",
                                      "new character"
6. ambiguous     — none of the above matched
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models.safety import SafetyResult
from app.models.voice_command import CommandType

# ---------------------------------------------------------------------------
# SteeringClassification
# ---------------------------------------------------------------------------

ClassificationType = CommandType | Literal["ambiguous", "unsafe"]


@dataclass(frozen=True)
class SteeringClassification:
    """
    Result of classify_steering.

    Attributes:
        type:       The classified command type, "ambiguous", or "unsafe".
        confidence: 0.0–1.0; 1.0 for unsafe/exact keyword matches,
                    lower for partial or inferred matches.
        detail:     Human-readable explanation or None.
    """

    type: ClassificationType
    confidence: float
    detail: str | None = None


# ---------------------------------------------------------------------------
# Pattern tables
# These are compiled once at import time for efficiency.
# Each pattern list is ordered from most specific to least specific.
# ---------------------------------------------------------------------------

_TONE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfunnier\b", re.IGNORECASE),
    re.compile(r"\bsillier\b", re.IGNORECASE),
    re.compile(r"\bcalmer\b", re.IGNORECASE),
    re.compile(r"\bscarier\b", re.IGNORECASE),
    re.compile(r"\bmore\s+exciting\b", re.IGNORECASE),
    re.compile(r"\bsleepier\b", re.IGNORECASE),
]

_PACING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfaster\b", re.IGNORECASE),
    re.compile(r"\bslower\b", re.IGNORECASE),
    re.compile(r"\bshorter\b", re.IGNORECASE),
    re.compile(r"\blonger\b", re.IGNORECASE),
    re.compile(r"\bmore\s+detail\b", re.IGNORECASE),
]

_ELEMENT_REINTRO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bbring\s+back\b", re.IGNORECASE),
    re.compile(r"\bremember\s+the\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+happened\s+to\b", re.IGNORECASE),
    # Natural edit/override language from parents during steering windows.
    re.compile(r"\bchange\b", re.IGNORECASE),
    re.compile(r"\binstead\b", re.IGNORECASE),
    re.compile(r"\bi\s+don'?t\s+want\b", re.IGNORECASE),
    re.compile(r"\bmake\s+\w+", re.IGNORECASE),
    re.compile(r"\bnot\b.+\bbut\b", re.IGNORECASE),
]

_CHARACTER_INTRO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\badd\s+a\b", re.IGNORECASE),
    re.compile(r"\bgive\s+him\s+a\b", re.IGNORECASE),
    re.compile(r"\bgive\s+her\s+a\b", re.IGNORECASE),
    re.compile(r"\bgive\s+them\s+a\b", re.IGNORECASE),
    re.compile(r"\bintroduce\b", re.IGNORECASE),
    re.compile(r"\bnew\s+friend\b", re.IGNORECASE),
    re.compile(r"\bnew\s+character\b", re.IGNORECASE),
]

# Ordered list of (CommandType, pattern_list) for linear scan
_RULES: list[tuple[CommandType, list[re.Pattern[str]]]] = [
    (CommandType.tone_change, _TONE_PATTERNS),
    (CommandType.pacing_change, _PACING_PATTERNS),
    (CommandType.element_reintroduction, _ELEMENT_REINTRO_PATTERNS),
    (CommandType.character_introduction, _CHARACTER_INTRO_PATTERNS),
]


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


def classify_steering(
    utterance: str,
    safety_result: SafetyResult,
) -> SteeringClassification:
    """
    Classify a steering utterance into a command type.

    Safety always wins: if ``safety_result.safe`` is False the result is
    ``type="unsafe"`` regardless of the utterance text.

    Args:
        utterance:      The raw transcript from the user's steering turn.
        safety_result:  Result of the prior safety evaluation.

    Returns:
        A :class:`SteeringClassification` with ``type``, ``confidence``, and
        an optional ``detail`` string.
    """
    # --- Priority 1: unsafe always wins ---
    if not safety_result.safe:
        return SteeringClassification(
            type="unsafe",
            confidence=1.0,
            detail=(
                f"Safety check failed: {safety_result.category}"
                if safety_result.category
                else "Safety check failed"
            ),
        )

    # --- Priority 2–5: keyword/regex heuristics ---
    for command_type, patterns in _RULES:
        for pattern in patterns:
            match = pattern.search(utterance)
            if match:
                return SteeringClassification(
                    type=command_type,
                    confidence=0.9,
                    # Preserve the full utterance so apply_steering receives
                    # the user's real instruction, not just a keyword match.
                    detail=utterance.strip(),
                )

    # --- Priority 6: ambiguous ---
    return SteeringClassification(
        type="ambiguous",
        confidence=1.0,
        detail="No known steering pattern matched",
    )
