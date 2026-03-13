"""
Tests for T-008: SessionStore — Page and PageAsset CRUD.

Strategy: inject a mock AsyncClient identical to T-007 tests.

Firestore paths covered:
    sessions/{id}/pages/{n}             ← Page  (document ID = str(page_number))
    sessions/{id}/pages/{n}/assets/{t}  ← PageAsset (document ID = asset_type)

Covers:
- save_page: calls .set() with serialised Page data; document ID is page_number string
- get_page: returns Page when exists; returns None when missing
- list_pages: returns pages ordered by page_number; returns [] when empty
- save_page_asset: calls .set() on pages/{n}/assets/{type}; accepts enum or string
- get_page_asset: returns PageAsset when exists; returns None when missing
- list_page_assets: returns both assets for a page; returns [] when empty
- update_page_asset_status:
    - updates generation_status
    - includes gcs_uri when supplied
    - sets generated_at when status is "ready" or "failed"
    - does NOT set generated_at for "generating"
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.page import AssetStatus, AssetType, Page, PageAsset, PageStatus
from app.services.session_store import SessionStore

NOW = datetime.now(timezone.utc)
SESSION_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(page_number: int = 1, **overrides) -> Page:
    defaults = dict(
        page_number=page_number,
        beat="Spark discovers a glowing mushroom",
        status=PageStatus.pending,
    )
    defaults.update(overrides)
    return Page(**defaults)


def _make_page_asset(
    page_number: int = 1,
    asset_type: str = "illustration",
    **overrides,
) -> PageAsset:
    defaults = dict(
        page_number=page_number,
        asset_type=asset_type,
        generation_status=AssetStatus.pending,
    )
    defaults.update(overrides)
    return PageAsset(**defaults)


def _mock_doc(exists: bool, data: dict | None = None) -> MagicMock:
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict = MagicMock(return_value=data or {})
    return doc


def _make_store() -> tuple[SessionStore, MagicMock]:
    client = MagicMock()
    store = SessionStore(client=client)
    return store, client


def _wire_page_ref(client: MagicMock, doc_mock: MagicMock) -> MagicMock:
    """Wire client → sessions → document → pages → document(n) ref."""
    page_doc_ref = MagicMock()
    page_doc_ref.get = AsyncMock(return_value=doc_mock)
    page_doc_ref.set = AsyncMock()
    page_doc_ref.update = AsyncMock()
    pages_col = MagicMock()
    pages_col.document = MagicMock(return_value=page_doc_ref)
    pages_col.order_by = MagicMock(return_value=pages_col)
    pages_col.get = AsyncMock(return_value=[])
    session_doc = MagicMock()
    session_doc.collection = MagicMock(return_value=pages_col)
    top_col = MagicMock()
    top_col.document = MagicMock(return_value=session_doc)
    client.collection = MagicMock(return_value=top_col)
    return page_doc_ref


def _wire_asset_ref(client: MagicMock, doc_mock: MagicMock) -> MagicMock:
    """Wire full chain: sessions → doc → pages → doc(n) → assets → doc(type)."""
    asset_doc_ref = MagicMock()
    asset_doc_ref.get = AsyncMock(return_value=doc_mock)
    asset_doc_ref.set = AsyncMock()
    asset_doc_ref.update = AsyncMock()
    assets_col = MagicMock()
    assets_col.document = MagicMock(return_value=asset_doc_ref)
    assets_col.get = AsyncMock(return_value=[])
    page_doc_ref = MagicMock()
    page_doc_ref.collection = MagicMock(return_value=assets_col)
    pages_col = MagicMock()
    pages_col.document = MagicMock(return_value=page_doc_ref)
    session_doc = MagicMock()
    session_doc.collection = MagicMock(return_value=pages_col)
    top_col = MagicMock()
    top_col.document = MagicMock(return_value=session_doc)
    client.collection = MagicMock(return_value=top_col)
    return asset_doc_ref


# ---------------------------------------------------------------------------
# save_page
# ---------------------------------------------------------------------------


class TestSavePage:
    @pytest.mark.asyncio
    async def test_calls_set_with_serialised_data(self):
        page = _make_page(page_number=1)
        store, client = _make_store()
        page_ref = _wire_page_ref(client, _mock_doc(exists=False))

        await store.save_page(SESSION_ID, page)

        page_ref.set.assert_called_once()
        payload = page_ref.set.call_args[0][0]
        assert payload["page_number"] == 1
        assert payload["beat"] == "Spark discovers a glowing mushroom"
        assert payload["status"] == "pending"
        assert payload["illustration_failed"] is False

    @pytest.mark.asyncio
    async def test_document_id_is_page_number_string(self):
        page = _make_page(page_number=3)
        store, client = _make_store()
        pages_col = MagicMock()
        page_ref = MagicMock()
        page_ref.set = AsyncMock()
        pages_col.document = MagicMock(return_value=page_ref)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_page(SESSION_ID, page)

        pages_col.document.assert_called_with("3")

    @pytest.mark.asyncio
    async def test_serialises_text_when_set(self):
        page = _make_page(
            page_number=2,
            status=PageStatus.text_ready,
            text="Spark soared over the treetops.",
        )
        store, client = _make_store()
        page_ref = _wire_page_ref(client, _mock_doc(exists=False))

        await store.save_page(SESSION_ID, page)

        payload = page_ref.set.call_args[0][0]
        assert payload["text"] == "Spark soared over the treetops."
        assert payload["status"] == "text_ready"


# ---------------------------------------------------------------------------
# get_page
# ---------------------------------------------------------------------------


class TestGetPage:
    @pytest.mark.asyncio
    async def test_returns_page_when_exists(self):
        page = _make_page(page_number=1)
        data = page.model_dump(mode="json")
        store, client = _make_store()
        _wire_page_ref(client, _mock_doc(exists=True, data=data))

        result = await store.get_page(SESSION_ID, 1)

        assert result is not None
        assert result.page_number == 1
        assert result.beat == "Spark discovers a glowing mushroom"

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self):
        store, client = _make_store()
        _wire_page_ref(client, _mock_doc(exists=False))

        result = await store.get_page(SESSION_ID, 1)

        assert result is None

    @pytest.mark.asyncio
    async def test_round_trip_preserves_all_fields(self):
        page = _make_page(
            page_number=2,
            status=PageStatus.complete,
            text="Some story text.",
            narration_script="Some narration.",
            illustration_failed=True,
            steering_applied=["cmd-1"],
            generated_at=NOW,
        )
        data = page.model_dump(mode="json")
        store, client = _make_store()
        _wire_page_ref(client, _mock_doc(exists=True, data=data))

        result = await store.get_page(SESSION_ID, 2)

        assert result.status == "complete"
        assert result.illustration_failed is True
        assert result.steering_applied == ["cmd-1"]


# ---------------------------------------------------------------------------
# list_pages
# ---------------------------------------------------------------------------


class TestListPages:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_pages(self):
        store, client = _make_store()
        query_mock = MagicMock()
        query_mock.get = AsyncMock(return_value=[])
        pages_col = MagicMock()
        pages_col.order_by = MagicMock(return_value=query_mock)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_pages(SESSION_ID)
        assert result == []

    @pytest.mark.asyncio
    async def test_orders_by_page_number(self):
        store, client = _make_store()
        query_mock = MagicMock()
        query_mock.get = AsyncMock(return_value=[])
        pages_col = MagicMock()
        pages_col.order_by = MagicMock(return_value=query_mock)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.list_pages(SESSION_ID)

        pages_col.order_by.assert_called_with("page_number")

    @pytest.mark.asyncio
    async def test_returns_multiple_pages(self):
        p1 = _make_page(page_number=1)
        p2 = _make_page(page_number=2, beat="Beat 2")

        def _make_doc(page):
            d = MagicMock()
            d.to_dict = MagicMock(return_value=page.model_dump(mode="json"))
            return d

        store, client = _make_store()
        query_mock = MagicMock()
        query_mock.get = AsyncMock(return_value=[_make_doc(p1), _make_doc(p2)])
        pages_col = MagicMock()
        pages_col.order_by = MagicMock(return_value=query_mock)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_pages(SESSION_ID)

        assert len(result) == 2
        assert result[0].page_number == 1
        assert result[1].page_number == 2


# ---------------------------------------------------------------------------
# save_page_asset
# ---------------------------------------------------------------------------


class TestSavePageAsset:
    @pytest.mark.asyncio
    async def test_calls_set_with_serialised_data(self):
        asset = _make_page_asset(page_number=1, asset_type="illustration")
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=False))

        await store.save_page_asset(SESSION_ID, asset)

        asset_ref.set.assert_called_once()
        payload = asset_ref.set.call_args[0][0]
        assert payload["page_number"] == 1
        assert payload["asset_type"] == "illustration"
        assert payload["generation_status"] == "pending"

    @pytest.mark.asyncio
    async def test_document_id_is_asset_type_string(self):
        asset = _make_page_asset(page_number=2, asset_type="narration")
        store, client = _make_store()
        assets_col = MagicMock()
        asset_ref = MagicMock()
        asset_ref.set = AsyncMock()
        assets_col.document = MagicMock(return_value=asset_ref)
        page_doc = MagicMock()
        page_doc.collection = MagicMock(return_value=assets_col)
        pages_col = MagicMock()
        pages_col.document = MagicMock(return_value=page_doc)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_page_asset(SESSION_ID, asset)

        assets_col.document.assert_called_with("narration")

    @pytest.mark.asyncio
    async def test_accepts_enum_asset_type(self):
        asset = _make_page_asset(asset_type=AssetType.illustration)
        store, client = _make_store()
        assets_col = MagicMock()
        asset_ref = MagicMock()
        asset_ref.set = AsyncMock()
        assets_col.document = MagicMock(return_value=asset_ref)
        page_doc = MagicMock()
        page_doc.collection = MagicMock(return_value=assets_col)
        pages_col = MagicMock()
        pages_col.document = MagicMock(return_value=page_doc)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        await store.save_page_asset(SESSION_ID, asset)

        assets_col.document.assert_called_with("illustration")


# ---------------------------------------------------------------------------
# get_page_asset
# ---------------------------------------------------------------------------


class TestGetPageAsset:
    @pytest.mark.asyncio
    async def test_returns_asset_when_exists(self):
        asset = _make_page_asset(page_number=1, asset_type="illustration")
        data = asset.model_dump(mode="json")
        store, client = _make_store()
        _wire_asset_ref(client, _mock_doc(exists=True, data=data))

        result = await store.get_page_asset(SESSION_ID, 1, AssetType.illustration)

        assert result is not None
        assert result.asset_type == "illustration"
        assert result.page_number == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self):
        store, client = _make_store()
        _wire_asset_ref(client, _mock_doc(exists=False))

        result = await store.get_page_asset(SESSION_ID, 1, "narration")

        assert result is None

    @pytest.mark.asyncio
    async def test_round_trip_preserves_gcs_uri(self):
        uri = "gs://bucket/sessions/s1/pages/1/illustration.png"
        asset = _make_page_asset(
            generation_status="ready",
            gcs_uri=uri,
        )
        data = asset.model_dump(mode="json")
        store, client = _make_store()
        _wire_asset_ref(client, _mock_doc(exists=True, data=data))

        result = await store.get_page_asset(SESSION_ID, 1, "illustration")

        assert result.gcs_uri == uri
        assert result.generation_status == "ready"


# ---------------------------------------------------------------------------
# list_page_assets
# ---------------------------------------------------------------------------


class TestListPageAssets:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_assets(self):
        store, client = _make_store()
        _wire_asset_ref(client, _mock_doc(exists=False))

        result = await store.list_page_assets(SESSION_ID, 1)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_both_assets(self):
        illus = _make_page_asset(asset_type="illustration")
        narr = _make_page_asset(asset_type="narration")

        def _make_doc(asset):
            d = MagicMock()
            d.to_dict = MagicMock(return_value=asset.model_dump(mode="json"))
            return d

        store, client = _make_store()
        assets_col = MagicMock()
        assets_col.get = AsyncMock(return_value=[_make_doc(illus), _make_doc(narr)])
        page_doc = MagicMock()
        page_doc.collection = MagicMock(return_value=assets_col)
        pages_col = MagicMock()
        pages_col.document = MagicMock(return_value=page_doc)
        session_doc = MagicMock()
        session_doc.collection = MagicMock(return_value=pages_col)
        top_col = MagicMock()
        top_col.document = MagicMock(return_value=session_doc)
        client.collection = MagicMock(return_value=top_col)

        result = await store.list_page_assets(SESSION_ID, 1)

        assert len(result) == 2
        types = {a.asset_type for a in result}
        assert types == {"illustration", "narration"}


# ---------------------------------------------------------------------------
# update_page_asset_status
# ---------------------------------------------------------------------------


class TestUpdatePageAssetStatus:
    @pytest.mark.asyncio
    async def test_updates_generation_status(self):
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 1, "illustration", AssetStatus.generating
        )

        asset_ref.update.assert_called_once()
        payload = asset_ref.update.call_args[0][0]
        assert payload["generation_status"] == "generating"

    @pytest.mark.asyncio
    async def test_includes_gcs_uri_when_supplied(self):
        uri = "gs://bucket/sessions/s1/pages/1/illustration.png"
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 1, "illustration", AssetStatus.ready, gcs_uri=uri
        )

        payload = asset_ref.update.call_args[0][0]
        assert payload["gcs_uri"] == uri

    @pytest.mark.asyncio
    async def test_sets_generated_at_when_ready(self):
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 1, "illustration", AssetStatus.ready
        )

        payload = asset_ref.update.call_args[0][0]
        assert "generated_at" in payload

    @pytest.mark.asyncio
    async def test_sets_generated_at_when_failed(self):
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 1, "narration", AssetStatus.failed
        )

        payload = asset_ref.update.call_args[0][0]
        assert "generated_at" in payload

    @pytest.mark.asyncio
    async def test_does_not_set_generated_at_when_generating(self):
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 1, "illustration", AssetStatus.generating
        )

        payload = asset_ref.update.call_args[0][0]
        assert "generated_at" not in payload

    @pytest.mark.asyncio
    async def test_no_gcs_uri_in_payload_when_not_supplied(self):
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 1, "illustration", AssetStatus.generating
        )

        payload = asset_ref.update.call_args[0][0]
        assert "gcs_uri" not in payload

    @pytest.mark.asyncio
    async def test_accepts_enum_asset_type_and_status(self):
        store, client = _make_store()
        asset_ref = _wire_asset_ref(client, _mock_doc(exists=True))

        await store.update_page_asset_status(
            SESSION_ID, 2, AssetType.narration, AssetStatus.ready
        )

        payload = asset_ref.update.call_args[0][0]
        assert payload["generation_status"] == "ready"
