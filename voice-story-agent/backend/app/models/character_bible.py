"""
Pydantic v2 models for CharacterBible and its embedded sub-documents.

Data model reference: specs/001-voice-story-agent/data-model.md §4–5

Hierarchy:
    CharacterBible
        ├── protagonist: ProtagonistProfile   (embedded)
        ├── style_bible: StyleBible           (embedded; also written as a
        │                                      separate Firestore document at
        │                                      sessions/{id}/style_bible/main)
        ├── content_policy: ContentPolicy     (embedded)
        └── character_refs: list[CharacterRef]
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# ProtagonistProfile
# ---------------------------------------------------------------------------


class ProtagonistProfile(BaseModel):
    """
    Visual description of the main character.
    Embedded inside CharacterBible; mirrors StoryBrief.protagonist_name.
    """

    model_config = ConfigDict(use_enum_values=True)

    name: str
    species_or_type: str
    color: str
    attire: Optional[str] = None
    # 2–4 visual traits used in image prompts
    notable_traits: list[str] = Field(..., min_length=2, max_length=4)
    # Null until page-1 illustration PageAsset is ready
    reference_image_gcs_uri: Optional[str] = None

    @field_validator("notable_traits")
    @classmethod
    def traits_must_be_non_empty(cls, v: list[str]) -> list[str]:
        for i, trait in enumerate(v):
            if not trait or not trait.strip():
                raise ValueError(f"notable_traits[{i}] must be a non-empty string")
        return v


# ---------------------------------------------------------------------------
# StyleBible
# ---------------------------------------------------------------------------


class StyleBible(BaseModel):
    """
    Art-direction parameters shared across all page illustrations.

    Firestore path: sessions/{session_id}/style_bible/main
    Also embedded inside CharacterBible.style_bible (kept in sync via batch write).
    The only field mutated post-creation is `mood` (via tone-change VoiceCommand).
    """

    model_config = ConfigDict(use_enum_values=True)

    art_style: str
    color_palette: str
    mood: str
    negative_style_terms: list[str]
    # Set when a tone-change VoiceCommand last updated mood; null otherwise
    last_updated_by_command_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# ContentPolicy
# ---------------------------------------------------------------------------


class ContentPolicy(BaseModel):
    """
    Active content exclusion constraints for the session.
    Pre-populated with a base set; extended when a SafetyDecision is accepted.
    """

    model_config = ConfigDict(use_enum_values=True)

    exclusions: list[str] = Field(default_factory=list)
    # SafetyDecision.decision_id values that contributed exclusions
    derived_from_safety_decisions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# CharacterRef
# ---------------------------------------------------------------------------


class CharacterRef(BaseModel):
    """
    Secondary character introduced mid-story via a character_introduction VoiceCommand.
    Stored as an element of CharacterBible.character_refs.
    """

    model_config = ConfigDict(use_enum_values=True)

    char_id: str
    name: str
    description: str
    # Null until the character's first-appearance illustration PageAsset is ready
    reference_image_gcs_uri: Optional[str] = None
    # Page number when this character first appeared (1–5)
    introduced_on_page: int = Field(..., ge=1, le=5)
    # ID of the VoiceCommand that introduced this character
    voice_command_id: UUID = Field(default_factory=uuid4)


# ---------------------------------------------------------------------------
# CharacterBible
# ---------------------------------------------------------------------------


class CharacterBible(BaseModel):
    """
    Top-level character consistency document.

    Firestore path: sessions/{session_id}/character_bible/main
    Written atomically with StyleBible in a single Firestore batch.
    """

    model_config = ConfigDict(use_enum_values=True)

    protagonist: ProtagonistProfile
    style_bible: StyleBible
    content_policy: ContentPolicy = Field(default_factory=ContentPolicy)
    character_refs: list[CharacterRef] = Field(default_factory=list)
