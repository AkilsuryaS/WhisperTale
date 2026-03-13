# Hackathon Submission Checklist: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12
**Status**: Pre-submission reference — verify each item before final submission

---

## 1. Hackathon Category

| Field | Value |
|-------|-------|
| **Primary category** | **Creative Storyteller** |
| Rationale | The product is an interleaved multimodal storytelling experience: voice input drives a 5-page illustrated storybook with text, Imagen illustrations, and narration audio delivered page-by-page in real time. ADK bidi-streaming + Gemini Live is the conversational voice interaction layer; the storytelling output is the core value delivered. |

- [ ] Submission form category field is set to **Creative Storyteller**
- [ ] Project description does not position this as a "Live Agent" submission

---

## 2. Gemini Models Used

| Model | Purpose | Required for demo |
|-------|---------|-------------------|
| **Gemini 2.5 Pro** | Generates the 5-page story arc outline (called once at setup) | Yes |
| **Gemini 2.5 Flash** | Expands each page beat into full story text; applies steering revisions | Yes |
| **Gemini 2.5 Flash** | Safety classifier + rewrite (single structured-output call per user utterance) | Yes |
| **Gemini Live** (via ADK) | Bidi-streaming conversational voice agent: setup, hold phrases, steering acks, safety rewrite proposals, closing | Yes |

- [ ] All four Gemini model usages are demonstrated in the end-to-end demo
- [ ] No non-Google / non-Gemini LLM is called at any point in any code path

---

## 3. ADK Usage

| ADK Feature | Usage in This Project |
|-------------|----------------------|
| **ADK Python SDK** (`google-adk`) | Manages the bidi-streaming `LiveSession` lifecycle with Gemini Live |
| **Bidi-streaming session** | Receives PCM audio from the browser via WebSocket; yields transcribed turns and agent PCM audio back |
| **System prompt injection** | Character bible, content exclusions, and story persona loaded into ADK session at open time |
| **Turn-based interaction** | Agent speaks hold phrases, setup confirmations, steering acknowledgements; user audio produces `turn_detected` events |

- [ ] ADK SDK version is pinned in `backend/requirements.txt`
- [ ] ADK session open/close lifecycle is exercised in the demo (setup through page 5)
- [ ] ADK usage is visible in Cloud Logging events (`session_created`, `turn_detected`)

---

## 4. Google Cloud Services Used

| Service | Purpose | GCP API to enable |
|---------|---------|-------------------|
| **Gemini Live API** (Vertex AI / AI Platform) | ADK bidi-streaming voice agent | `aiplatform.googleapis.com` |
| **Vertex AI Imagen 3** | Per-page story illustration generation with reference-image anchoring | `aiplatform.googleapis.com` |
| **Google Cloud Text-to-Speech** | Per-page story narration synthesis (Neural2 / WaveNet voice) | `texttospeech.googleapis.com` |
| **Firestore (Native mode)** | Session, page, character bible, and steering state persistence | `firestore.googleapis.com` |
| **Cloud Storage (GCS)** | Stores generated illustrations (PNG) and narration audio (MP3); serves signed URLs | `storage.googleapis.com` |
| **Cloud Run** | Hosts the FastAPI backend; provides HTTPS and WebSocket support | `run.googleapis.com` |
| **Cloud Logging** | Structured JSON logs from the FastAPI backend; filterable by `session_id` | `logging.googleapis.com` |

- [ ] All seven services are enabled in the project (`gcloud services list --enabled`)
- [ ] Service account has required IAM roles: `aiplatform.user`, `datastore.user`, `storage.objectAdmin`, `logging.logWriter`, `cloudtexttospeech.serviceAgent`
- [ ] No third-party AI or storage services are used at any point

---

## 5. Frontend Hosting Target

| Field | Value |
|-------|-------|
| **Primary** | **Firebase App Hosting** |
| Reason | Handles Next.js SSR natively; no static export step; CDN-backed; GCP-native |
| Alternative (fallback only) | Legacy Firebase Hosting with `output: "export"` — disables SSR; use only if App Hosting is unavailable in your region |

