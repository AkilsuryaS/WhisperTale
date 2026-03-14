"""
TEST-C01 · Character consistency metadata unit tests — build_image_prompt

Pure unit tests: build_image_prompt is a pure function with no I/O.
All 8 test cases from the TEST-C01 spec are covered here.

Depends: T-027
"""

from __future__ import annotations

from app.models.character_bible import (
    CharacterBible,
    CharacterRef,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)
from app.services.character_bible_service import CharacterBibleService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_protagonist(
    *,
    name: str = "Pip",
    species_or_type: str = "rabbit",
    color: str = "golden",
    attire: str | None = None,
    notable_traits: list[str] | None = None,
    reference_image_gcs_uri: str | None = None,
) -> ProtagonistProfile:
    return ProtagonistProfile(
        name=name,
        species_or_type=species_or_type,
        color=color,
        attire=attire,
        notable_traits=notable_traits or ["big round eyes", "stubby legs"],
        reference_image_gcs_uri=reference_image_gcs_uri,
    )


def _make_style_bible(
    *,
    art_style: str = "soft watercolour",
    color_palette: str = "warm pastels",
    mood: str = "cosy",
    negative_style_terms: list[str] | None = None,
) -> StyleBible:
    return StyleBible(
        art_style=art_style,
        color_palette=color_palette,
        mood=mood,
        negative_style_terms=negative_style_terms or ["dark shadows", "sharp edges"],
    )


def _make_bible(
    protagonist: ProtagonistProfile | None = None,
    style_bible: StyleBible | None = None,
    content_policy: ContentPolicy | None = None,
    character_refs: list[CharacterRef] | None = None,
) -> CharacterBible:
    return CharacterBible(
        protagonist=protagonist or _make_protagonist(),
        style_bible=style_bible or _make_style_bible(),
        content_policy=content_policy or ContentPolicy(exclusions=["no gore"]),
        character_refs=character_refs or [],
    )


svc = CharacterBibleService()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestBuildImagePromptPageOneNoRefs:
    """TC-1: Page 1 — no reference URLs regardless of protagonist uri."""

    def test_page1_no_protagonist_uri_returns_empty_refs(self) -> None:
        bible = _make_bible(protagonist=_make_protagonist(reference_image_gcs_uri=None))
        prompt = svc.build_image_prompt(bible, "Pip hopped through the meadow.", 1)
        assert prompt.reference_urls == []

    def test_page1_with_protagonist_uri_still_returns_empty_refs(self) -> None:
        """Even if a URI is set, page 1 must return empty list."""
        bible = _make_bible(
            protagonist=_make_protagonist(
                reference_image_gcs_uri="gs://bucket/protagonist.png"
            )
        )
        prompt = svc.build_image_prompt(bible, "Pip hopped through the meadow.", 1)
        assert prompt.reference_urls == []


class TestBuildImagePromptPageTwoProtagonistRef:
    """TC-2 & TC-3: Page 2 protagonist reference URL handling."""

    def test_page2_protagonist_uri_present_included(self) -> None:
        bible = _make_bible(
            protagonist=_make_protagonist(
                reference_image_gcs_uri="gs://bucket/protagonist_ref.png"
            )
        )
        prompt = svc.build_image_prompt(bible, "Pip found a golden key.", 2)
        assert "gs://bucket/protagonist_ref.png" in prompt.reference_urls

    def test_page2_protagonist_uri_none_returns_empty(self) -> None:
        bible = _make_bible(protagonist=_make_protagonist(reference_image_gcs_uri=None))
        prompt = svc.build_image_prompt(bible, "Pip found a golden key.", 2)
        assert prompt.reference_urls == []

    def test_page2_no_none_values_in_reference_urls(self) -> None:
        bible = _make_bible(protagonist=_make_protagonist(reference_image_gcs_uri=None))
        prompt = svc.build_image_prompt(bible, "Pip found a golden key.", 2)
        assert None not in prompt.reference_urls


class TestBuildImagePromptSecondaryCharacters:
    """TC-4, TC-5, TC-6: Secondary character reference URL handling."""

    def _bible_with_yellow_bird(
        self, uri: str | None = "gs://bucket/yellow_bird.png"
    ) -> CharacterBible:
        char_ref = CharacterRef(
            char_id="yellow_bird",
            name="Yellow Bird",
            description="a cheerful yellow bird",
            reference_image_gcs_uri=uri,
            introduced_on_page=2,
        )
        return _make_bible(character_refs=[char_ref])

    def test_secondary_character_in_scene_includes_uri(self) -> None:
        """TC-4: CharacterRef in scene → URI appended."""
        bible = self._bible_with_yellow_bird()
        prompt = svc.build_image_prompt(bible, "Yellow Bird flew by the pond.", 3)
        assert "gs://bucket/yellow_bird.png" in prompt.reference_urls

    def test_secondary_character_not_in_scene_excludes_uri(self) -> None:
        """TC-5: CharacterRef NOT in scene → URI not included."""
        bible = self._bible_with_yellow_bird()
        prompt = svc.build_image_prompt(bible, "The bunny hopped alone.", 3)
        assert "gs://bucket/yellow_bird.png" not in prompt.reference_urls

    def test_secondary_character_none_uri_not_in_reference_urls(self) -> None:
        """TC-6: CharacterRef in scene but URI is None → no None in list."""
        bible = self._bible_with_yellow_bird(uri=None)
        prompt = svc.build_image_prompt(bible, "Yellow Bird flew by the pond.", 3)
        assert None not in prompt.reference_urls
        assert "gs://bucket/yellow_bird.png" not in prompt.reference_urls


class TestBuildImagePromptTextContent:
    """TC-7 & TC-8: Text prompt content assertions."""

    def test_negative_style_terms_in_prompt(self) -> None:
        """TC-7: negative_style_terms appear in text_prompt (negated form)."""
        style = _make_style_bible(negative_style_terms=["realistic", "dark"])
        bible = _make_bible(style_bible=style)
        prompt = svc.build_image_prompt(bible, "A scene.", 1)
        # Both terms must appear (either raw or prefixed with "no ")
        assert "realistic" in prompt.text_prompt or "no realistic" in prompt.text_prompt
        assert "dark" in prompt.text_prompt or "no dark" in prompt.text_prompt

    def test_protagonist_notable_traits_in_prompt(self) -> None:
        """TC-8: notable_traits appear verbatim in text_prompt."""
        protagonist = _make_protagonist(notable_traits=["big round eyes", "stubby legs"])
        bible = _make_bible(protagonist=protagonist)
        prompt = svc.build_image_prompt(bible, "A meadow scene.", 1)
        assert "big round eyes" in prompt.text_prompt
        assert "stubby legs" in prompt.text_prompt


class TestBuildImagePromptMultipleRefs:
    """Additional: protagonist + secondary both in page ≥ 2."""

    def test_both_protagonist_and_secondary_refs_included(self) -> None:
        char_ref = CharacterRef(
            char_id="yellow_bird",
            name="Yellow Bird",
            description="a cheerful yellow bird",
            reference_image_gcs_uri="gs://bucket/yellow_bird.png",
            introduced_on_page=2,
        )
        bible = _make_bible(
            protagonist=_make_protagonist(
                reference_image_gcs_uri="gs://bucket/protagonist.png"
            ),
            character_refs=[char_ref],
        )
        prompt = svc.build_image_prompt(bible, "Yellow Bird appeared with Pip.", 4)
        assert "gs://bucket/protagonist.png" in prompt.reference_urls
        assert "gs://bucket/yellow_bird.png" in prompt.reference_urls
        assert len(prompt.reference_urls) == 2
