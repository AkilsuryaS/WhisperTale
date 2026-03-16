# Building WhisperTale: A Voice-First AI Storytelling Agent on Google AI + Google Cloud

## Disclosure

I created this piece of content for the purposes of entering the **Gemini Live Agent Challenge** hackathon.  
When sharing this project on social media, please use the hashtag **#GeminiLiveAgentChallenge**.

---

## Why I Built WhisperTale

Children are natural storytellers, but most AI experiences still start with typing. I wanted to invert that pattern and build a voice-native storytelling experience where a child can simply speak an idea and receive a living, illustrated story that responds in real time.

The result is **WhisperTale**: a live voice storytelling agent that:
- listens to a child’s spoken prompt,
- asks follow-up questions naturally,
- generates a five-page story arc,
- creates per-page illustrations and narration audio,
- and supports mid-story voice steering (for example, “add a yellow bird friend”).

Instead of a single chatbot response, WhisperTale behaves like an interactive creative partner.

---

## What the Product Does (End-to-End)

### 1) Voice Setup Conversation
A child presses and holds the talk button in the browser and speaks an idea.  
The backend streams audio to **Gemini Live API** through **Google ADK**, receives transcripts and turns, and asks clarifying questions (name, setting, mood, etc.).

### 2) Story Planning and Character Grounding
Once enough context is collected, the system:
- generates a structured story brief,
- builds a **Character Bible** for visual consistency,
- and plans a five-beat arc for page-by-page generation.

### 3) Per-Page Generation Pipeline
For each page, WhisperTale orchestrates:
- text generation (Gemini),
- image generation (Imagen),
- narration synthesis (Google Cloud Text-to-Speech),
- asset persistence (Google Cloud Storage),
- and UI updates via WebSocket to stream results progressively.

### 4) Mid-Story Steering
Children can interrupt and redirect the story while it is running.  
The agent classifies the steering command, updates planning for remaining pages, and continues generation while preserving continuity.

### 5) Child-Safety Layer
Before unsafe output can be produced, a safety pass detects problematic requests and proposes age-appropriate rewrites. This keeps the experience creative while still safe for young users.

---

## How I Built It with Google AI Models

WhisperTale is intentionally multimodal and model-specialized:

- **Gemini Live API (`gemini-2.0-flash-live-001`) + Google ADK**  
  Handles low-latency bidirectional voice interaction and turn-taking.

- **Gemini 2.5 Pro / Flash**  
  Used for story planning, page narrative generation, and transformation tasks where fast iteration and coherence are required.

- **Imagen 4 Fast (with Imagen 3 fallback)**  
  Generates per-page illustrations aligned with Character Bible constraints to reduce visual drift.

- **Google Cloud Text-to-Speech**  
  Converts generated page text into spoken narration so each page is both readable and listenable.

The key lesson: model quality matters, but orchestration matters more. A polished experience came from sequencing, state design, and resilience handling across all model calls.

---

## How I Built It on Google Cloud

### Core Cloud Architecture
- **Cloud Run** for the FastAPI backend and WebSocket handling.
- **Firestore (Native mode)** for sessions, pages, and metadata state.
- **Cloud Storage** for generated image/audio assets.
- **Firebase App Hosting** for the Next.js frontend deployment.
- **Cloud Logging** for operational observability and session-level troubleshooting.

### Why This Stack Worked
Managed services let me focus on agent behavior instead of infra plumbing:
- rapid provisioning,
- easy scaling,
- repeatable deployments,
- and clearer debugging in a hackathon timeframe.

---

## Proof I Automated Cloud Deployment (Code in Public Repo)

One of my goals was to avoid manual “click-ops” and keep deployment reproducible. I implemented deployment automation using shell scripts and CI/CD workflow files, and these are committed in the repository.

### A) Infrastructure Provisioning Script
**File:** `voice-story-agent/infra/setup.sh`

This script automates:
- enabling required Google APIs,
- creating Firestore database,
- creating Cloud Storage bucket,
- creating service account,
- applying IAM roles,
- and generating local credentials for development.

Run pattern:

```bash
cd voice-story-agent
chmod +x infra/setup.sh
./infra/setup.sh YOUR_GCP_PROJECT_ID us-central1
```

### B) Backend Cloud Run Deployment Script
**File:** `voice-story-agent/infra/cloud-run-deploy.sh`

This script automates:
- Docker image build,
- push to Google Container Registry,
- Cloud Run deploy/update with environment variables and runtime settings,
- and printing the deployed service URL plus health-check guidance.

Run pattern:

```bash
cd voice-story-agent
chmod +x infra/cloud-run-deploy.sh
./infra/cloud-run-deploy.sh YOUR_GCP_PROJECT_ID us-central1
```

### C) Frontend Firebase App Hosting Deployment Script
**File:** `voice-story-agent/infra/firebase-deploy.sh`

This script automates:
- CLI/project validation,
- branch push trigger to App Hosting build pipeline,
- and retrieval of deployment URL details.

Run pattern:

```bash
cd voice-story-agent
chmod +x infra/firebase-deploy.sh
./infra/firebase-deploy.sh YOUR_FIREBASE_PROJECT_ID main
```

### D) CI/CD Workflow for Backend
**File:** `voice-story-agent/.github/workflows/deploy-backend.yml`

This GitHub Actions workflow automates:
- build and push of backend container image on `main`,
- Cloud Run deployment,
- service URL output,
- and smoke test against `/health`.

### E) App Hosting Configuration as Code
**File:** `voice-story-agent/frontend/apphosting.yaml`

This defines runtime and secret-backed environment config for frontend hosting, including:
- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_WS_BASE_URL`

Together, these files prove deployment automation is codified and versioned in the public repository.

---

## Engineering Decisions and Lessons Learned

### 1) Consistency Required Structured State
A Character Bible (not just prompt text) materially improved cross-page visual consistency.

### 2) Real-Time UX Is an Orchestration Problem
Progressive streaming and event timing mattered as much as model outputs.

### 3) Safety Works Best as a First-Class Flow
Embedding safety decisions directly in the conversation pipeline created a better child-safe UX than a purely post-generation filter.

### 4) Managed Cloud Services Compressed Build Time
Using Google Cloud managed primitives helped me ship a production-like multimodal system within hackathon constraints.

---

## What I’d Build Next

- age-band personalization and parent control settings,
- long-term memory for recurring characters/worlds across sessions,
- multilingual voice support for narration and interaction,
- richer telemetry dashboards for latency, safety interventions, and completion metrics.

---

## Closing

WhisperTale demonstrates how Google AI models and Google Cloud can be combined into a truly interactive agent experience, not just a static response generator. The project’s strongest signal is not one model call, but the full loop: listen, reason, generate, speak, and adapt in real time.

If you share this project publicly, please include **#GeminiLiveAgentChallenge**.
