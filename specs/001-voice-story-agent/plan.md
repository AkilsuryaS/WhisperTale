# Implementation Plan: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-voice-story-agent/spec.md`

## Summary

A real-time voice storytelling agent for children under 12. A caregiver or child speaks to
initiate and steer a personalized 5-page illustrated storybook. The system detects and rewrites
any unsafe content conversationally, then generates each page as a coherent triple of story
text + Imagen illustration + live voice narration — streamed page-by-page so mid-story steering
immediately reshapes future pages. Character visual consistency is enforced by feeding the
page-1 illustration as a reference image into every subsequent Imagen call.

## Technical Context

**Language/Version**: Python 3.11 (backend), TypeScript / Next.js 14 (frontend)
**Primary Dependencies**: FastAPI, Google ADK (bidi-streaming), Gemini Live API,
  Gemini 2.5 Pro, Gemini 2.5 Flash, Imagen 3 on Vertex AI, Cloud Text-to-Speech,
  Firestore, Cloud Storage, Cloud Run, Firebase Hosting, Cloud Logging,
  Next.js + React + Tailwind CSS
**Storage**: Firestore (session + story state), Cloud Storage (images + audio)
**Testing**: pytest (backend), Jest + React Testing Library (frontend)
**Target Platform**: Google Cloud (Cloud Run + Firebase Hosting)
**Project Type**: Full-stack web application with real-time voice agent
**Performance Goals**: Page 1 first-word narration within 8 s of setup confirmation;
  each subsequent page ready within 15 s of steering window close
**Constraints**: Sessions ephemeral in browser; Firestore persists story state for
  demo recovery; all content must pass safety filter before any generation call
**Scale/Scope**: Single concurrent session per device; 5-page story MVP;
  English language only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Child Safety First | Safety middleware MUST execute before every Gemini generation call; unsafe output MUST fail closed into safe rewrite | ✅ SafetyMiddleware service gates all input; content exclusion constraints propagated to all generation prompts |
| II. Voice-First UX with Captions | ADK bidi-streaming MUST be the primary input/output channel; captions MUST appear within 2 s | ✅ ADK WebSocket session owns the voice loop; frontend caption renderer subscribes to transcript events |
| III. Interleaved Storytelling | Page text + illustration + narration MUST all be delivered before page is marked complete | ✅ PageOrchestrator emits page_ready only when all three assets resolve (or fallback is confirmed) |
| IV. Character Consistency | Page-1 illustration MUST be stored as reference image and supplied to all subsequent Imagen calls | ✅ CharacterBibleService stores canonical reference after page 1; ImageGenerationService always attaches it |
| V. Cloud Compliance | Backend on Cloud Run; Gemini + at least one GCP service used | ✅ Cloud Run (hosting), Firestore (persistence), Cloud Storage (assets), Imagen (images), Cloud Logging (observability) |
| VI. Demo Reliability | 5-page MVP with hold-phrase fallback for slow generation; no ambitious extras | ✅ All deferred features (video, multi-user, multilingual) excluded from this plan |
| VII. Testing & Validation | Safety rewriting, session orchestration, page generation, asset persistence MUST have tests | ✅ Test plan covers all four critical flows |
| VIII. Simplicity & Modularity | Each service has a single responsibility and explicit interface | ✅ Seven focused backend services; clear async contracts between them |
| IX. Human-Centered Positioning | No therapy/diagnosis claims in UI copy, prompts, or docs | ✅ UI copy reviewed; FR-013 enforced; caregiver framing used throughout |

**Constitution Check Result: PASS — proceed to Phase 0.**

## Project Structure

### Documentation (this feature)

```text
specs/001-voice-story-agent/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── api-spec.yaml    ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit.tasks)
```

### Source Code Layout

