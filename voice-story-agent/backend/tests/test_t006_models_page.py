"""
Tests for T-006 (part 1): Pydantic v2 models — Page and PageAsset.

Covers:
- PageStatus, AssetType, AssetStatus enums
- Page: construction, defaults, page_number bounds, optional fields
- PageAsset: construction, auto-generated asset_id, page_number bounds,
  optional GCS/signed-URL/error fields
- use_enum_values=True stores plain strings
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.models.page import AssetStatus, AssetType, Page, PageAsset, PageStatus

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_page(**overrides) -> Page:
    defaults = dict(page_number=1, beat="The dragon sets off on an adventure")
    defaults.update(overrides)
    return Page(**defaults)


def make_page_asset(**overrides) -> PageAsset:
    defaults = dict(
        page_number=1,
        asset_type=AssetType.illustration,
    )
    defaults.update(overrides)
    return PageAsset(**defaults)


# ---------------------------------------------------------------------------
# Enum membership
# ---------------------------------------------------------------------------


class TestPageStatusEnum:
    @pytest.mark.parametrize(
        "status",
        ["pending", "text_ready", "assets_generating", "complete", "error"],
    )
    def test_all_values_accepted(self, status):
        p = make_page(status=status)
        assert p.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            make_page(status="unknown")


class TestAssetTypeEnum:
    def test_illustration_accepted(self):
        a = make_page_asset(asset_type="illustration")
        assert a.asset_type == "illustration"

    def test_narration_accepted(self):
        a = make_page_asset(asset_type="narration")
        assert a.asset_type == "narration"

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            make_page_asset(asset_type="video")


class TestAssetStatusEnum:
    @pytest.mark.parametrize("status", ["pending", "generating", "ready", "failed"])
    def test_all_values_accepted(self, status):
        a = make_page_asset(generation_status=status)
        assert a.generation_status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            make_page_asset(generation_status="cancelled")


# ---------------------------------------------------------------------------
# Page — construction and defaults
# ---------------------------------------------------------------------------


class TestPageConstruction:
    def test_minimal_page_constructs(self):
        p = make_page()
        assert p.page_number == 1
        assert p.status == "pending"
        assert p.beat == "The dragon sets off on an adventure"
        assert p.text is None
        assert p.narration_script is None
        assert p.illustration_failed is False
        assert p.audio_failed is False
        assert p.steering_applied == []
        assert p.generated_at is None

    def test_use_enum_values_stores_string(self):
        p = make_page(status=PageStatus.complete)
        assert p.status == "complete"
        assert isinstance(p.status, str)

    def test_text_and_narration_script_stored(self):
        p = make_page(
            status="text_ready",
            text="Spark soared over the treetops.",
            narration_script="Spark soared... over the treetops.",
        )
        assert p.text == "Spark soared over the treetops."
        assert p.narration_script == "Spark soared... over the treetops."

    def test_illustration_failed_flag(self):
        p = make_page(illustration_failed=True)
        assert p.illustration_failed is True

    def test_audio_failed_flag(self):
        p = make_page(audio_failed=True)
        assert p.audio_failed is True

    def test_steering_applied_stores_command_ids(self):
        uid = str(uuid4())
        p = make_page(steering_applied=[uid])
        assert uid in p.steering_applied

    def test_generated_at_stored(self):
        p = make_page(status="complete", generated_at=NOW)
        assert p.generated_at == NOW

    def test_two_pages_have_independent_steering_lists(self):
        p1 = Page(page_number=1, beat="a")
        p2 = Page(page_number=2, beat="b")
        p1.steering_applied.append("cmd-1")
        assert "cmd-1" not in p2.steering_applied


class TestPageNumberBounds:
    def test_page_number_1_valid(self):
        assert make_page(page_number=1).page_number == 1

    def test_page_number_5_valid(self):
        assert make_page(page_number=5).page_number == 5

    def test_page_number_0_raises(self):
        with pytest.raises(ValidationError):
            make_page(page_number=0)

    def test_page_number_6_raises(self):
        with pytest.raises(ValidationError):
            make_page(page_number=6)


# ---------------------------------------------------------------------------
# PageAsset — construction and defaults
# ---------------------------------------------------------------------------


class TestPageAssetConstruction:
    def test_minimal_asset_constructs(self):
        a = make_page_asset()
        assert isinstance(a.asset_id, UUID)
        assert a.page_number == 1
        assert a.asset_type == "illustration"
        assert a.generation_status == "pending"
        assert a.gcs_uri is None
        assert a.signed_url is None
        assert a.signed_url_expires_at is None
        assert a.error_detail is None
        assert a.generated_at is None

    def test_asset_id_auto_generated(self):
        a1 = make_page_asset()
        a2 = make_page_asset()
        assert a1.asset_id != a2.asset_id

    def test_explicit_asset_id_accepted(self):
        uid = uuid4()
        a = make_page_asset(asset_id=uid)
        assert a.asset_id == uid

    def test_use_enum_values_stores_string(self):
        a = make_page_asset(
            asset_type=AssetType.narration,
            generation_status=AssetStatus.generating,
        )
        assert a.asset_type == "narration"
        assert a.generation_status == "generating"
        assert isinstance(a.asset_type, str)

    def test_ready_asset_with_gcs_uri(self):
        uri = "gs://my-bucket/sessions/s1/pages/1/illustration.png"
        a = make_page_asset(generation_status="ready", gcs_uri=uri, generated_at=NOW)
        assert a.gcs_uri == uri
        assert a.generated_at == NOW

    def test_signed_url_stored(self):
        a = make_page_asset(
            generation_status="ready",
            gcs_uri="gs://b/s/p/1/illustration.png",
            signed_url="https://storage.googleapis.com/b/illustration.png?token=abc",
            signed_url_expires_at=NOW,
        )
        assert a.signed_url.startswith("https://")
        assert a.signed_url_expires_at == NOW

    def test_failed_asset_with_error_detail(self):
        a = make_page_asset(
            generation_status="failed",
            error_detail="Imagen quota exceeded",
            generated_at=NOW,
        )
        assert a.error_detail == "Imagen quota exceeded"
        assert a.generation_status == "failed"

    def test_narration_asset_constructs(self):
        a = PageAsset(
            page_number=3,
            asset_type="narration",
            generation_status="pending",
        )
        assert a.asset_type == "narration"
        assert a.page_number == 3


class TestPageAssetPageNumberBounds:
    def test_page_number_1_valid(self):
        assert make_page_asset(page_number=1).page_number == 1

    def test_page_number_5_valid(self):
        assert make_page_asset(page_number=5).page_number == 5

    def test_page_number_0_raises(self):
        with pytest.raises(ValidationError):
            make_page_asset(page_number=0)

    def test_page_number_6_raises(self):
        with pytest.raises(ValidationError):
            make_page_asset(page_number=6)
