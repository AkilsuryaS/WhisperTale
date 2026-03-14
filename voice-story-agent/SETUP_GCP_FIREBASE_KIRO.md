# Voice Story Agent Setup (GCP + Firebase Tools + Any IDE)

This guide is a single-source setup checklist for running this project in a different IDE (for example Antigravity / AWS Kiro) without missing config.

It covers:
- Local development setup
- Google Cloud provisioning and IAM
- Backend deploy to Cloud Run
- Frontend deploy via Firebase App Hosting
- Required env vars, secrets, and project-specific values

---

## 1) Project Topology

Repository layout:
- `voice-story-agent/backend` -> FastAPI service (Cloud Run)
- `voice-story-agent/frontend` -> Next.js 14 app (Firebase App Hosting)
- `voice-story-agent/infra` -> helper deploy/provision scripts

Current project defaults in this repo:
- GCP project: `whispertale-dev`
- Primary backend region: `us-central1`
- Frontend App Hosting region (active backend): `us-east4`
- Backend Cloud Run service name: `voice-story-backend`
- Artifact Registry repo: `voice-story-agent` (Docker)

---

## 2) Required Tooling

Install:
- Python `3.11+`
- Node.js `20+`
- `gcloud` CLI
- Docker
- Firebase CLI (`firebase-tools`)

