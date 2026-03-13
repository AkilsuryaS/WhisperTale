"""
Tests for T-001: Backend scaffold — health endpoint and Settings guards.

Covers:
- GET /health returns {"status": "ok"} with HTTP 200 (no credentials required)
- Settings.require_gcp raises RuntimeError when GCP_PROJECT_ID is unset
- Settings.require_gcp returns the project ID when it is set
- Settings.require_gcs_bucket raises RuntimeError when GCS_BUCKET_NAME is unset
- Settings.require_gcs_bucket returns the bucket name when it is set
- Settings.startup_warnings returns warnings for unset optional vars
- Settings.cors_origins_list splits a comma-separated CORS_ORIGINS string
"""

from __future__ import annotations

import pytest

from app.config import Settings


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_status_ok(self, client):
        response = client.get("/health")
        assert response.json() == {"status": "ok"}

    def test_health_content_type_is_json(self, client):
        response = client.get("/health")
        assert "application/json" in response.headers["content-type"]


# ---------------------------------------------------------------------------
# Settings.require_gcp
# ---------------------------------------------------------------------------


class TestRequireGcp:
    def test_raises_when_project_id_unset(self):
        s = Settings(GCP_PROJECT_ID=None)
        with pytest.raises(RuntimeError, match="GCP_PROJECT_ID"):
            s.require_gcp("TestService")

    def test_returns_project_id_when_set(self):
        s = Settings(GCP_PROJECT_ID="my-test-project")
        assert s.require_gcp("TestService") == "my-test-project"

    def test_error_message_names_the_service(self):
        s = Settings(GCP_PROJECT_ID=None)
        with pytest.raises(RuntimeError, match="MySpecialService"):
            s.require_gcp("MySpecialService")


# ---------------------------------------------------------------------------
# Settings.require_gcs_bucket
# ---------------------------------------------------------------------------


class TestRequireGcsBucket:
    def test_raises_when_bucket_unset(self):
        s = Settings(GCS_BUCKET_NAME=None)
        with pytest.raises(RuntimeError, match="GCS_BUCKET_NAME"):
            s.require_gcs_bucket()

    def test_returns_bucket_name_when_set(self):
        s = Settings(GCS_BUCKET_NAME="my-project-story-assets")
        assert s.require_gcs_bucket() == "my-project-story-assets"


# ---------------------------------------------------------------------------
# Settings.startup_warnings
# ---------------------------------------------------------------------------


class TestStartupWarnings:
    def test_warnings_present_when_both_unset(self):
        s = Settings(GCP_PROJECT_ID=None, GCS_BUCKET_NAME=None)
        warnings = s.startup_warnings()
        assert len(warnings) == 2
        assert any("GCP_PROJECT_ID" in w for w in warnings)
        assert any("GCS_BUCKET_NAME" in w for w in warnings)

    def test_no_warnings_when_both_set(self):
        s = Settings(GCP_PROJECT_ID="proj", GCS_BUCKET_NAME="bucket")
        assert s.startup_warnings() == []

    def test_only_bucket_warning_when_project_set(self):
        s = Settings(GCP_PROJECT_ID="proj", GCS_BUCKET_NAME=None)
        warnings = s.startup_warnings()
        assert len(warnings) == 1
        assert "GCS_BUCKET_NAME" in warnings[0]

    def test_only_project_warning_when_bucket_set(self):
        s = Settings(GCP_PROJECT_ID=None, GCS_BUCKET_NAME="bucket")
        warnings = s.startup_warnings()
        assert len(warnings) == 1
        assert "GCP_PROJECT_ID" in warnings[0]


# ---------------------------------------------------------------------------
# Settings.cors_origins_list
# ---------------------------------------------------------------------------


class TestCorsOriginsList:
    def test_single_origin(self):
        s = Settings(CORS_ORIGINS="http://localhost:3000")
        assert s.cors_origins_list == ["http://localhost:3000"]

    def test_multiple_origins_split_by_comma(self):
        s = Settings(CORS_ORIGINS="http://localhost:3000,https://example.com")
        assert s.cors_origins_list == ["http://localhost:3000", "https://example.com"]

    def test_whitespace_around_commas_is_stripped(self):
        s = Settings(CORS_ORIGINS="http://localhost:3000 , https://example.com")
        assert s.cors_origins_list == ["http://localhost:3000", "https://example.com"]

    def test_empty_string_returns_empty_list(self):
        s = Settings(CORS_ORIGINS="")
        assert s.cors_origins_list == []
