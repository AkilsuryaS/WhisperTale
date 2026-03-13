# Implementation Plan: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/001-voice-story-agent/spec.md`

## Summary

A real-time voice storytelling agent for children under 12, submitted under the **Creative
Storyteller** hackathon category. A caregiver or child speaks to initiate and steer a
personalized 5-page illustrated storybook. The system detects and rewrites any unsafe content
conversationally, then generates each page as a coherent triple of story text + Imagen
illustration + narration audio — streamed page-by-page so mid-story steering immediately
reshapes future pages. Conversational turns are spoken by ADK Gemini Live; per-page story
narration is synthesised by Cloud TTS for demo reliability. Character visual consistency is
enforced by feeding the page-1 illustration as a reference image into every subsequent Imagen call.

## Technical Context

**Language/Version**: Python 3.11 (backend), TypeScript / Next.js 14 (frontend)
**Primary Dependencies**: FastAPI, Google ADK (bidi-streaming), Gemini Live API,
  Gemini 2.5 Pro, Gemini 2.5 Flash, Imagen 3 on Vertex AI, Cloud Text-to-Speech,
  Firestore, Cloud Storage, Cloud Run, Firebase App Hosting, Cloud Logging,
  Next.js + React + Tailwind CSS
**Storage**: Firestore (session + story state), Cloud Storage (images + audio)
**Testing**: pytest (backend), Jest + React Testing Library (frontend)
**Target Platform**: Google Cloud (Cloud Run backend + Firebase App Hosting frontend)
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
Frontend is a Next.js app deployed to Firebase App Hosting. Communication uses a single
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
| Hosting | Cloud Run (backend) + Firebase App Hosting (frontend) | Both GCP-native; auto-scaling; HTTPS by default; App Hosting handles Next.js SSR natively |
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

### Layered Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser (Next.js + React)                                          │
│  ┌──────────────┐  ┌─────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ VoiceButton  │  │ CaptionBar  │  │ StoryBook  │  │HoldAnim.  │  │
│  └──────┬───────┘  └──────┬──────┘  └─────┬──────┘  └─────┬─────┘  │
│         └─────────────────┴──────────────┴────────────────┘        │
│                           │                                         │
│               useVoiceSession (hook)  useStoryState (hook)          │
│                           │  wsClient.ts                            │
└───────────────────────────┼─────────────────────────────────────────┘
                            │  WS /ws/story/{session_id}
                            │  binary: PCM audio  /  text: JSON events
┌───────────────────────────┼─────────────────────────────────────────┐
│  WebSocketHandler  (story_ws.py)  ← ONLY component that touches WS  │
│                           │                                         │
│  dispatches inbound audio + events; emits all outbound events       │
└──────┬─────────┬──────────┴──────────┬────────────────┬────────────┘
       │         │                     │                │
       ▼         ▼                     ▼                ▼
 ┌─────────┐ ┌──────────┐      ┌──────────────┐ ┌────────────┐
 │  Voice  │ │  Safety  │      │  Steering    │ │  Session   │
 │ Session │ │ Service  │      │  Router      │ │  Store     │
 │ Service │ │          │      │              │ │            │
 └────┬────┘ └────┬─────┘      └──────┬───────┘ └─────┬──────┘
      │           │                   │               │
      ▼           ▼                   ▼               ▼
 Gemini    Gemini Flash        StoryPlanner     Firestore
 Live API  (classify+rewrite)  Service
                                    │
                         ┌──────────┴──────────┐
                         ▼                     ▼
                   Gemini Pro            Gemini Flash
                   (arc outline)         (page text)
                         │
                         ▼
              ┌──────────────────────────┐
              │    PageOrchestrator      │   ← coordinates per-page generation
              │  (not a service; lives   │
              │   inside WebSocketHandler│
              │   per session loop)      │
              └──┬──────────┬────────────┘
                 │          │
          ┌──────▼──┐  ┌────▼────────────────────────┐
          │Character│  │         (parallel)           │
          │ Bible   │  │  ┌──────────────┐            │
          │ Service │  │  │  Image       │            │
          └──┬──────┘  │  │  Generation  │→Imagen 3  │
             │         │  │  Service     │            │
             │         │  └──────────────┘            │
             │         │  ┌──────────────┐            │
             │         │  │  TTS         │            │
             │         │  │  Service     │→Cloud TTS  │
             │         │  └──────────────┘            │
             │         └──────────────────────────────┘
             │                   │
             ▼                   ▼
          Firestore    ┌─────────────────────┐
          + GCS        │  Media Persistence  │ → Cloud Storage (GCS)
                       │  Service            │
                       └─────────────────────┘
