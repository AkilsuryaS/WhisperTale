#!/usr/bin/env bash
# =============================================================================
# Voice Story Agent — Cloud Run backend deploy script
#
# Usage:
#   chmod +x infra/cloud-run-deploy.sh
#   ./infra/cloud-run-deploy.sh YOUR_GCP_PROJECT_ID [REGION]
#
# What this script does:
#   1. docker build   — builds the multi-stage backend image
#   2. docker push    — pushes it to Google Container Registry (gcr.io)
#   3. gcloud run deploy — deploys / updates the Cloud Run service
#   4. Print the deployed service URL
#
# Prerequisites:
#   • gcloud CLI authenticated  (gcloud auth login / application-default)
#   • docker CLI available and logged in to gcr.io
#       gcloud auth configure-docker
#   • Cloud Run API enabled on the project
#       gcloud services enable run.googleapis.com --project=$PROJECT_ID
#   • Service account voice-story-agent-sa already created
#       Run infra/setup.sh first if you haven't already.
#
# Environment variables that can override defaults:
#   PROJECT_ID         — GCP project ID (or pass as arg 1)
#   REGION             — GCP region (default: us-central1)
#   IMAGE_TAG          — image tag  (default: latest)
#   SERVICE_NAME       — Cloud Run service name (default: voice-story-backend)
#
# All application config values from backend/app/config.py are forwarded to
# Cloud Run as --set-env-vars.  Override any of them via shell env before
# calling this script.
# =============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}   $*"; }
error()   { echo -e "${RED}[ERROR]${NC}  $*" >&2; }
section() { echo ""; echo -e "${CYAN}══ $* ══${NC}"; }

# ── Argument / env validation ─────────────────────────────────────────────────
PROJECT_ID="${1:-${PROJECT_ID:-}}"
REGION="${2:-${REGION:-us-central1}}"

if [[ -z "${PROJECT_ID}" ]]; then
  error "GCP project ID is required."
  echo ""
  echo "  Usage: ./infra/cloud-run-deploy.sh YOUR_GCP_PROJECT_ID [REGION]"
  echo ""
  exit 1
fi

IMAGE_TAG="${IMAGE_TAG:-latest}"
SERVICE_NAME="${SERVICE_NAME:-voice-story-backend}"
SA_NAME="${SA_NAME:-voice-story-agent-sa}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Container image URI
IMAGE_URI="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:${IMAGE_TAG}"

# Path to backend directory (relative to the repo root, not this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${SCRIPT_DIR}/../backend"

# ── Prerequisites check ───────────────────────────────────────────────────────
section "Prerequisites"

for cmd in gcloud docker; do
  if ! command -v "${cmd}" &>/dev/null; then
    error "${cmd} CLI not found. Please install it before running this script."
    exit 1
  fi
done

AUTHED_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
if [[ -z "${AUTHED_ACCOUNT}" ]]; then
  error "No active gcloud account found. Run: gcloud auth login"
  exit 1
fi
info "Authenticated as: ${AUTHED_ACCOUNT}"
info "Project: ${PROJECT_ID}  |  Region: ${REGION}  |  Tag: ${IMAGE_TAG}"

# ── Step 1: docker build ──────────────────────────────────────────────────────
section "Step 1 — docker build"

info "Building image: ${IMAGE_URI}"
docker build \
  --tag "${IMAGE_URI}" \
  --file "${BACKEND_DIR}/Dockerfile" \
  "${BACKEND_DIR}"

info "Build complete."

# ── Step 2: docker push ───────────────────────────────────────────────────────
section "Step 2 — docker push"

info "Pushing image: ${IMAGE_URI}"
gcloud auth configure-docker --quiet
docker push "${IMAGE_URI}"

info "Push complete."

# ── Step 3: gcloud run deploy ─────────────────────────────────────────────────
section "Step 3 — Cloud Run deploy"

# ── Application env vars (all config.py settings) ────────────────────────────
# These can be overridden before calling the script by setting the same-named
# shell variables.  Empty values are forwarded as-is; Cloud Run will use them
# to override the image defaults.