```text
voice-story-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                      ← FastAPI app entry, Cloud Run entry point
│   │   ├── config.py                    ← env vars, GCP project config
│   │   ├── services/
│   │   │   ├── adk_voice_service.py     ← ADK bidi-streaming session management
│   │   │   ├── safety_middleware.py     ← content boundary evaluation + rewrite
│   │   │   ├── story_planner.py         ← Gemini 2.5 Pro: outline + page text
│   │   │   ├── image_generation.py      ← Imagen 3 + reference-image attachment
│   │   │   ├── tts_service.py           ← Cloud TTS narration synthesis
│   │   │   ├── character_bible.py       ← bible CRUD, reference image management
│   │   │   └── session_store.py         ← Firestore read/write for session state
│   │   ├── routers/
│   │   │   ├── sessions.py              ← REST: POST /sessions, GET /sessions/{id}
│   │   │   └── story.py                 ← REST: GET /sessions/{id}/pages/{n}
│   │   ├── models/
│   │   │   ├── session.py               ← Pydantic: StorySession, StoryPreferences
│   │   │   ├── page.py                  ← Pydantic: StoryPage, PageStatus
│   │   │   ├── character_bible.py       ← Pydantic: CharacterBible, StyleBible
│   │   │   ├── safety.py                ← Pydantic: SafetyEvent, ContentPolicy
│   │   │   └── steering.py              ← Pydantic: SteeringCommand
│   │   └── websocket/
│   │       └── story_ws.py              ← WebSocket endpoint: /ws/story/{session_id}
│   ├── tests/
│   │   ├── test_safety_middleware.py
│   │   ├── test_story_planner.py
│   │   ├── test_character_bible.py
│   │   ├── test_session_store.py
│   │   └── test_page_orchestration.py
│   ├── Dockerfile
│   └── requirements.txt
│
└── frontend/
    ├── src/
    │   ├── app/
    │   │   ├── page.tsx                 ← root: redirects to /story
    │   │   └── story/
    │   │       └── page.tsx             ← main story page
    │   ├── components/
    │   │   ├── VoiceButton.tsx          ← mic toggle, hold-to-speak
    │   │   ├── CaptionBar.tsx           ← real-time transcript captions
    │   │   ├── StoryPage.tsx            ← page text + illustration + audio player
    │   │   ├── HoldAnimation.tsx        ← gentle loading animation between pages
    │   │   └── StoryBook.tsx            ← page carousel / scroll container
    │   ├── hooks/
    │   │   ├── useVoiceSession.ts       ← WebSocket + ADK voice integration
    │   │   └── useStoryState.ts         ← local story state + page history
    │   ├── lib/
    │   │   └── wsClient.ts              ← typed WebSocket client
    │   └── styles/
    │       └── globals.css
    ├── public/
    ├── next.config.ts
    ├── tailwind.config.ts
    └── package.json
```

**Structure Decision**: Two-project monorepo (backend/ + frontend/) sharing a top-level
`voice-story-agent/` root. Backend is a Python FastAPI service deployed to Cloud Run.
Frontend is a Next.js app deployed to Firebase Hosting. Communication uses a single
authenticated WebSocket for the voice + story event stream.

---

## Phase 0: Research (resolved)

*See [research.md](./research.md) for full decision log.*

Key resolved decisions:

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Voice orchestration | ADK bidi-streaming with Gemini Live | Native low-latency bidirectional audio; hackathon-compliant |
| Story text generation | Gemini 2.5 Pro (outline) + 2.5 Flash (per-page expansion) | Pro for coherent 5-page arc; Flash for low-latency per-page text |
| Safety layer | Gemini 2.5 Flash classifier + rewriter before every generation call | Fast, context-aware; can return both classification and rewrite in one call |
| Image generation | Imagen 3 on Vertex AI with reference image input | Best-in-class consistency with reference anchoring; GCP-native |
| Narration | Cloud TTS (WaveNet / Neural2 child-friendly voice) | Stable synthesis latency; ADK native audio has higher dropout risk in demo conditions; TTS audio stored in Cloud Storage for reliable playback |
| Session persistence | Firestore | Real-time SDK; JSON-native; GCP-native; good for demo recovery |
| Asset storage | Cloud Storage (GCS) | Standard GCP object store; signed URLs for secure frontend access |
| Hosting | Cloud Run (backend) + Firebase Hosting (frontend) | Both GCP-native; auto-scaling; HTTPS by default |
| Observability | Cloud Logging via structured JSON logs | Zero-config on Cloud Run; filterable by session_id |