```

**Ownership rules**:
- `WebSocketHandler` is the ONLY component that emits WebSocket events or receives audio frames.
- `SafetyService` is called by `WebSocketHandler` for every user utterance; it is NEVER called by any other service.
- `CharacterBibleService` is the ONLY component that builds image prompts and manages reference images.
- `MediaPersistenceService` is the ONLY component that reads or writes Cloud Storage.
- `SessionStore` is the ONLY component that reads or writes Firestore.

---

### Service Catalogue

Seven services, each with a single responsibility, explicit public API, and named dependencies.

---

#### 1. VoiceSessionService

**Responsibility**: Own the ADK bidi-streaming lifecycle with Gemini Live. Receive PCM audio
from the WebSocket handler and yield transcribed turns and agent audio back. Does not know
anything about story content, safety, or pages.

**External dependency**: Gemini Live API via Google ADK SDK

**Public API**:
```python
class VoiceSessionService:
    async def start(session_id: str, system_prompt: str) -> None
    # Opens an ADK bidi-stream. system_prompt sets voice, persona, and language.

    async def send_audio(session_id: str, pcm_bytes: bytes) -> None
    # Forwards one PCM audio chunk into the open ADK stream.

    async def stream_turns(session_id: str) -> AsyncIterator[VoiceTurn]
    # Yields completed turns: { role: "user"|"agent", transcript: str, audio_bytes: bytes|None }

    async def speak(session_id: str, text: str) -> None
    # Injects text into the ADK stream so the agent speaks it (hold phrases, acknowledgements).
    # Waits until audio is fully spoken before returning.

    async def end(session_id: str) -> None
    # Closes the ADK stream and releases resources.
```

**Called by**: `WebSocketHandler` only.
**Calls**: Gemini Live API.

---

#### 2. SafetyService

**Responsibility**: Evaluate a single utterance against the content boundary (FR-004) and
return a structured result. Produces a rewrite when unsafe content is detected. Does not
call any other service, emit any events, or store anything.

**External dependency**: Gemini 2.5 Flash (structured output, single call)

**Public API**:
```python
class SafetyService:
    async def evaluate(utterance: str) -> SafetyResult
    # SafetyResult:
    #   safe: bool
    #   category: Literal["physical_harm","character_death","gore","destruction",
    #                      "sexual_content","fear_escalation"] | None
    #   rewrite: str | None   # child-safe alternative; populated only when safe=False
    #
    # Permitted pass-through (safe=True, category=None):
    #   emotional_realism — sadness, loneliness, fear, conflict without graphic harm
    #
    # NEVER raises; on any Gemini API exception or malformed response returns
    # SafetyResult(safe=False, category=None, rewrite=SAFE_FALLBACK_REWRITE).
    # Child safety is non-negotiable: unclassified input MUST NOT reach generation.
