#!/usr/bin/env bash
# =============================================================================
# Voice Story Agent — GCP Project Setup
#
# Usage:
#   chmod +x infra/setup.sh
#   ./infra/setup.sh YOUR_GCP_PROJECT_ID [REGION]
#
# What this script does (all steps are safe to re-run):
#   1. Sets the active gcloud project
#   2. Enables all required GCP APIs
#   3. Creates a Firestore database in Native mode  (skips if already exists)
#   4. Creates the GCS story-assets bucket          (skips if already exists)
#   5. Creates the voice-story-agent-sa service account
#   6. Grants all required IAM roles
#   7. Downloads a service-account key to .credentials/sa-key.json
#
# After running, copy the two values into backend/.env:
#   GCP_PROJECT_ID=<your-project-id>
#   GCS_BUCKET_NAME=<your-project-id>-story-assets
# =============================================================================

set -euo pipefail

# ── Argument validation ───────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo ""
  echo "  Usage: ./infra/setup.sh YOUR_GCP_PROJECT_ID [REGION]"
  echo ""
  echo "  Example:"
  echo "    ./infra/setup.sh my-gcp-project us-central1"
  echo ""
  exit 1
fi

PROJECT_ID="$1"
REGION="${2:-us-central1}"
SA_NAME="voice-story-agent-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
BUCKET_NAME="${PROJECT_ID}-story-assets"
CREDENTIALS_DIR="$(dirname "$0")/../.credentials"
KEY_FILE="${CREDENTIALS_DIR}/sa-key.json"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Colour

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
section() { echo ""; echo -e "${GREEN}══ $* ══${NC}"; }

# ── Prerequisites check ───────────────────────────────────────────────────────
section "Prerequisites"

if ! command -v gcloud &>/dev/null; then
  echo -e "${RED}[ERROR]${NC}  gcloud CLI not found."
  echo "  Install it from: https://cloud.google.com/sdk/docs/install"
  exit 1
fi

AUTHED_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
if [[ -z "$AUTHED_ACCOUNT" ]]; then
  echo -e "${RED}[ERROR]${NC}  No active gcloud account found."
  echo "  Run: gcloud auth login"
  exit 1
fi
info "Authenticated as: ${AUTHED_ACCOUNT}"

# ── Set project ───────────────────────────────────────────────────────────────
section "Project"
gcloud config set project "${PROJECT_ID}" --quiet
info "Active project: ${PROJECT_ID}"

# ── Enable APIs ───────────────────────────────────────────────────────────────
section "Enabling GCP APIs (this may take ~60 s on first run)"

APIS=(
  "firestore.googleapis.com"
  "storage.googleapis.com"
  "aiplatform.googleapis.com"
  "texttospeech.googleapis.com"
  "run.googleapis.com"
  "firebase.googleapis.com"
  "cloudbuild.googleapis.com"
  "logging.googleapis.com"
)

gcloud services enable "${APIS[@]}" --project="${PROJECT_ID}" --quiet
info "All APIs enabled."

# ── Firestore ─────────────────────────────────────────────────────────────────
section "Firestore (Native mode)"

if gcloud firestore databases describe --project="${PROJECT_ID}" --quiet &>/dev/null; then
  warn "Firestore database already exists — skipping creation."
  warn "If it is NOT in Native mode, see infra/README.md for manual steps."
else
  gcloud firestore databases create \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --type=firestore-native \
    --quiet \
    && info "Firestore database created in ${REGION}." \
    || warn "Firestore create returned non-zero (may already exist). Check console."
fi

# ── Cloud Storage bucket ──────────────────────────────────────────────────────
section "Cloud Storage bucket: gs://${BUCKET_NAME}"

if gcloud storage buckets describe "gs://${BUCKET_NAME}" --project="${PROJECT_ID}" &>/dev/null; then
  warn "Bucket gs://${BUCKET_NAME} already exists — skipping creation."
else
  gcloud storage buckets create "gs://${BUCKET_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --quiet \
    && info "Bucket gs://${BUCKET_NAME} created." \
    || warn "Bucket creation returned non-zero. Check console."
fi

# ── Service account ───────────────────────────────────────────────────────────
section "Service account: ${SA_NAME}"

if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &>/dev/null; then
  warn "Service account ${SA_EMAIL} already exists — skipping creation."
else
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Voice Story Agent Backend" \
    --project="${PROJECT_ID}" \
    --quiet \
    && info "Service account created: ${SA_EMAIL}"
fi

# ── IAM role bindings ─────────────────────────────────────────────────────────
section "IAM role bindings"

ROLES=(
  "roles/datastore.user"
  "roles/storage.objectAdmin"
  "roles/aiplatform.user"
  "roles/logging.logWriter"
  "roles/cloudtexttospeech.serviceAgent"
)

for ROLE in "${ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" \
    --condition=None \
    --quiet &>/dev/null \
    && info "Granted ${ROLE}" \
    || warn "Could not grant ${ROLE} (may already be bound, or insufficient permissions)."
done

# ── Service account key ───────────────────────────────────────────────────────
section "Service account key"

mkdir -p "${CREDENTIALS_DIR}"

# Add .credentials/ to root .gitignore if not already there
ROOT_GITIGNORE="$(dirname "$0")/../.gitignore"
if ! grep -q "^\.credentials/" "${ROOT_GITIGNORE}" 2>/dev/null; then
  echo ".credentials/" >> "${ROOT_GITIGNORE}"
  info "Added .credentials/ to .gitignore"
fi

if [[ -f "${KEY_FILE}" ]]; then
  warn "Key file already exists at ${KEY_FILE} — skipping download."
  warn "Delete it manually and re-run if you need a fresh key."
else
  gcloud iam service-accounts keys create "${KEY_FILE}" \
    --iam-account="${SA_EMAIL}" \
    --project="${PROJECT_ID}" \
    --quiet \
    && info "Key saved to ${KEY_FILE}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══ Setup complete ══${NC}"
echo ""
echo "  Add these two lines to voice-story-agent/backend/.env:"
echo ""
echo "    GCP_PROJECT_ID=${PROJECT_ID}"
echo "    GCS_BUCKET_NAME=${BUCKET_NAME}"
echo ""
echo "  To use the service account key for local dev:"
echo "    export GOOGLE_APPLICATION_CREDENTIALS=\"$(realpath "${KEY_FILE}" 2>/dev/null || echo "${KEY_FILE}")\""
echo ""
echo "  Or (simpler for personal dev — no key file needed):"
echo "    gcloud auth application-default login"
echo ""
