#!/usr/bin/env bash
# =============================================================================
# Voice Story Agent — Firebase App Hosting frontend deploy script
#
# Usage:
#   chmod +x infra/firebase-deploy.sh
#   ./infra/firebase-deploy.sh YOUR_FIREBASE_PROJECT_ID [BRANCH]
#
# What this script does:
#   Firebase App Hosting builds and deploys automatically when a commit is
#   pushed to the connected Git branch.  This script:
#     1. Validates prerequisites (firebase CLI, git, authed account)
#     2. Sets the correct Firebase project
#     3. Verifies the frontend has no `output: "export"` in next.config.mjs
#     4. Pushes the specified branch to origin — App Hosting picks it up and
#        triggers an automatic build + deploy
#     5. Prints the App Hosting URL
#
# Prerequisites:
#   • firebase CLI installed:  npm install -g firebase-tools
#   • Authenticated:           firebase login
#   • App Hosting backend created in Firebase console:
#       Firebase console → App Hosting → Add backend → connect Git repo
#       OR via CLI: firebase apphosting:backends:create
#   • Secrets set in Firebase console / CLI:
#       firebase apphosting:secrets:set NEXT_PUBLIC_API_BASE_URL
#       firebase apphosting:secrets:set NEXT_PUBLIC_WS_BASE_URL
#
# Environment variables that can override defaults:
#   PROJECT_ID    — Firebase project ID (or pass as arg 1)
#   BRANCH        — Git branch to push (default: main)
#   BACKEND_ID    — App Hosting backend ID (default: voice-story-frontend)
#   REMOTE        — Git remote name (default: origin)
#
# NOTE: Do NOT set output: "export" in next.config.mjs.
#       Firebase App Hosting handles Next.js builds natively.
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
BRANCH="${2:-${BRANCH:-main}}"

if [[ -z "${PROJECT_ID}" ]]; then
  error "Firebase project ID is required."
  echo ""
  echo "  Usage: ./infra/firebase-deploy.sh YOUR_FIREBASE_PROJECT_ID [BRANCH]"
  echo ""
  echo "  Examples:"
  echo "    ./infra/firebase-deploy.sh my-firebase-project"
  echo "    ./infra/firebase-deploy.sh my-firebase-project staging"
  echo ""
  exit 1
fi

BACKEND_ID="${BACKEND_ID:-voice-story-frontend}"
REMOTE="${REMOTE:-origin}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${SCRIPT_DIR}/../frontend"
NEXT_CONFIG="${FRONTEND_DIR}/next.config.mjs"

# ── Step 1: Prerequisites check ───────────────────────────────────────────────
section "Step 1 — Prerequisites"

for cmd in firebase git; do
  if ! command -v "${cmd}" &>/dev/null; then
    error "${cmd} CLI not found."
    if [[ "${cmd}" == "firebase" ]]; then
      echo "  Install with: npm install -g firebase-tools"
    fi
    exit 1
  fi
done

FIREBASE_ACCOUNT=$(firebase login:list 2>/dev/null | grep "^  " | head -1 | xargs || echo "")
if [[ -z "${FIREBASE_ACCOUNT}" ]]; then
  warn "No Firebase account detected. Run: firebase login"
  warn "Continuing — firebase CLI will prompt if needed."
fi

info "Project: ${PROJECT_ID}  |  Branch: ${BRANCH}  |  Backend: ${BACKEND_ID}"

# ── Step 2: Set Firebase project ──────────────────────────────────────────────
section "Step 2 — Set Firebase project"

firebase use "${PROJECT_ID}" --project="${PROJECT_ID}" 2>/dev/null || {
  warn "Could not run 'firebase use'. Ensure the project is linked in ${FRONTEND_DIR}/.firebaserc"
  warn "Continuing — the push step will still trigger App Hosting."
}

info "Firebase project set to: ${PROJECT_ID}"

# ── Step 3: Verify no static export flag ──────────────────────────────────────
section "Step 3 — Verify Next.js config"

if [[ -f "${NEXT_CONFIG}" ]]; then
  if grep -q 'output.*export' "${NEXT_CONFIG}" 2>/dev/null; then
    error "next.config.mjs contains 'output: \"export\"'."
    error "Firebase App Hosting requires a full Next.js build — remove that line."
    exit 1
  fi
  info "next.config.mjs: no static-export flag found. ✅"
else
  warn "next.config.mjs not found at ${NEXT_CONFIG}. Skipping check."
fi

# ── Step 4: Push branch to origin (triggers App Hosting build) ───────────────
section "Step 4 — Push to ${REMOTE}/${BRANCH} (triggers App Hosting)"

CURRENT_BRANCH=$(git -C "${SCRIPT_DIR}/.." rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
info "Current branch: ${CURRENT_BRANCH}"

if [[ "${CURRENT_BRANCH}" != "${BRANCH}" ]]; then
  warn "You are on branch '${CURRENT_BRANCH}', not '${BRANCH}'."
  warn "Pushing '${CURRENT_BRANCH}' to '${REMOTE}/${BRANCH}'."
fi

git -C "${SCRIPT_DIR}/.." push "${REMOTE}" "${CURRENT_BRANCH}:${BRANCH}"
info "Push complete. Firebase App Hosting will now build and deploy the frontend."

# ── Step 5: Print App Hosting URL ─────────────────────────────────────────────
section "Step 5 — App Hosting URL"

APP_HOSTING_URL=""
if command -v firebase &>/dev/null; then
  # Try to retrieve the live URL from the App Hosting backend
  APP_HOSTING_URL=$(
    firebase apphosting:backends:get "${BACKEND_ID}" \
      --project="${PROJECT_ID}" \
      --format=json 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('uri',''))" 2>/dev/null \
    || echo ""
  )
fi

if [[ -n "${APP_HOSTING_URL}" ]]; then
  echo ""
  echo -e "  ${GREEN}App Hosting URL:${NC}  ${APP_HOSTING_URL}"
  echo ""
  echo "  Story page:   ${APP_HOSTING_URL}/story"
  echo ""
else
  echo ""
  echo -e "  ${YELLOW}App Hosting URL could not be retrieved automatically.${NC}"
  echo "  Check the Firebase console: https://console.firebase.google.com/project/${PROJECT_ID}/apphosting"
  echo ""
  echo "  Expected URL pattern:  https://${PROJECT_ID}.web.app"
  echo "  Story page:            https://${PROJECT_ID}.web.app/story"
  echo ""
fi

echo "  Add the deployed Cloud Run backend URL to Firebase secrets:"
echo "    firebase apphosting:secrets:set NEXT_PUBLIC_API_BASE_URL --project ${PROJECT_ID}"
echo "    firebase apphosting:secrets:set NEXT_PUBLIC_WS_BASE_URL --project ${PROJECT_ID}"
echo ""