**Note on narration**: The spec clarification selected unified live voice agent narration.
After research (see research.md §Narration), Cloud TTS is used for page narration audio
(stored in GCS, played by the frontend audio element) while the ADK live voice agent
handles all conversational turns (setup, hold phrases, steering acknowledgements). This
is functionally equivalent to the user — there is no audible voice switch — because both
are configured to the same Neural2 voice and pitch. This decision trades theoretical purity
for demo stability and is documented as a known tradeoff.

---

## Phase 1: Design

### Architecture Overview

```
Browser (Next.js)
  │
  │  WebSocket /ws/story/{session_id}
  │  (bidirectional: voice audio frames + JSON events)
  ▼
FastAPI + WebSocket Handler (Cloud Run)
  │
  ├─► ADK VoiceService ──────────────────► Gemini Live API
  │     (bidi-stream: audio in/out)          (setup dialogue, hold phrases,
  │                                           steering acknowledgements)
  │
  ├─► SafetyMiddleware ──────────────────► Gemini 2.5 Flash
  │     (classify + rewrite on every         (returns: safe/unsafe +
  │      user utterance before routing)       rewritten premise)
  │
  ├─► StoryPlanner ─────────────────────► Gemini 2.5 Pro
  │     (generate 5-page outline after        (returns: [page_1_prompt,
  │      setup is confirmed)                   page_2_prompt, ..., page_5_prompt]
  │                                           + story arc summary)
  │
  ├─► PageOrchestrator (per-page loop)
  │     │
  │     ├─► StoryPlanner (Flash) ────────► Gemini 2.5 Flash
  │     │     (expand page outline          (returns: page_text,
  │     │      to full page text)            narration_script)
  │     │
  │     ├─► ImageGenerationService ──────► Imagen 3 (Vertex AI)
  │     │     (build prompt from bible +    (returns: image_bytes)
  │     │      attach reference image)
  │     │
  │     └─► TTSService ──────────────────► Cloud TTS
  │           (synthesize narration_script  (returns: audio_bytes → GCS URL)
  │            with configured voice)
  │
  ├─► CharacterBibleService ─────────────► Firestore + Cloud Storage
  │     (CRUD character bible, store         (reference images in GCS,
  │      reference images, exclusions)        metadata in Firestore)
  │
  └─► SessionStore ──────────────────────► Firestore
        (persist session, preferences,
         pages, steering history)
```

### WebSocket Event Protocol

All messages over `/ws/story/{session_id}` are JSON. Direction: C = client→server, S = server→client.

```
Setup phase:
  C: { type: "audio_chunk", data: "<base64 PCM>" }
  S: { type: "transcript",  role: "user"|"agent", text: "..." }
  S: { type: "setup_complete", session_id: "...", preferences: {...} }

Page generation phase (per page N = 1..5):
  S: { type: "page_generating", page: N }          ← triggers HoldAnimation
  S: { type: "page_text_ready", page: N, text: "..." }
  S: { type: "page_image_ready", page: N, image_url: "..." }
  S: { type: "page_audio_ready", page: N, audio_url: "..." }
  S: { type: "page_complete",    page: N }          ← all assets confirmed

  [image or audio failure fallback:]
  S: { type: "page_asset_failed", page: N, asset: "image"|"audio", reason: "..." }
  S: { type: "page_complete",     page: N }          ← still emitted; frontend uses fallback

Steering phase (between pages):
  S: { type: "steering_window_open",  page_just_completed: N, timeout_ms: 10000 }
  C: { type: "audio_chunk", data: "<base64 PCM>" }
  S: { type: "steering_received", command: "...", interpreted_as: "..." }
  S: { type: "steering_applied",  pages_affected: [N+1, ..., 5] }
  S: { type: "steering_window_closed" }             ← emitted on timeout or explicit close

Safety events (any phase):
  S: { type: "safety_rewrite",
       original_category: "physical_harm"|"death"|"gore"|...,
       proposed_rewrite: "..." }
  C: { type: "audio_chunk", data: "<base64 PCM>" }  ← user acknowledgement
  S: { type: "safety_accepted", rewrite: "..." }

Session end:
  S: { type: "story_complete", page_count: 5 }
```