Quick install references:
- gcloud: [https://cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)
- Docker: [https://docs.docker.com/get-docker/](https://docs.docker.com/get-docker/)
- Firebase CLI: `npm install -g firebase-tools`

---

## 3) Authentication (One-Time per Machine)

### Google Cloud auth

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project whispertale-dev
```

### Firebase auth

```bash
firebase login
```

---

## 4) Provision GCP Resources (Recommended)

From repo root:

```bash
cd voice-story-agent
chmod +x infra/setup.sh
./infra/setup.sh whispertale-dev us-central1
```

What this provisions:
- Enables APIs:
  - `firestore.googleapis.com`
  - `storage.googleapis.com`
  - `aiplatform.googleapis.com`
  - `texttospeech.googleapis.com`
  - `run.googleapis.com`
  - `firebase.googleapis.com`
  - `cloudbuild.googleapis.com`
  - `logging.googleapis.com`
- Firestore database: `(default)` in Native mode
- GCS bucket: `whispertale-dev-story-assets`
- Service account: `voice-story-agent-sa@whispertale-dev.iam.gserviceaccount.com`
- IAM roles:
  - `roles/datastore.user`
  - `roles/storage.objectAdmin`
  - `roles/aiplatform.user`
  - `roles/logging.logWriter`
  - `roles/cloudtexttospeech.serviceAgent`

---

## 5) Backend Local Configuration

From `voice-story-agent/backend`:

```bash
cp .env.example .env
```

Use these values in `.env`:

```env
# Core GCP
GCP_PROJECT_ID=whispertale-dev
GCP_REGION=us-central1
GCS_BUCKET_NAME=whispertale-dev-story-assets
FIRESTORE_DATABASE=(default)

# Models
GEMINI_PRO_MODEL=gemini-2.5-pro
GEMINI_FLASH_MODEL=gemini-2.5-flash
IMAGEN_MODEL=imagen-4.0-generate-001
GEMINI_LIVE_MODEL=gemini-2.0-flash-live-001
GEMINI_LIVE_REGION=global

# TTS
TTS_VOICE_NAME=en-US-Journey-F
TTS_LANGUAGE_CODE=en-US

# Agent
ADK_AGENT_NAME=voice-story-agent

# Server
CORS_ORIGINS=http://localhost:3000
```

Optional:
- `GOOGLE_API_KEY=<key>` if using AI Studio endpoint instead of Vertex AI.
- If `GOOGLE_API_KEY` is not set, backend uses Vertex AI + ADC.

---

## 6) Backend Local Run

From `voice-story-agent/backend`:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

---

## 7) Frontend Local Configuration

From `voice-story-agent/frontend`:

```bash
cp .env.local.example .env.local
```

Set:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
NEXT_PUBLIC_WS_BASE_URL=ws://localhost:8000
```

Run:

```bash
npm install
npm run dev
```

Open:
- `http://localhost:3000/story`

---

## 8) Backend Deploy to Cloud Run (Current Project)

Build + push image (Artifact Registry):

```bash
cd voice-story-agent/backend
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/whispertale-dev/voice-story-agent/voice-story-backend:latest \
  --project=whispertale-dev
```

Deploy:

```bash
gcloud run deploy voice-story-backend \
  --image us-central1-docker.pkg.dev/whispertale-dev/voice-story-agent/voice-story-backend:latest \
  --region us-central1 \
  --project whispertale-dev \
  --allow-unauthenticated \
  --timeout=300
```

Route traffic to latest:

```bash
gcloud run services update-traffic voice-story-backend \
  --to-latest \
  --region us-central1 \
  --project whispertale-dev
```

Current backend URL:
- `https://voice-story-backend-vxeq55xwqa-uc.a.run.app`

---

## 9) Firebase App Hosting Setup + Deploy

This repo uses App Hosting (not static export).

Important:
- Do **not** use `output: "export"` in `next.config.mjs`.
- `frontend/apphosting.yaml` expects secrets:
  - `NEXT_PUBLIC_API_BASE_URL`
  - `NEXT_PUBLIC_WS_BASE_URL`

Set secrets:

```bash
firebase apphosting:secrets:set NEXT_PUBLIC_API_BASE_URL --project whispertale-dev
firebase apphosting:secrets:set NEXT_PUBLIC_WS_BASE_URL --project whispertale-dev
```

Recommended secret values:
- `NEXT_PUBLIC_API_BASE_URL=https://voice-story-backend-vxeq55xwqa-uc.a.run.app`
- `NEXT_PUBLIC_WS_BASE_URL=wss://voice-story-backend-vxeq55xwqa-uc.a.run.app`

Deploy trigger:
- App Hosting builds on git push to connected branch.

```bash
cd /path/to/google-hackathon
git push origin main
```

Useful command to inspect App Hosting backends:

```bash
firebase apphosting:backends:list --project whispertale-dev
```

Known current App Hosting backends in this project:
- `voice-story-ui` -> `https://voice-story-ui--whispertale-dev.us-east4.hosted.app`
- `voice-story-frontend` -> `https://voice-story-frontend--whispertale-dev.us-central1.hosted.app`

---

## 10) CORS and Connectivity

When frontend is hosted, set backend CORS to include hosted origin(s):

```bash
gcloud run services update voice-story-backend \
  --region us-central1 \
  --project whispertale-dev \
  --update-env-vars CORS_ORIGINS=https://voice-story-ui--whispertale-dev.us-east4.hosted.app
```

If multiple origins needed, comma-separate:

```env
CORS_ORIGINS=https://voice-story-ui--whispertale-dev.us-east4.hosted.app,http://localhost:3000
```

---

## 11) Verification Checklist

Backend:

```bash
curl https://voice-story-backend-vxeq55xwqa-uc.a.run.app/health
```

Frontend:
- Open `https://voice-story-ui--whispertale-dev.us-east4.hosted.app/story`
- Confirm mic permission prompt appears
- Confirm story creation, page images, and narration
- Confirm mid-story steering updates current + subsequent pages

Logs:

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="voice-story-backend"' \
  --project whispertale-dev \
  --limit 100
```

---

## 12) Common Pitfalls

- Wrong env keys in frontend:
  - Must use `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_WS_BASE_URL`.
- Missing Firebase App Hosting secrets:
  - Build succeeds but frontend cannot call backend correctly.
- CORS not updated for hosted frontend origin:
  - Browser blocks API/WS calls.
- Forgetting `gcloud auth application-default login`:
  - Local Vertex/Firestore/GCS/TTS calls fail.
- Using static export in Next config:
  - Breaks App Hosting SSR behavior.

---

## 13) Quick Bootstrap Commands (Copy/Paste)

```bash
# 1) Clone + auth
git clone <your-repo-url> google-hackathon
cd google-hackathon/voice-story-agent
gcloud auth login
gcloud auth application-default login
gcloud config set project whispertale-dev
firebase login

# 2) Provision GCP
chmod +x infra/setup.sh
./infra/setup.sh whispertale-dev us-central1

# 3) Backend local
cd backend
cp .env.example .env
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 4) Frontend local (new terminal)
cd ../frontend
cp .env.local.example .env.local
npm install
npm run dev
```

