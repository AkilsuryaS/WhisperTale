"""
Pydantic v2 models for the post-generation story edit feature.

Edit scopes:
    global_character — CharacterBible attribute change, regenerate images only
    single_page      — isolated text+image change on one page
    cascade          — text+image rewrite from page N through end of story
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EditScope(str, Enum):
    global_character = "global_character"
    single_page = "single_page"
    cascade = "cascade"


class EditRequest(BaseModel):
    """Client-submitted edit instruction."""

    instruction: str = Field(..., min_length=1)
    hint_page: Optional[int] = Field(default=None, ge=1, le=5)


class EditDecision(BaseModel):
    """
    Model-generated decision describing what to change and where.

    Produced by EditClassifierService from the user's instruction
    plus full story context.
    """

    scope: EditScope
    affected_pages: list[int] = Field(..., min_length=1)
    bible_patch: Optional[dict] = None
    page_instructions: dict[int, str] = Field(default_factory=dict)
    reasoning: str = ""
