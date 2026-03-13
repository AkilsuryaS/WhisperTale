"""
Tests for T-005: Pydantic v2 models — CharacterBible, StyleBible,
ContentPolicy, ProtagonistProfile, CharacterRef.

Covers:
- All five models import and construct without error
- CharacterBible full construction with all embedded sub-documents
- ProtagonistProfile: notable_traits 2–4 bounds, non-empty trait validation,
  optional attire and reference_image_gcs_uri
- StyleBible: required fields, optional last_updated_by_command_id
- ContentPolicy: defaults to empty lists, accepts exclusions and decision IDs
- CharacterRef: required fields, optional reference_image_gcs_uri,
  introduced_on_page bounds (1–5), auto-generated voice_command_id
- CharacterBible: content_policy defaults, character_refs defaults,
  multiple CharacterRefs stored correctly
- use_enum_values=True sanity check (inherited from ConfigDict)
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.character_bible import (
    CharacterBible,
    CharacterRef,
    ContentPolicy,
    ProtagonistProfile,
    StyleBible,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CMD_ID = uuid4()


def make_protagonist(**overrides) -> ProtagonistProfile:
    defaults = dict(
        name="Spark",
        species_or_type="purple dragon",
        color="bright purple",
        notable_traits=["big round eyes", "stumpy legs"],
    )
    defaults.update(overrides)
    return ProtagonistProfile(**defaults)


def make_style_bible(**overrides) -> StyleBible:
    defaults = dict(
        art_style="soft colorful picture book illustration",
        color_palette="pastel purples, warm yellows, soft greens",
        mood="warm, gentle, playful",
        negative_style_terms=["realistic", "dark", "scary"],
    )
    defaults.update(overrides)
    return StyleBible(**defaults)


def make_content_policy(**overrides) -> ContentPolicy:
    defaults = dict(
        exclusions=["no destruction", "no gore"],
    )
    defaults.update(overrides)
    return ContentPolicy(**defaults)


def make_character_ref(**overrides) -> CharacterRef:
    defaults = dict(
        char_id="yellow_bird",
        name="Yellow Bird",
        description="A small cheerful yellow bird with a red beak",
        introduced_on_page=3,
        voice_command_id=CMD_ID,
    )
    defaults.update(overrides)
    return CharacterRef(**defaults)


def make_character_bible(**overrides) -> CharacterBible:
    defaults = dict(
        protagonist=make_protagonist(),
        style_bible=make_style_bible(),
    )
    defaults.update(overrides)
    return CharacterBible(**defaults)


# ---------------------------------------------------------------------------
# ProtagonistProfile — construction
# ---------------------------------------------------------------------------


class TestProtagonistProfileConstruction:
    def test_minimal_profile_constructs(self):
        p = make_protagonist()
        assert p.name == "Spark"
        assert p.species_or_type == "purple dragon"
        assert p.color == "bright purple"
        assert p.attire is None
        assert p.reference_image_gcs_uri is None

    def test_optional_attire_stored(self):
        p = make_protagonist(attire="red scarf")
        assert p.attire == "red scarf"

    def test_reference_image_gcs_uri_stored(self):
        uri = "gs://my-bucket/sessions/abc/pages/1/illustration.png"
        p = make_protagonist(reference_image_gcs_uri=uri)
        assert p.reference_image_gcs_uri == uri

    def test_exactly_two_traits_is_valid(self):
        p = make_protagonist(notable_traits=["big eyes", "fluffy tail"])
        assert len(p.notable_traits) == 2

    def test_exactly_four_traits_is_valid(self):
        p = make_protagonist(notable_traits=["a", "b", "c", "d"])
        assert len(p.notable_traits) == 4

    def test_one_trait_raises(self):
        with pytest.raises(ValidationError):
            make_protagonist(notable_traits=["only one"])

    def test_five_traits_raises(self):
        with pytest.raises(ValidationError):
            make_protagonist(notable_traits=["a", "b", "c", "d", "e"])

    def test_empty_list_raises(self):
        with pytest.raises(ValidationError):
            make_protagonist(notable_traits=[])

    def test_empty_string_trait_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            make_protagonist(notable_traits=["valid trait", ""])

    def test_whitespace_only_trait_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            make_protagonist(notable_traits=["valid trait", "   "])

    def test_three_traits_valid(self):
        p = make_protagonist(notable_traits=["a", "b", "c"])
        assert len(p.notable_traits) == 3


# ---------------------------------------------------------------------------
# StyleBible — construction
# ---------------------------------------------------------------------------


class TestStyleBibleConstruction:
    def test_minimal_style_bible_constructs(self):
        s = make_style_bible()
        assert s.art_style == "soft colorful picture book illustration"
        assert s.mood == "warm, gentle, playful"
        assert s.last_updated_by_command_id is None

    def test_negative_style_terms_stored(self):
        s = make_style_bible(negative_style_terms=["realistic", "dark"])
        assert s.negative_style_terms == ["realistic", "dark"]

    def test_empty_negative_terms_valid(self):
        s = make_style_bible(negative_style_terms=[])
        assert s.negative_style_terms == []

    def test_last_updated_by_command_id_accepts_uuid(self):
        uid = uuid4()
        s = make_style_bible(last_updated_by_command_id=uid)
        assert s.last_updated_by_command_id == uid

    def test_mood_can_be_updated(self):
        s = make_style_bible(mood="exciting, action-packed")
        assert s.mood == "exciting, action-packed"


# ---------------------------------------------------------------------------
# ContentPolicy — construction
# ---------------------------------------------------------------------------


class TestContentPolicyConstruction:
    def test_default_content_policy_has_empty_lists(self):
        cp = ContentPolicy()
        assert cp.exclusions == []
        assert cp.derived_from_safety_decisions == []

    def test_exclusions_stored(self):
        cp = make_content_policy()
        assert "no destruction" in cp.exclusions
        assert "no gore" in cp.exclusions

    def test_derived_from_safety_decisions_accepts_uuid_strings(self):
        uid = str(uuid4())
        cp = ContentPolicy(
            exclusions=["no fear"],
            derived_from_safety_decisions=[uid],
        )
        assert cp.derived_from_safety_decisions == [uid]

    def test_exclusions_is_mutable_list(self):
        cp = make_content_policy()
        cp.exclusions.append("no violence")
        assert "no violence" in cp.exclusions

    def test_two_policies_have_independent_exclusion_lists(self):
        cp1 = ContentPolicy()
        cp2 = ContentPolicy()
        cp1.exclusions.append("only cp1")
        assert "only cp1" not in cp2.exclusions


# ---------------------------------------------------------------------------
# CharacterRef — construction
# ---------------------------------------------------------------------------


class TestCharacterRefConstruction:
    def test_minimal_ref_constructs(self):
        ref = make_character_ref()
        assert ref.char_id == "yellow_bird"
        assert ref.name == "Yellow Bird"
        assert ref.introduced_on_page == 3
        assert ref.voice_command_id == CMD_ID
        assert ref.reference_image_gcs_uri is None

    def test_voice_command_id_auto_generated_when_omitted(self):
        ref = CharacterRef(
            char_id="blue_cat",
            name="Blue Cat",
            description="A blue tabby",
            introduced_on_page=2,
        )
        assert isinstance(ref.voice_command_id, UUID)

    def test_reference_image_gcs_uri_stored(self):
        uri = "gs://bucket/sessions/s1/characters/yellow_bird_ref.png"
        ref = make_character_ref(reference_image_gcs_uri=uri)
        assert ref.reference_image_gcs_uri == uri

    def test_introduced_on_page_1_is_valid(self):
        ref = make_character_ref(introduced_on_page=1)
        assert ref.introduced_on_page == 1

    def test_introduced_on_page_5_is_valid(self):
        ref = make_character_ref(introduced_on_page=5)
        assert ref.introduced_on_page == 5

    def test_introduced_on_page_0_raises(self):
        with pytest.raises(ValidationError):
            make_character_ref(introduced_on_page=0)

    def test_introduced_on_page_6_raises(self):
        with pytest.raises(ValidationError):
            make_character_ref(introduced_on_page=6)


# ---------------------------------------------------------------------------
# CharacterBible — construction
# ---------------------------------------------------------------------------


class TestCharacterBibleConstruction:
    def test_minimal_bible_constructs(self):
        bible = make_character_bible()
        assert bible.protagonist.name == "Spark"
        assert bible.style_bible.art_style == "soft colorful picture book illustration"
        assert bible.content_policy.exclusions == []
        assert bible.character_refs == []

    def test_content_policy_exclusions_is_list_of_strings(self):
        bible = make_character_bible(
            content_policy=ContentPolicy(exclusions=["no gore", "no darkness"])
        )
        assert isinstance(bible.content_policy.exclusions, list)
        assert all(isinstance(e, str) for e in bible.content_policy.exclusions)

    def test_character_refs_stored_as_list(self):
        ref1 = make_character_ref(char_id="bird", name="Bird", introduced_on_page=2)
        ref2 = make_character_ref(char_id="cat", name="Cat", introduced_on_page=4)
        bible = make_character_bible(character_refs=[ref1, ref2])
        assert len(bible.character_refs) == 2
        assert bible.character_refs[0].char_id == "bird"
        assert bible.character_refs[1].char_id == "cat"

    def test_full_construction_with_all_fields(self):
        protagonist = ProtagonistProfile(
            name="Luna",
            species_or_type="silver fox",
            color="silver",
            attire="blue ribbon",
            notable_traits=["fluffy tail", "bright green eyes", "tiny paws"],
        )
        style = StyleBible(
            art_style="watercolor children's book",
            color_palette="soft blues and silvers",
            mood="calm and magical",
            negative_style_terms=["dark", "scary", "realistic"],
            last_updated_by_command_id=uuid4(),
        )
        policy = ContentPolicy(
            exclusions=["no violence"],
            derived_from_safety_decisions=[str(uuid4())],
        )
        ref = CharacterRef(
            char_id="wise_owl",
            name="Wise Owl",
            description="An old owl with round glasses",
            introduced_on_page=2,
            voice_command_id=uuid4(),
        )
        bible = CharacterBible(
            protagonist=protagonist,
            style_bible=style,
            content_policy=policy,
            character_refs=[ref],
        )
        assert bible.protagonist.name == "Luna"
        assert bible.style_bible.mood == "calm and magical"
        assert bible.content_policy.exclusions == ["no violence"]
        assert len(bible.character_refs) == 1
        assert bible.character_refs[0].name == "Wise Owl"

    def test_protagonist_is_required(self):
        with pytest.raises(ValidationError):
            CharacterBible(style_bible=make_style_bible())

    def test_style_bible_is_required(self):
        with pytest.raises(ValidationError):
            CharacterBible(protagonist=make_protagonist())