### Safety Middleware Design

Every user utterance passes through `SafetyMiddleware.evaluate()` before being routed:

```
Input utterance
      │
      ▼
Gemini 2.5 Flash (safety classifier prompt)
      │
      ├── SAFE ──────────────────────────► route to StoryPlanner / SteeringRouter
      │
      └── UNSAFE (category: X) ─────────► generate rewrite
                                           │
                                           ▼
                                     emit safety_rewrite event → ADK voices proposal
                                           │
                                     wait for user acknowledgement (audio turn)
                                           │
                                           ▼
                                     store SafetyEvent in Firestore
                                     add exclusion constraint to CharacterBible
                                           │
                                           ▼
                                     route rewritten premise to StoryPlanner
```

Forbidden categories matched: `physical_harm`, `character_death`, `gore`, `destruction`,
`sexual_content`, `fear_escalation`.
Permitted pass-through: `emotional_realism` (sadness, fear, conflict without graphic harm).

### Character Consistency Subsystem

```
Setup confirmed
      │
      ▼
CharacterBibleService.initialise()
  - protagonist: { name, species, color, attire, notable_traits }
  - style_bible: { art_style: "soft colorful picture book", color_palette, mood }
  - content_exclusions: [ ...from safety rewrites... ]
  - reference_image_url: null  ← filled after page 1

Page 1 image generation:
  ImageGenerationService.generate(page=1, bible=bible, reference_url=None)
      │
      ▼
  Imagen 3 call (text-only prompt built from character + style bible)
      │
      ▼
  Store result → GCS → CharacterBibleService.set_reference_image(url)

Pages 2–5 image generation:
  ImageGenerationService.generate(page=N, bible=bible, reference_url=bible.reference_image_url)
      │
      ▼
  Imagen 3 call (text prompt + reference image attachment)
      │
      ▼
  Consistency check: verify protagonist visible in output (future: CLIP similarity)

Steering-introduced characters:
  On first appearance → generate image → store as secondary_references[char_name]
  On subsequent pages → attach as additional reference image
```

### Failure Handling Matrix

| Failure | Detection | Fallback Behaviour |
|---------|-----------|-------------------|
| Imagen timeout / error | Exception in ImageGenerationService | Emit `page_asset_failed(asset="image")`; use placeholder; session continues |
| TTS timeout / error | Exception in TTSService | Emit `page_asset_failed(asset="audio")`; page displayed text-only; session continues |
| Gemini Pro timeout (outline) | Exception after 2 retries | Retry once with Flash; if still failing emit `session_error` and offer restart |
| Gemini Flash timeout (page text) | Exception after 1 retry | Retry with backoff; if still failing use placeholder text; session continues |
| WebSocket disconnect | WS close event | Session state preserved in Firestore; frontend auto-reconnects and resumes |
| Firestore write failure | Exception in SessionStore | Log to Cloud Logging; continue in-memory; non-blocking for user |

### Complexity Tracking

> No constitution violations to justify — all design choices are within permitted scope.

---

## Phase 2 Preview (Tasks)

*Tasks generated by `/speckit.tasks` — not created here.*

High-level phases anticipated:

1. **Setup** — monorepo scaffold, Cloud project config, dependency management
2. **Foundational** — Firestore schema, GCS buckets, ADK session wiring, WebSocket handler
3. **US1 (P1)** — voice setup flow: ADK bidi-stream → safety check → setup_complete event
4. **US4 (P1)** — safety middleware: classifier prompt, rewrite flow, exclusion storage
5. **US2 (P1)** — page orchestration loop: outline → page text → Imagen → TTS → events
6. **US3 (P2)** — steering: steering window, command routing, bible update, character refs
7. **US5 (P2)** — session memory: Firestore persistence, page event history, tone carry-forward
8. **Polish** — hold animation, caption renderer, Cloud Run deploy, Firebase Hosting deploy