```

**Called by**: `WebSocketHandler` on every user utterance, before any routing.
**Calls**: Gemini 2.5 Flash.

---

#### 3. StoryPlannerService

**Responsibility**: Generate and update story content using Gemini. Knows nothing about
voice, images, or storage. Three distinct operations: create an arc outline (called once
at setup), expand a beat into page text (called once per page), apply steering to remaining
arc beats (called when a steering command is accepted).

**External dependencies**: Gemini 2.5 Pro (outline), Gemini 2.5 Flash (page text, steering)

**Public API**:
```python
class StoryPlannerService:
    async def create_arc(
        preferences: StoryPreferences,
        bible: CharacterBible
    ) -> StoryArc
    # Calls Gemini Pro once. Returns list of 5 PageBeat (page number + beat description).
    # CharacterBible.content_exclusions are injected into the system prompt.

    async def expand_page(
        beat: PageBeat,
        page_history: list[PageSummary],
        bible: CharacterBible
    ) -> PageContent
    # Calls Gemini Flash once. Returns { text: str (60–120 words), narration_script: str }.
    # page_history contains one-sentence summaries of prior pages for coherence.
    # bible.content_exclusions injected to prevent forbidden content in output.

    async def apply_steering(
        arc: StoryArc,
        command: SteeringCommand,
        from_page: int
    ) -> StoryArc
    # Calls Gemini Flash once. Updates beats for pages from_page..5 to reflect the
    # steering command. Returns a new StoryArc; the original is not mutated.
```

**Called by**: `WebSocketHandler` (for `create_arc` and `apply_steering`),
`PageOrchestrator` (for `expand_page`).
**Calls**: Gemini 2.5 Pro, Gemini 2.5 Flash.

---

#### 4. CharacterBibleService

**Responsibility**: Own the entire lifecycle of the character bible — initialisation,
reference image management, content exclusion updates, secondary character registration,
and building image prompts. Is the single source of truth for what Imagen should draw.
Does not generate images.

**External dependencies**: `SessionStore` (read/write), `MediaPersistenceService`
(reference image URL storage)

**Public API**:
```python
class CharacterBibleService:
    async def initialise(
        session_id: str,
        preferences: StoryPreferences
    ) -> CharacterBible
    # Builds the initial CharacterBible from StoryPreferences. Persists via SessionStore.

    async def set_reference_image(session_id: str, gcs_url: str) -> None
    # Stores the page-1 illustration URL as the canonical protagonist reference.
    # Must be called immediately after page 1 illustration is stored.

    async def add_content_exclusion(session_id: str, exclusion: str) -> None
    # Appends one exclusion constraint (e.g., "no character harm") derived from a
    # SafetyEvent. Persists via SessionStore.

    async def add_secondary_character(
        session_id: str,
        char: CharacterRef
    ) -> None
    # Registers a new character introduced via steering. char.reference_image_url
    # is null until set_secondary_reference is called after first-page image generation.

    async def set_secondary_reference(
        session_id: str, char_id: str, gcs_url: str
    ) -> None
    # Stores the first-appearance illustration URL for a secondary character.

    async def get(session_id: str) -> CharacterBible
    # Returns the current state of the bible. Used before every image generation call.

    def build_image_prompt(
        bible: CharacterBible,
        page_scene: str,
        page_number: int
    ) -> ImagePrompt
    # Pure function (no I/O). Constructs the Imagen prompt text and collects reference
    # image URLs. For page 1: reference_urls=[]. For pages 2–5: reference_urls includes
    # protagonist reference and any secondary character references appearing in page_scene.
    # ImagePrompt: { text_prompt: str, reference_urls: list[str] }
```

**Called by**: `WebSocketHandler` (initialise, add_content_exclusion, add_secondary_character),
`PageOrchestrator` (get, build_image_prompt, set_reference_image, set_secondary_reference).
**Calls**: `SessionStore`, `MediaPersistenceService` (indirectly via URL storage).

---

#### 5. ImageGenerationService

**Responsibility**: Accept a pre-built `ImagePrompt` and return raw image bytes. Does not
build prompts, know about story content, or persist anything.

**External dependency**: Imagen 3 on Vertex AI

**Public API**:
```python
class ImageGenerationService:
    async def generate(prompt: ImagePrompt) -> bytes
    # Calls Imagen 3 with prompt.text_prompt and prompt.reference_urls.
    # Raises ImageGenerationError on failure (timeout, content policy rejection, quota).
    # Caller is responsible for catching and triggering asset-failure fallback.
