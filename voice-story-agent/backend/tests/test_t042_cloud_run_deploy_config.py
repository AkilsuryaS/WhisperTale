"""
Tests for T-042: Cloud Run backend deploy config.

Since we cannot run docker/gcloud in CI, these tests validate the
structural correctness and completeness of all deploy artefacts:

1. Dockerfile
   - Final stage starts from python:3.11-slim
   - Non-root user (appuser) is used
   - HEALTHCHECK instruction is present
   - EXPOSE 8080 is present
   - CMD starts uvicorn on $PORT
   - PYTHONUNBUFFERED=1 is set
   - Multi-stage: both builder and runtime stages present

2. cloud-run-deploy.sh
   - All four deploy steps are present (build, push, run deploy, URL print)
   - All config.py env vars are forwarded (GCP_PROJECT_ID, GCS_BUCKET_NAME, etc.)
   - --allow-unauthenticated flag is present
   - --service-account flag is present
   - Script uses set -euo pipefail
   - Usage message is present
   - Script is executable

3. deploy-backend.yml
   - Triggered on push to main
   - Triggered on workflow_dispatch
   - Uses google-github-actions/auth step
   - docker build step present
   - docker push step present
   - gcloud run deploy step present
   - Health-check smoke test step present
   - GCP_PROJECT_ID secret referenced
   - All config.py env vars set in gcloud run deploy command
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# parents[0] = tests/, parents[1] = backend/, parents[2] = voice-story-agent/
VOICE_AGENT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = VOICE_AGENT_ROOT / "backend"
INFRA_DIR = VOICE_AGENT_ROOT / "infra"
WORKFLOWS_DIR = VOICE_AGENT_ROOT / ".github" / "workflows"

DOCKERFILE = BACKEND_DIR / "Dockerfile"
DEPLOY_SCRIPT = INFRA_DIR / "cloud-run-deploy.sh"
WORKFLOW_FILE = WORKFLOWS_DIR / "deploy-backend.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Dockerfile tests
# ---------------------------------------------------------------------------

class TestDockerfile:
    """Dockerfile is finalised and production-ready."""

    def test_dockerfile_exists(self):
        assert DOCKERFILE.exists(), f"Dockerfile not found at {DOCKERFILE}"

    def test_multi_stage_builder_present(self):
        content = read(DOCKERFILE)
        assert "AS builder" in content, "Dockerfile missing builder stage"

    def test_multi_stage_runtime_present(self):
        content = read(DOCKERFILE)
        assert "AS runtime" in content, "Dockerfile missing runtime stage"

    def test_python_311_base(self):
        content = read(DOCKERFILE)
        assert "python:3.11-slim" in content

    def test_non_root_user(self):
        content = read(DOCKERFILE)
        assert "appuser" in content, "Dockerfile should use a non-root user"
        assert "USER appuser" in content

    def test_expose_8080(self):
        content = read(DOCKERFILE)
        assert "EXPOSE 8080" in content

    def test_pythonunbuffered(self):
        content = read(DOCKERFILE)
        assert "PYTHONUNBUFFERED=1" in content

    def test_cmd_uvicorn(self):
        content = read(DOCKERFILE)
        assert "uvicorn" in content
        assert "app.main:app" in content

    def test_healthcheck_present(self):
        content = read(DOCKERFILE)
        assert "HEALTHCHECK" in content, "Dockerfile must include a HEALTHCHECK instruction"

    def test_healthcheck_uses_health_endpoint(self):
        content = read(DOCKERFILE)
        assert "/health" in content

    def test_port_env_var(self):
        content = read(DOCKERFILE)
        assert "PORT" in content, "Dockerfile should reference the PORT env var"


# ---------------------------------------------------------------------------
# 2. cloud-run-deploy.sh tests
# ---------------------------------------------------------------------------

class TestCloudRunDeployScript:
    """cloud-run-deploy.sh is complete and correct."""

    def test_script_exists(self):
        assert DEPLOY_SCRIPT.exists(), f"Deploy script not found at {DEPLOY_SCRIPT}"

    def test_script_is_executable(self):
        mode = os.stat(DEPLOY_SCRIPT).st_mode
        assert bool(mode & stat.S_IXUSR), "cloud-run-deploy.sh must be executable"

    def test_strict_mode(self):
        content = read(DEPLOY_SCRIPT)
        assert "set -euo pipefail" in content

    def test_step1_docker_build(self):
        content = read(DEPLOY_SCRIPT)
        assert "docker build" in content, "Script must include docker build step"

    def test_step2_docker_push(self):
        content = read(DEPLOY_SCRIPT)
        assert "docker push" in content, "Script must include docker push step"

    def test_step3_gcloud_run_deploy(self):
        content = read(DEPLOY_SCRIPT)
        assert "gcloud run deploy" in content, "Script must include gcloud run deploy step"

    def test_step4_service_url_printed(self):
        content = read(DEPLOY_SCRIPT)
        assert "Service URL" in content or "service URL" in content or "SERVICE_URL" in content

    def test_allow_unauthenticated(self):
        content = read(DEPLOY_SCRIPT)
        assert "--allow-unauthenticated" in content

    def test_service_account_flag(self):
        content = read(DEPLOY_SCRIPT)
        assert "--service-account" in content

    def test_usage_message(self):
        content = read(DEPLOY_SCRIPT)
        assert "Usage" in content or "usage" in content

    def test_shebang(self):
        content = read(DEPLOY_SCRIPT)
        assert content.startswith("#!/"), "Script must have a shebang line"

    # All config.py env vars must be forwarded
    @pytest.mark.parametrize("env_var", [
        "GCP_PROJECT_ID",
        "GCS_BUCKET_NAME",
        "FIRESTORE_DATABASE",
        "GEMINI_PRO_MODEL",
        "GEMINI_FLASH_MODEL",
        "IMAGEN_MODEL",
        "GEMINI_LIVE_MODEL",
        "TTS_VOICE_NAME",
        "TTS_LANGUAGE_CODE",
        "ADK_AGENT_NAME",
        "CORS_ORIGINS",
    ])
    def test_env_var_forwarded(self, env_var: str):
        content = read(DEPLOY_SCRIPT)
        assert env_var in content, f"Deploy script must forward env var {env_var}"

    def test_region_flag(self):
        content = read(DEPLOY_SCRIPT)
        assert "--region" in content

    def test_platform_managed(self):
        content = read(DEPLOY_SCRIPT)
        assert "--platform=managed" in content or "--platform managed" in content

    def test_image_uri_uses_gcr(self):
        content = read(DEPLOY_SCRIPT)
        assert "gcr.io" in content

    def test_port_8080_flag(self):
        content = read(DEPLOY_SCRIPT)
        assert "--port=8080" in content or "--port 8080" in content


# ---------------------------------------------------------------------------
# 3. deploy-backend.yml tests
# ---------------------------------------------------------------------------

class TestDeployBackendWorkflow:
    """GitHub Actions workflow is complete and correct."""

    def test_workflow_file_exists(self):
        assert WORKFLOW_FILE.exists(), f"Workflow file not found at {WORKFLOW_FILE}"

    def test_triggers_on_push_to_main(self):
        content = read(WORKFLOW_FILE)
        assert "main" in content
        assert "push" in content

    def test_trigger_on_workflow_dispatch(self):
        content = read(WORKFLOW_FILE)
        assert "workflow_dispatch" in content

    def test_gcloud_auth_step(self):
        content = read(WORKFLOW_FILE)
        assert "google-github-actions/auth" in content

    def test_docker_build_step(self):
        content = read(WORKFLOW_FILE)
        assert "docker build" in content

    def test_docker_push_step(self):
        content = read(WORKFLOW_FILE)
        assert "docker push" in content

    def test_gcloud_run_deploy_step(self):
        content = read(WORKFLOW_FILE)
        assert "gcloud run deploy" in content

    def test_health_smoke_test(self):
        content = read(WORKFLOW_FILE)
        assert "/health" in content

    def test_gcp_project_id_secret(self):
        content = read(WORKFLOW_FILE)
        assert "GCP_PROJECT_ID" in content

    def test_gcp_sa_key_secret(self):
        content = read(WORKFLOW_FILE)
        assert "GCP_SA_KEY" in content

    def test_allow_unauthenticated_flag(self):
        content = read(WORKFLOW_FILE)
        assert "--allow-unauthenticated" in content

    def test_service_account_flag(self):
        content = read(WORKFLOW_FILE)
        assert "--service-account" in content

    @pytest.mark.parametrize("env_var", [
        "GCP_PROJECT_ID",
        "GCS_BUCKET_NAME",
        "GEMINI_PRO_MODEL",
        "GEMINI_FLASH_MODEL",
        "IMAGEN_MODEL",
        "GEMINI_LIVE_MODEL",
        "TTS_VOICE_NAME",
        "TTS_LANGUAGE_CODE",
    ])
    def test_env_var_in_workflow(self, env_var: str):
        content = read(WORKFLOW_FILE)
        assert env_var in content, f"Workflow must reference env var {env_var}"

    def test_uses_ubuntu_runner(self):
        content = read(WORKFLOW_FILE)
        assert "ubuntu-latest" in content

    def test_path_filter_on_backend(self):
        content = read(WORKFLOW_FILE)
        assert "backend" in content

    def test_paths_filter_includes_workflow(self):
        content = read(WORKFLOW_FILE)
        # The workflow should filter on its own path to allow self-updates
        assert "deploy-backend.yml" in content