GCP_PROJECT_ID_VAL="${GCP_PROJECT_ID:-${PROJECT_ID}}"
GCP_REGION_VAL="${GCP_REGION:-${REGION}}"
GCS_BUCKET_NAME_VAL="${GCS_BUCKET_NAME:-${PROJECT_ID}-story-assets}"
FIRESTORE_DATABASE_VAL="${FIRESTORE_DATABASE:-(default)}"
GEMINI_PRO_MODEL_VAL="${GEMINI_PRO_MODEL:-gemini-2.5-pro}"
GEMINI_FLASH_MODEL_VAL="${GEMINI_FLASH_MODEL:-gemini-2.5-flash}"
GEMINI_FLASH_IMAGE_MODEL_VAL="${GEMINI_FLASH_IMAGE_MODEL:-gemini-2.5-flash-image}"
IMAGEN_MODEL_VAL="${IMAGEN_MODEL:-imagen-4.0-fast-generate-001}"
GEMINI_LIVE_MODEL_VAL="${GEMINI_LIVE_MODEL:-gemini-2.0-flash-live-preview-04-09}"
TTS_VOICE_NAME_VAL="${TTS_VOICE_NAME:-en-US-Neural2-F}"
TTS_LANGUAGE_CODE_VAL="${TTS_LANGUAGE_CODE:-en-US}"
ADK_AGENT_NAME_VAL="${ADK_AGENT_NAME:-voice-story-agent}"
CORS_ORIGINS_VAL="${CORS_ORIGINS:-}"

ENV_VARS="GCP_PROJECT_ID=${GCP_PROJECT_ID_VAL}"
ENV_VARS="${ENV_VARS},GCP_REGION=${GCP_REGION_VAL}"
ENV_VARS="${ENV_VARS},GCS_BUCKET_NAME=${GCS_BUCKET_NAME_VAL}"
ENV_VARS="${ENV_VARS},FIRESTORE_DATABASE=${FIRESTORE_DATABASE_VAL}"
ENV_VARS="${ENV_VARS},GEMINI_PRO_MODEL=${GEMINI_FLASH_MODEL_VAL}"
ENV_VARS="${ENV_VARS},GEMINI_FLASH_MODEL=${GEMINI_FLASH_MODEL_VAL}"
ENV_VARS="${ENV_VARS},GEMINI_FLASH_IMAGE_MODEL=${GEMINI_FLASH_IMAGE_MODEL_VAL}"
ENV_VARS="${ENV_VARS},IMAGEN_MODEL=${IMAGEN_MODEL_VAL}"
ENV_VARS="${ENV_VARS},GEMINI_LIVE_MODEL=${GEMINI_LIVE_MODEL_VAL}"
ENV_VARS="${ENV_VARS},TTS_VOICE_NAME=${TTS_VOICE_NAME_VAL}"
ENV_VARS="${ENV_VARS},TTS_LANGUAGE_CODE=${TTS_LANGUAGE_CODE_VAL}"
ENV_VARS="${ENV_VARS},ADK_AGENT_NAME=${ADK_AGENT_NAME_VAL}"
ENV_VARS="${ENV_VARS},PYTHONUNBUFFERED=1"

if [[ -n "${CORS_ORIGINS_VAL}" ]]; then
  ENV_VARS="${ENV_VARS},CORS_ORIGINS=${CORS_ORIGINS_VAL}"
fi

info "Deploying service '${SERVICE_NAME}' to ${REGION}…"

gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE_URI}" \
  --region="${REGION}" \
  --platform=managed \
  --allow-unauthenticated \
  --service-account="${SA_EMAIL}" \
  --set-env-vars="${ENV_VARS}" \
  --min-instances=0 \
  --max-instances=10 \
  --memory=512Mi \
  --cpu=1 \
  --timeout=600 \
  --concurrency=80 \
  --port=8080 \
  --project="${PROJECT_ID}" \
  --quiet

# ── Step 4: Print the deployed service URL ────────────────────────────────────
section "Step 4 — Service URL"

SERVICE_URL=$(
  gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format="value(status.url)"
)

if [[ -z "${SERVICE_URL}" ]]; then
  warn "Could not retrieve service URL. Check Cloud Console."
else
  info "Deployment complete!"
  echo ""
  echo -e "  ${GREEN}Service URL:${NC}  ${SERVICE_URL}"
  echo ""
  echo "  Health check:"
  echo "    curl ${SERVICE_URL}/health"
  echo ""
  echo "  WebSocket endpoint:"
  echo "    wss://${SERVICE_URL#https://}/ws/story/{session_id}"
  echo ""
  echo "  Add to frontend/.env.local:"
  echo "    NEXT_PUBLIC_API_BASE_URL=${SERVICE_URL}"
  echo "    NEXT_PUBLIC_WS_BASE_URL=wss://${SERVICE_URL#https://}"
  echo ""
fi