```

**Called by**: `PageOrchestrator` only.
**Calls**: Vertex AI Imagen 3.

---

#### 6. TTSService

**Responsibility**: Accept a narration script string and return synthesised audio bytes.
Does not know about story content, pages, or persistence.

**External dependency**: Cloud Text-to-Speech

**Public API**:
```python
class TTSService:
    async def synthesize(
        script: str,
        voice_config: VoiceConfig
    ) -> bytes
    # Calls Cloud TTS. Returns MP3 bytes.
    # VoiceConfig: { voice_name: str, language_code: str, speaking_rate: float }
    # Raises TTSError on failure. Caller handles fallback.
```

**Called by**: `PageOrchestrator` only.
**Calls**: Cloud Text-to-Speech.

---

#### 7. MediaPersistenceService

**Responsibility**: Own all Cloud Storage writes and reads for binary assets. Generate
signed URLs for frontend access. Does not know what the files contain.

**External dependency**: Cloud Storage (GCS)

**Public API**:
```python
class MediaPersistenceService:
    async def store_illustration(
        session_id: str, page: int, image_bytes: bytes
    ) -> str  # returns gs:// URL

    async def store_narration(
        session_id: str, page: int, audio_bytes: bytes
    ) -> str  # returns gs:// URL

    async def store_character_ref(
        session_id: str, char_id: str, image_bytes: bytes
    ) -> str  # returns gs:// URL

    async def get_signed_url(
        gcs_url: str, expiry_seconds: int = 3600
    ) -> str  # returns https:// signed URL for frontend
```

**Called by**: `PageOrchestrator` (store_illustration, store_narration, store_character_ref,
get_signed_url).
**Calls**: Cloud Storage.

---

#### 8. SessionStore

**Responsibility**: Own all Firestore reads and writes. No business logic; pure data access.

**External dependency**: Firestore

**Public API**:
```python
class SessionStore:
    async def create(session: StorySession) -> None
    async def get(session_id: str) -> StorySession
    async def update_status(session_id: str, status: SessionStatus) -> None
    async def update_arc(session_id: str, arc: StoryArc) -> None
    async def save_preferences(session_id: str, prefs: StoryPreferences) -> None
    async def save_character_bible(session_id: str, bible: CharacterBible) -> None
    async def save_page(session_id: str, page: StoryPage) -> None
    async def save_steering_command(session_id: str, cmd: SteeringCommand) -> None
    async def save_safety_event(session_id: str, event: SafetyEvent) -> None
```

**Called by**: `CharacterBibleService`, `WebSocketHandler` (for session lifecycle),
`PageOrchestrator` (save_page).
**Calls**: Firestore.

---

#### Supporting Coordinator: PageOrchestrator

Not a service (has no external callers) — a per-page async coroutine called by
`WebSocketHandler` inside the story generation loop.

**Responsibility**: Coordinate the generation of one page. Emit asset-ready events to
`WebSocketHandler` as each asset resolves. Handle per-asset failures without terminating
the session.

**Calls**: `StoryPlannerService.expand_page`, `CharacterBibleService.get`,
`CharacterBibleService.build_image_prompt`, `CharacterBibleService.set_reference_image`,
`CharacterBibleService.set_secondary_reference`, `ImageGenerationService.generate`,
`TTSService.synthesize`, `MediaPersistenceService.*`, `SessionStore.save_page`.

**Emits events** (via callback to `WebSocketHandler`):
`page_generating`, `page_text_ready`, `page_image_ready`, `page_audio_ready`,
`page_asset_failed`, `page_complete`.

#### Supporting Classifier: SteeringRouter

Not a service — a synchronous pure function called inside `WebSocketHandler` during the
steering window.

```python
def classify_steering(
    utterance: str,
    safety_result: SafetyResult
) -> SteeringClassification
# Returns one of:
#   type: "tone_change" | "pacing_change" | "element_reintroduction" |
#         "character_introduction" | "ambiguous" | "unsafe"
# Called only after SafetyService.evaluate has already run.
# No external calls; purely pattern-matching + heuristic classification.
```

---

### Event Flow Diagrams

#### Flow 1 — Voice Setup

```
Browser          WebSocketHandler     VoiceSessionService   SafetyService   StoryPlannerService   CharacterBibleService   SessionStore
   │                    │                     │                   │                  │                       │                   │
   │──audio_chunk──────►│                     │                   │                  │                       │                   │
   │                    │──send_audio─────────►                   │                  │                       │                   │
   │                    │◄──stream_turns───────────────────────────                  │                       │                   │
   │◄──transcript(user)─│                     │                   │                  │                       │                   │
   │                    │──evaluate(utterance)──────────────────►│                  │                       │                   │
   │                    │◄──SafetyResult(safe=True)──────────────│                  │                       │                   │
   │                    │  [ADK agent asks follow-up questions]   │                  │                       │                   │
   │◄──transcript(agent)│                     │                   │                  │                       │                   │
   │    [repeats for each setup turn]         │                   │                  │                       │                   │
   │                    │                     │                   │                  │                       │                   │
   │  [final answer received]                 │                   │                  │                       │                   │
   │                    │──evaluate────────────────────────────►│                  │                       │                   │
   │                    │◄──SafetyResult(safe=True)──────────────│                  │                       │                   │
   │                    │──create_arc(prefs, bible)───────────────────────────────►│                       │                   │
   │                    │◄──StoryArc([beat_1..beat_5])────────────────────────────│                       │                   │
   │                    │──initialise(session_id, prefs)──────────────────────────────────────────────────►│                   │
   │                    │◄──CharacterBible────────────────────────────────────────────────────────────────│                   │
   │                    │──save_preferences──────────────────────────────────────────────────────────────────────────────────►│
   │                    │──update_arc────────────────────────────────────────────────────────────────────────────────────────►│
   │◄──setup_complete───│                     │                   │                  │                       │                   │
