# Quickstart: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12
**Goal**: Running local dev environment → full end-to-end demo on Google Cloud.

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | `brew install python@3.11` |
| Node.js | 20 LTS+ | `brew install node` |
| gcloud CLI | latest | https://cloud.google.com/sdk/docs/install |
| Docker | latest | https://docs.docker.com/get-docker/ |
| Firebase CLI | latest | `npm install -g firebase-tools` |

GCP services that must be enabled in your project:
```bash
gcloud services enable \
  run.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  storage.googleapis.com \
  texttospeech.googleapis.com \
  logging.googleapis.com \
  firebase.googleapis.com
```

---

## 1. Clone and Configure

```bash
git clone <repo-url> voice-story-agent
cd voice-story-agent
```

### 1a. GCP Project Setup

```bash
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT_ID
gcloud auth application-default login
```

### 1b. Create Cloud Storage bucket

```bash
gcloud storage buckets create gs://YOUR_GCP_PROJECT_ID-story-assets \
  --location=us-central1 \
  --uniform-bucket-level-access
```

### 1c. Create Firestore database (Native mode)

```bash
gcloud firestore databases create --location=us-central1
```

### 1d. Environment variables

Copy the template and fill in your values:

```bash
cp backend/.env.example backend/.env
```

`backend/.env`:
```
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=us-central1
GCS_BUCKET_NAME=your-project-id-story-assets
GEMINI_MODEL_PRO=gemini-2.5-pro
GEMINI_MODEL_FLASH=gemini-2.5-flash
IMAGEN_MODEL=imagen-3.0-generate-001
TTS_VOICE_NAME=en-US-Neural2-F
TTS_LANGUAGE_CODE=en-US
ADK_AGENT_NAME=voice-story-agent
FIRESTORE_DATABASE=(default)
CORS_ORIGINS=http://localhost:3000
```

`frontend/.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

---

## 2. Backend (FastAPI)

```bash
cd backend

# Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run development server (with hot reload)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Verify backend is healthy:
```bash
curl http://localhost:8000/health
# → {"status": "ok"}
```

---

## 3. Frontend (Next.js)

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
# → http://localhost:3000
```

Open http://localhost:3000 in Chrome (required for WebAudio + microphone API).
Grant microphone permission when prompted.

---

## 4. Running Tests

### Backend

```bash
cd backend
source .venv/bin/activate

# All tests
pytest

# Safety middleware tests (critical path)
pytest tests/test_safety_middleware.py -v

# Story orchestration tests
pytest tests/test_page_orchestration.py -v

# Character bible tests
pytest tests/test_character_bible.py -v
```

### Frontend

```bash
cd frontend
npm test
```

---

## 5. End-to-End Demo (Local)

1. Start the backend: `uvicorn app.main:app --reload` (port 8000)
2. Start the frontend: `npm run dev` (port 3000)
3. Open http://localhost:3000 in Chrome
4. Click the microphone button and say:
   > "Tell me a story about a little purple monster who makes too much noise"
5. Answer the agent's follow-up questions (protagonist details, setting, tone)
6. Watch page 1 generate: text appears → illustration appears → narration plays
7. During narration of page 1, interrupt:
   > "Give the monster a tiny yellow bird friend"
8. Confirm pages 2–5 include the bird with visual consistency

**Demo safety test**:
- Say: "A story where the dragon destroys the whole city"
- Verify the agent proposes a safe rewrite aloud, waits for acknowledgement, then
  proceeds — and that "destroys" or "city destruction" appears nowhere in any caption

---

## 6. Deploy to Google Cloud

### 6a. Deploy Backend to Cloud Run

```bash
cd backend

# Build and push Docker image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/voice-story-agent-backend

# Deploy to Cloud Run
gcloud run deploy voice-story-agent-backend \
  --image gcr.io/YOUR_PROJECT_ID/voice-story-agent-backend \
  --region us-central1 \
  --allow-unauthenticated \
  --session-affinity \
  --min-instances 1 \
  --set-env-vars GCP_PROJECT_ID=YOUR_PROJECT_ID,GCS_BUCKET_NAME=YOUR_PROJECT_ID-story-assets \
  --service-account voice-story-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

Note the Cloud Run service URL (e.g., `https://voice-story-agent-backend-xxx-uc.a.run.app`).

### 6b. Create Service Account with Required Roles

```bash
gcloud iam service-accounts create voice-story-agent-sa \
  --display-name="Voice Story Agent Backend"

for ROLE in \
  roles/aiplatform.user \
  roles/datastore.user \
  roles/storage.objectAdmin \
  roles/logging.logWriter \
  roles/cloudtexttospeech.serviceAgent; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:voice-story-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="$ROLE"
done
```

### 6c. Deploy Frontend to Firebase Hosting

Update `frontend/.env.production`:
```
NEXT_PUBLIC_API_URL=https://voice-story-agent-backend-xxx-uc.a.run.app
NEXT_PUBLIC_WS_URL=wss://voice-story-agent-backend-xxx-uc.a.run.app
```

```bash
cd frontend
npm run build

firebase login
firebase init hosting   # select existing Firebase project
firebase deploy --only hosting
```

Firebase Hosting URL will be displayed (e.g., `https://your-project.web.app`).

### 6d. Update CORS

Set `CORS_ORIGINS` on Cloud Run to match your Firebase Hosting URL:
```bash
gcloud run services update voice-story-agent-backend \
  --region us-central1 \
  --update-env-vars CORS_ORIGINS=https://your-project.web.app
```

---

## 7. Verify Cloud Deployment

```bash
# Health check
curl https://voice-story-agent-backend-xxx-uc.a.run.app/health

# Create a test session
curl -X POST https://voice-story-agent-backend-xxx-uc.a.run.app/sessions
# → {"session_id": "...", "ws_url": "wss://..."}
```

Open your Firebase Hosting URL in Chrome and run the full 5-page demo.

---

## 8. Observability

View structured logs for a specific session:
```bash
gcloud logging read \
  'jsonPayload.session_id="YOUR_SESSION_ID"' \
  --project=YOUR_PROJECT_ID \
  --limit=100 \
  --format=json | jq '.[] | {time: .timestamp, event: .jsonPayload.event, page: .jsonPayload.page}'
```

Key log events to check after a demo run:
```
session_created → setup_complete → safety_triggered (if applicable)
→ page_generation_started (×5) → page_complete (×5) → story_complete
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| No microphone access | Browser permission denied | Enable mic in Chrome site settings |
| WebSocket connects then immediately closes | CORS misconfiguration | Verify `CORS_ORIGINS` env var matches frontend URL |
| Imagen returns 403 | Service account missing `aiplatform.user` role | Re-run IAM grant commands in §6b |
| TTS returns empty audio | Voice name not available in region | Change `TTS_VOICE_NAME` to `en-US-Wavenet-F` |
| Cloud Run cold start on first request | `--min-instances` not set | Set `--min-instances=1` for demo |
| Page image fails silently | GCS bucket CORS not configured | `gcloud storage buckets update gs://... --cors-file=cors.json` |
| ADK session drops mid-narration | Cloud Run default timeout (60 s) hit | Set `--timeout=300` on Cloud Run |