- [ ] `frontend/apphosting.yaml` is present and sets `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL`
- [ ] Firebase App Hosting deployment is connected to the project's Git branch
- [ ] Production frontend URL is reachable at `https://{project}.web.app/story`

---

## 6. Backend Hosting Target

| Field | Value |
|-------|-------|
| **Service** | **Cloud Run** |
| Region | `us-central1` (recommended; change if Gemini Live not available) |
| Key flags | `--session-affinity` (WebSocket), `--min-instances=1` (no cold start on demo), `--timeout=300` (long voice sessions) |

- [ ] Cloud Run service is deployed and `GET /health` returns `{"status": "ok"}`
- [ ] WebSocket endpoint `wss://{cloud-run-url}/ws/story/{session_id}` accepts connections
- [ ] `CORS_ORIGINS` is set to the Firebase App Hosting URL

---

## 7. Public Repository Requirement

- [ ] Repository is **public** on GitHub (or the hackathon-approved platform)
- [ ] Repository was **created during the contest period** (verify git initial commit date)
- [ ] `README.md` at repo root explains the project, how to run it locally, and how to deploy
- [ ] No API keys, service account JSON files, or `.env` files are committed
- [ ] `.gitignore` excludes `backend/.env`, `frontend/.env.local`, `*.json` key files

---

## 8. Demo Proof Checklist

Each item must be demonstrable live or reproducible from a recorded video:

- [ ] **Voice setup**: A user completes the full story setup using only voice (no keyboard input)
- [ ] **Safety rewrite**: Saying "a story where the dragon destroys the city" triggers the safety agent aloud; no unsafe word appears in any caption, page text, or image
- [ ] **5-page delivery**: All 5 pages are delivered in order with text + illustration + narration audio
- [ ] **Character consistency**: The protagonist in page 5 illustration matches the visual identity from page 1 (same color, species, body proportions)
- [ ] **Steering (stretch)**: Saying "give the monster a bird friend" during a steering window adds the bird to page N+1 and all subsequent pages
- [ ] **Hold animation**: A loading animation and hold phrase are visible between every page
- [ ] **Captions**: Captions appear within 2 seconds for 100% of agent and user speech
- [ ] **Cloud-only**: The demo runs end-to-end on Google Cloud — no local fallback services active

---

## 9. Rule-to-Feature Mapping

Verifies the project satisfies the hackathon compliance rules:

| Hackathon Rule | How This Project Satisfies It |
|----------------|-------------------------------|
| Use Gemini models | Gemini 2.5 Pro (story arc), Gemini 2.5 Flash (page text + safety), Gemini Live via ADK (conversational voice) |
| Use ADK | ADK Python SDK manages the Gemini Live bidi-streaming session throughout every story session |
| Deploy on Google Cloud | Cloud Run (backend), Firebase App Hosting (frontend), Firestore, GCS, Vertex AI — all GCP |
| Creative Storyteller category | Interleaved multimodal output: voice → text + illustration + narration, page-by-page, real-time steerable |
| No third-party AI services | All AI: Gemini models + Imagen 3 on Vertex AI + Cloud TTS — zero non-GCP AI calls |
| Public repository | GitHub repo, created during contest period, with README |

---

## 10. New Project Built During Contest Period

- [ ] Git log confirms the **initial commit** was made on or after the contest start date
- [ ] No substantial code was copied or adapted from a pre-existing private repository
- [ ] Any open-source library dependencies are properly licensed (MIT / Apache 2.0 preferred)
- [ ] The project was not previously submitted to another hackathon or published publicly before the contest start date

---

## Quick Verification Commands

```bash
# Verify all required GCP services are enabled
gcloud services list --enabled --filter="name:(run OR aiplatform OR firestore OR storage OR texttospeech OR logging OR firebase)" --project=YOUR_PROJECT_ID

# Smoke-test Cloud Run backend
curl https://YOUR_CLOUD_RUN_URL/health
# → {"status": "ok"}

# Create a session (end-to-end API smoke test)
curl -X POST https://YOUR_CLOUD_RUN_URL/sessions
# → {"session_id": "...", "ws_url": "wss://..."}

# Verify Firebase App Hosting frontend
curl -I https://YOUR_PROJECT.web.app/story
# → HTTP/2 200
```