```

---

#### Flow 2 — Safety Rewrite (unsafe setup utterance)

```
Browser          WebSocketHandler     VoiceSessionService   SafetyService   StoryPlannerService   SessionStore
   │                    │                     │                   │                  │                   │
   │──audio_chunk──────►│                     │                   │                  │                   │
   │                    │──send_audio─────────►                   │                  │                   │
   │                    │◄──stream_turns(transcript)──────────────│                  │                   │
   │◄──transcript(user)─│                     │                   │                  │                   │
   │                    │──evaluate("...kills everyone...")───────►│                  │                   │
   │                    │◄──SafetyResult(safe=False,              │                  │                   │
   │                    │   category="character_death",           │                  │                   │
   │                    │   rewrite="...dragon knocks things...")──│                  │                   │
   │◄──safety_rewrite───│                     │                   │                  │                   │
   │  (proposed_rewrite shown in caption)     │                   │                  │                   │
   │                    │──speak(proposed_rewrite)────────────────►                  │                   │
   │  [user hears agent voice the rewrite]    │                   │                  │                   │
   │                    │◄──stream_turns(user ack: "yes, purple!")│                  │                   │
   │◄──transcript(user)─│                     │                   │                  │                   │
   │                    │──evaluate("yes, purple!")───────────────►│                  │                   │
   │                    │◄──SafetyResult(safe=True)───────────────│                  │                   │
   │                    │──save_safety_event─────────────────────────────────────────────────────────────►│
   │                    │──add_content_exclusion(exclusion)        │                  │  [to CharBible]   │
   │◄──safety_accepted──│                     │                   │                  │                   │
   │  [generation proceeds with rewritten premise]                │                  │                   │
```

---

#### Flow 3 — Page Generation (per page N)

```
WebSocketHandler       PageOrchestrator    StoryPlannerService  CharacterBibleService  ImageGenService  TTSService  MediaPersistenceService  SessionStore
       │                      │                    │                     │                    │              │                 │                   │
       │──run_page(N,beat,bible,history)──────────►│                    │                    │              │                 │                   │
       │◄──emit: page_generating(N)────────────────│                    │                    │              │                 │                   │
(→Browser)                    │──expand_page(beat,history,bible)───────►│                    │              │                 │                   │
                              │◄──PageContent(text,narration_script)────│                    │              │                 │                   │
       │◄──emit: page_text_ready(N,text)───────────│                    │                    │              │                 │                   │
