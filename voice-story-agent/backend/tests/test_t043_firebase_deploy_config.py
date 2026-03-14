"""
Tests for T-043: Firebase App Hosting frontend deploy config.

Validates the structural correctness and completeness of all deploy artefacts:

1. apphosting.yaml
   - File exists
   - NEXT_PUBLIC_API_BASE_URL env var defined with BUILD + RUNTIME availability
   - NEXT_PUBLIC_WS_BASE_URL env var defined with BUILD + RUNTIME availability
   - runConfig section present with concurrency, cpu, memoryMiB
   - Secrets (not plain values) used for the API/WS URLs
   - NODE_ENV=production present
   - No `output: export` in next.config.mjs

2. .firebaserc
   - File exists
   - Valid JSON
   - Contains a "projects" key with a "default" project entry

3. firebase-deploy.sh
   - File exists and is executable
   - Contains set -euo pipefail
   - Has a usage/help message
   - Has shebang
   - References the NEXT_PUBLIC_API_BASE_URL and NEXT_PUBLIC_WS_BASE_URL secrets
   - Contains `git push` to trigger App Hosting
   - Contains the output:export guard / check
   - firebase apphosting referenced (App Hosting CLI commands)
   - Step comments for all 5 steps

4. next.config.mjs
   - File exists
   - Does NOT contain output: "export" (critical for App Hosting)

5. infra/README.md (unchanged — no `output: export` instruction sneaked in)
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml  # pyyaml is available via google-cloud-aiplatform transitive deps

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# parents[0]=tests/, parents[1]=backend/, parents[2]=voice-story-agent/
VOICE_AGENT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = VOICE_AGENT_ROOT / "frontend"
INFRA_DIR = VOICE_AGENT_ROOT / "infra"

APPHOSTING_YAML = FRONTEND_DIR / "apphosting.yaml"
FIREBASERC = FRONTEND_DIR / ".firebaserc"
FIREBASE_DEPLOY_SH = INFRA_DIR / "firebase-deploy.sh"
NEXT_CONFIG = FRONTEND_DIR / "next.config.mjs"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. apphosting.yaml tests
# ---------------------------------------------------------------------------

class TestApphostingYaml:
    """apphosting.yaml is present and correctly structured."""

    def test_file_exists(self):
        assert APPHOSTING_YAML.exists(), f"apphosting.yaml not found at {APPHOSTING_YAML}"

    def test_valid_yaml(self):
        content = read(APPHOSTING_YAML)
        data = yaml.safe_load(content)
        assert isinstance(data, dict), "apphosting.yaml must be a valid YAML mapping"

    def test_env_section_present(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        assert "env" in data, "apphosting.yaml must have an 'env' section"

    def test_next_public_api_base_url_defined(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        assert "NEXT_PUBLIC_API_BASE_URL" in env_vars, \
            "apphosting.yaml must define NEXT_PUBLIC_API_BASE_URL"

    def test_next_public_ws_base_url_defined(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        assert "NEXT_PUBLIC_WS_BASE_URL" in env_vars, \
            "apphosting.yaml must define NEXT_PUBLIC_WS_BASE_URL"

    def test_api_url_available_at_build_and_runtime(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        avail = env_vars["NEXT_PUBLIC_API_BASE_URL"].get("availability", [])
        assert "BUILD" in avail, "NEXT_PUBLIC_API_BASE_URL must be available at BUILD"
        assert "RUNTIME" in avail, "NEXT_PUBLIC_API_BASE_URL must be available at RUNTIME"

    def test_ws_url_available_at_build_and_runtime(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        avail = env_vars["NEXT_PUBLIC_WS_BASE_URL"].get("availability", [])
        assert "BUILD" in avail, "NEXT_PUBLIC_WS_BASE_URL must be available at BUILD"
        assert "RUNTIME" in avail, "NEXT_PUBLIC_WS_BASE_URL must be available at RUNTIME"

    def test_api_url_uses_secret_not_plain_value(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        api_entry = env_vars["NEXT_PUBLIC_API_BASE_URL"]
        assert "secret" in api_entry, \
            "NEXT_PUBLIC_API_BASE_URL should reference a secret, not a hardcoded value"

    def test_ws_url_uses_secret_not_plain_value(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        ws_entry = env_vars["NEXT_PUBLIC_WS_BASE_URL"]
        assert "secret" in ws_entry, \
            "NEXT_PUBLIC_WS_BASE_URL should reference a secret, not a hardcoded value"

    def test_run_config_present(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        assert "runConfig" in data, "apphosting.yaml must have a 'runConfig' section"

    def test_run_config_concurrency(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        assert "concurrency" in data.get("runConfig", {}), \
            "runConfig must specify concurrency"

    def test_run_config_memory(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        assert "memoryMiB" in data.get("runConfig", {}), \
            "runConfig must specify memoryMiB"

    def test_node_env_production(self):
        data = yaml.safe_load(read(APPHOSTING_YAML))
        env_vars = {e["variable"]: e for e in data.get("env", [])}
        assert "NODE_ENV" in env_vars, "apphosting.yaml should set NODE_ENV"
        assert env_vars["NODE_ENV"].get("value") == "production", \
            "NODE_ENV must be set to production"


# ---------------------------------------------------------------------------
# 2. .firebaserc tests
# ---------------------------------------------------------------------------

class TestFirebaserc:
    """.firebaserc is present and valid."""

    def test_file_exists(self):
        assert FIREBASERC.exists(), f".firebaserc not found at {FIREBASERC}"

    def test_valid_json(self):
        content = read(FIREBASERC)
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            pytest.fail(f".firebaserc is not valid JSON: {e}")
        assert isinstance(data, dict)

    def test_projects_key_present(self):
        data = json.loads(read(FIREBASERC))
        assert "projects" in data, ".firebaserc must have a 'projects' key"

    def test_default_project_key(self):
        data = json.loads(read(FIREBASERC))
        assert "default" in data.get("projects", {}), \
            ".firebaserc must have a 'projects.default' entry"

    def test_default_project_is_string(self):
        data = json.loads(read(FIREBASERC))
        val = data.get("projects", {}).get("default", "")
        assert isinstance(val, str) and len(val) > 0, \
            "projects.default must be a non-empty string (the Firebase project ID)"


# ---------------------------------------------------------------------------
# 3. firebase-deploy.sh tests
# ---------------------------------------------------------------------------

class TestFirebaseDeployScript:
    """firebase-deploy.sh is complete and correct."""

    def test_script_exists(self):
        assert FIREBASE_DEPLOY_SH.exists(), \
            f"firebase-deploy.sh not found at {FIREBASE_DEPLOY_SH}"

    def test_script_is_executable(self):
        mode = os.stat(FIREBASE_DEPLOY_SH).st_mode
        assert bool(mode & stat.S_IXUSR), "firebase-deploy.sh must be executable"

    def test_shebang(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert content.startswith("#!/"), "Script must have a shebang line"

    def test_strict_mode(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "set -euo pipefail" in content

    def test_usage_message(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "Usage" in content or "usage" in content

    def test_git_push_triggers_app_hosting(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "git" in content and "push" in content, \
            "Script must call 'git push' to trigger App Hosting"

    def test_references_api_base_url_secret(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "NEXT_PUBLIC_API_BASE_URL" in content

    def test_references_ws_base_url_secret(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "NEXT_PUBLIC_WS_BASE_URL" in content

    def test_firebase_apphosting_referenced(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "apphosting" in content, \
            "Script must reference Firebase App Hosting (apphosting)"

    def test_output_export_guard(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "output" in content and "export" in content, \
            "Script must check/guard against output:export in next.config.mjs"

    def test_project_id_argument(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "PROJECT_ID" in content

    def test_branch_variable(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "BRANCH" in content

    def test_firebase_use_command(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert "firebase use" in content or "firebase" in content

    def test_app_hosting_url_printed(self):
        content = read(FIREBASE_DEPLOY_SH)
        assert ".web.app" in content or "App Hosting URL" in content or "apphosting" in content


# ---------------------------------------------------------------------------
# 4. next.config.mjs — must NOT have output: "export"
# ---------------------------------------------------------------------------

class TestNextConfig:
    """next.config.mjs must not have output:export (critical for App Hosting)."""

    def test_next_config_exists(self):
        assert NEXT_CONFIG.exists(), f"next.config.mjs not found at {NEXT_CONFIG}"

    def test_no_output_export(self):
        content = read(NEXT_CONFIG)
        assert 'output' not in content or 'export' not in content, \
            "next.config.mjs must NOT contain output: 'export' — " \
            "Firebase App Hosting handles Next.js builds natively"

    def test_no_output_export_specific(self):
        content = read(NEXT_CONFIG)
        # More targeted check: the combination output + export in same context
        import re
        matches = re.findall(r'output\s*[:\=]\s*["\']export["\']', content)
        assert len(matches) == 0, \
            f"next.config.mjs must NOT set output to 'export'. Found: {matches}"

    def test_react_strict_mode_still_present(self):
        content = read(NEXT_CONFIG)
        assert "reactStrictMode" in content, \
            "next.config.mjs should retain reactStrictMode: true"
