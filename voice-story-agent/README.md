# Voice Story Agent for Children

A real-time voice storytelling agent for children under 12.
Speaks → 5-page illustrated storybook with consistent characters, per-page narration, and mid-story steering.

**Hackathon category**: Creative Storyteller  
**Stack**: FastAPI + Google ADK (Gemini Live) + Gemini 2.5 Pro/Flash + Imagen 3 + Cloud TTS + Firestore + Next.js 14

---

## Quick Start (local, no credentials required)

```bash
# 1. Backend
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # edit GCP_PROJECT_ID when you have one
uvicorn app.main:app --reload --port 8000
# → GET http://localhost:8000/health  returns {"status":"ok"}

# 2. Frontend (coming in T-002)
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
# → http://localhost:3000
```

The backend starts and the health endpoint responds with **zero credentials**.
GCP-dependent features (Gemini, Firestore, GCS, TTS) print a startup warning and
fail gracefully until `GCP_PROJECT_ID` is set and credentials are configured.

---

## Adding Google Cloud Credentials

**Option A — recommended for local development:**
```bash
gcloud auth application-default login
# Then set GCP_PROJECT_ID in backend/.env
```

**Option B — service account key:**
```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json
# Then set GCP_PROJECT_ID in backend/.env
```

Run `infra/setup.sh YOUR_PROJECT_ID` to provision Firestore, GCS, and IAM roles.

---

## Project Structure

```
voice-story-agent/
├── backend/         ← FastAPI Python service (Cloud Run)
├── frontend/        ← Next.js 14 app (Firebase App Hosting)
├── infra/           ← GCP setup scripts
└── README.md
```

See [`specs/001-voice-story-agent/quickstart.md`](../specs/001-voice-story-agent/quickstart.md)
for the full deployment guide.