(→Browser)                    │──get(session_id)────────────────────────────────────────────►│              │                 │                   │
                              │──build_image_prompt(bible,scene,N)──────────────────────────►│              │                 │                   │
                              │◄──ImagePrompt(text_prompt,ref_urls)──────────────────────────│              │                 │                   │
                              │                    │                     │                    │              │                 │                   │
                              │         [parallel: image + audio]        │                    │              │                 │                   │
                              │──generate(ImagePrompt)─────────────────────────────────────────────────────►│                 │                   │
                              │──synthesize(narration_script)───────────────────────────────────────────────────────────────►│                   │
                              │                    │                     │                    │              │                 │                   │
                              │◄──image_bytes────────────────────────────────────────────────────────────── │                 │                   │
                              │──store_illustration(session_id,N,bytes)──────────────────────────────────────────────────────►│                   │
                              │◄──gcs_url────────────────────────────────────────────────────────────────────────────────────│                   │
                              │──get_signed_url(gcs_url)────────────────────────────────────────────────────────────────────►│                   │
                              │◄──signed_url─────────────────────────────────────────────────────────────────────────────────│                   │
       │◄──emit: page_image_ready(N, signed_url)───│                    │                    │              │                 │                   │
(→Browser)                    │                    │                     │                    │              │                 │                   │
                              │  [if N==1: set protagonist reference]    │                    │              │                 │                   │
                              │──set_reference_image(session_id,gcs_url)─────────────────────►│              │                 │                   │
                              │                    │                     │                    │              │                 │                   │
                              │◄──audio_bytes───────────────────────────────────────────────────────────────────────────────►│                   │
                              │──store_narration(session_id,N,bytes)─────────────────────────────────────────────────────────►│                   │
                              │◄──gcs_url────────────────────────────────────────────────────────────────────────────────────│                   │
                              │──get_signed_url(gcs_url)────────────────────────────────────────────────────────────────────►│                   │
       │◄──emit: page_audio_ready(N, signed_url)───│                    │                    │              │                 │                   │
(→Browser)                    │──save_page(session_id, StoryPage)────────────────────────────────────────────────────────────────────────────────►│
       │◄──emit: page_complete(N)──────────────────│                    │                    │              │                 │                   │
(→Browser)                    │                    │                     │                    │              │                 │                   │
```

**Asset failure path** (image or audio raises an exception):
```
                              │──generate(ImagePrompt)──────────────────────────────────────►│
                              │◄──ImageGenerationError───────────────────────────────────────│
       │◄──emit: page_asset_failed(N, "image")────│
(→Browser)                    │
                              │  [image_bytes = None; illustration_failed = True]
                              │  [continue to audio synthesis; then:]
       │◄──emit: page_complete(N, illustration_failed=True)
(→Browser, shows placeholder)
```

---

#### Flow 4 — Steering (tone change between pages)

```
Browser          WebSocketHandler     VoiceSessionService   SafetyService   SteeringRouter  StoryPlannerService  CharacterBibleService  SessionStore
   │                    │                     │                   │                 │                │                    │                  │
   │  [page N complete] │                     │                   │                 │                │                    │                  │
   │◄──steering_window_open(N, 10000ms)────────│                  │                 │                │                    │                  │
   │──audio_chunk──────►│                     │                   │                 │                │                    │                  │
   │                    │──send_audio─────────►                   │                 │                │                    │                  │
   │                    │◄──stream_turns(transcript)──────────────│                 │                │                    │                  │
   │◄──transcript(user)─│                     │                   │                 │                │                    │                  │
   │                    │──evaluate("make it funnier")────────────►│                │                │                    │                  │
   │                    │◄──SafetyResult(safe=True)───────────────│                 │                │                    │                  │
   │                    │──classify_steering("make it funnier", safe_result)───────►│                │                    │                  │
   │                    │◄──SteeringClassification(type="tone_change")──────────────│                │                    │                  │
   │◄──steering_received(interpreted_as="Increase humour from page N+1")───────────│                │                    │                  │
   │                    │──speak("Sure! Let's turn up the fun from here!")──────────►               │                    │                  │
   │                    │──apply_steering(arc, command, from_page=N+1)──────────────────────────────►│                    │                  │
   │                    │◄──updated_StoryArc──────────────────────────────────────────────────────── │                    │                  │
   │                    │──save_steering_command(session_id, command)──────────────────────────────────────────────────────────────────────►│
   │                    │──update_arc(session_id, updated_arc)─────────────────────────────────────────────────────────────────────────────►│
   │◄──steering_applied(pages_affected=[N+1..5])                  │                 │                │                    │                  │
   │◄──steering_window_closed──────────────────│                  │                 │                │                    │                  │
   │  [PageOrchestrator begins page N+1 with updated arc and current bible]         │                │                    │                  │
```

**Character introduction steering** adds one step after `apply_steering`:
```
   │                    │──add_secondary_character(session_id, CharacterRef(char_id, name, desc))──────────────────────────────────────────►│
   │  [PageOrchestrator: on first page featuring new char → after generate() → set_secondary_reference(session_id, char_id, gcs_url)]
```

---

#### Flow 5 — Frontend Streaming (event handling in browser)

```
wsClient.ts → useVoiceSession hook → component layer

Event received          Handler                         Component update
─────────────────────────────────────────────────────────────────────────
connected               init session state              status: "ready"
transcript(user)        append to captions              CaptionBar: user bubble
transcript(agent)       append to captions              CaptionBar: agent bubble
safety_rewrite          show rewrite in caption         CaptionBar: "Agent suggests..."
safety_accepted         clear rewrite overlay           CaptionBar: proceed normally
setup_complete          store preferences               StoryBook: show page 1 shell
page_generating         start HoldAnimation             HoldAnimation: visible, speaking
page_text_ready(N)      set page[N].text                StoryPage: text fades in
page_image_ready(N)     set page[N].imageUrl            StoryPage: illustration fades in
page_audio_ready(N)     set page[N].audioUrl; play()   StoryPage: narration auto-plays
page_asset_failed(N)    set page[N].{field}=null        StoryPage: show placeholder
page_complete(N)        stop HoldAnimation              HoldAnimation: hidden
steering_window_open    show steering indicator         VoiceButton: pulse ring
steering_received       show interpreted intent         CaptionBar: agent acknowledgement
steering_applied        update future page state        StoryBook: mark pages as pending
steering_window_closed  hide steering indicator         VoiceButton: normal state
story_complete          show end screen                 StoryBook: closing message
session_error           show restart prompt             full-screen error overlay
```

---

### WebSocket Event Protocol

All messages over `/ws/story/{session_id}`. Direction: C = client→server, S = server→client.
`WebSocketHandler` is the sole emitter of all S events.

```
─── Setup phase ─────────────────────────────────────────────────────────
C: { type: "audio_chunk", data: "<base64 PCM 16-bit 16kHz mono>" }
S: { type: "transcript", role: "user"|"agent", text: "...", is_final: bool }
S: { type: "setup_complete", session_id: "uuid", preferences: { ... } }

─── Safety (any phase) ──────────────────────────────────────────────────
S: { type: "safety_rewrite",
     safety_event_id: "uuid",
     detected_category: "physical_harm"|"character_death"|"gore"|
                        "destruction"|"sexual_content"|"fear_escalation",
     proposed_rewrite: "..." }            ← never contains original unsafe text
S: { type: "safety_accepted",
     safety_event_id: "uuid",
     final_premise: "..." }

─── Page generation (per page N = 1..5) ─────────────────────────────────
S: { type: "page_generating",   page: N }
S: { type: "page_text_ready",   page: N, text: "..." }
S: { type: "page_image_ready",  page: N, image_url: "<signed GCS URL>" }
S: { type: "page_audio_ready",  page: N, audio_url: "<signed GCS URL>" }
S: { type: "page_asset_failed", page: N, asset: "image"|"audio", reason: "..." }
S: { type: "page_complete",     page: N,
     illustration_failed: bool, audio_failed: bool }

─── Steering (after each page) ──────────────────────────────────────────
S: { type: "steering_window_open",  page_just_completed: N, timeout_ms: 10000 }
C: { type: "audio_chunk", data: "..." }
S: { type: "steering_received",
     command_id: "uuid",
     raw_transcript: "...",
     interpreted_as: "..." }
S: { type: "steering_applied",
     command_id: "uuid",
     steering_type: "tone_change"|"pacing_change"|
                    "element_reintroduction"|"character_introduction",
     pages_affected: [N+1, ..., 5] }
S: { type: "steering_window_closed",
     reason: "timeout"|"steering_applied"|"user_silent" }

─── Session end ─────────────────────────────────────────────────────────
S: { type: "story_complete", session_id: "uuid", page_count: 5 }
S: { type: "session_error",  code: "str",        message: "str" }

─── Keepalive ───────────────────────────────────────────────────────────
C: { type: "ping" }
S: { type: "pong" }
```

---

### Failure Handling Matrix

| Failure | Detected by | Retry policy | Fallback emitted |
|---------|-------------|-------------|------------------|
| Imagen error / timeout | `ImageGenerationService.generate` raises | 1 retry with 2 s backoff; then fail | `page_asset_failed(asset="image")` → `page_complete(illustration_failed=True)` |
| TTS error / timeout | `TTSService.synthesize` raises | 1 retry with 1 s backoff; then fail | `page_asset_failed(asset="audio")` → `page_complete(audio_failed=True)` |
| Gemini Pro timeout (arc) | `StoryPlannerService.create_arc` raises | 2 retries; fallback to Flash on 3rd | `session_error` if all retries exhausted |
| Gemini Flash timeout (page) | `StoryPlannerService.expand_page` raises | 1 retry; fallback text on 2nd | `page_text_ready` with placeholder text; session continues |
| Gemini Flash timeout (safety) | `SafetyService.evaluate` raises | No retry; return `safe=False, rewrite=SAFE_FALLBACK_REWRITE` (fail-safe — unclassified input MUST NOT reach generation) | `safety_rewrite` event with child-safe fallback premise; user must acknowledge before generation proceeds |
| WebSocket disconnect | `WebSocketHandler` close event | Frontend auto-reconnects; `GET /sessions/{id}` restores state | none — state in Firestore |
| Firestore write failure | `SessionStore` method raises | Log to Cloud Logging; continue in-memory | none — non-blocking for user |

---

### Complexity Tracking

> No constitution violations to justify — all design choices are within permitted scope.

---

## Phase 2 Preview (Tasks)

*Tasks generated by `/speckit.tasks` — not created here.*

High-level phases anticipated:

1. **Setup** — monorepo scaffold, Cloud project config, dependency management
2. **Foundational** — Firestore schema, GCS buckets, WebSocket handler skeleton,
   VoiceSessionService wiring, SessionStore, MediaPersistenceService
3. **US1+US2 (P1)** — child/parent voice setup: VoiceSessionService → SafetyService →
   StoryPlannerService.create_arc → CharacterBibleService.initialise → setup_complete event
4. **US3 (P1)** — safety rewrite: SafetyService classifier prompt, rewrite flow,
   add_content_exclusion, safety_rewrite/safety_accepted event pair
5. **US6 (P1)** — full 5-page delivery: PageOrchestrator loop (expand_page → generate →
   synthesize → persist → signed URLs → page events), HoldAnimation, graceful asset failure
6. **US5 (P1)** — protagonist visual consistency: CharacterBibleService.build_image_prompt,
   set_reference_image after page 1, reference image attachment for pages 2–5
7. **US4 (P2)** — tone-change steering: SteeringRouter, steering window, apply_steering,
   steering event sequence, secondary character introduction + set_secondary_reference
8. **US7 (P2)** — session memory: page_history accumulation, tone carry-forward in
   expand_page calls, clean-state on new session
9. **Polish** — caption renderer, CaptionBar, Cloud Run deploy config, Firebase App Hosting
   deploy, Cloud Logging structured events, reconnect recovery via GET /sessions/{id}
