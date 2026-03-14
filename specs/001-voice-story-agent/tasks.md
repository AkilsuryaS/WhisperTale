# Tasks: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12 | **Plan**: [plan.md](./plan.md)

## Reading guide

Each task is scoped to one focused coding session (≤ 4 hours). Dependencies are listed so
tasks can be ordered. Priority follows spec: P1 (launch blocker) > P2 (demo differentiator).
Test tasks carry a `TEST-` prefix and are colocated with the feature tasks they cover.

**Columns**:
- **ID**: stable reference used in dependency lists
- **Phase**: logical grouping (scaffold, data, ws, safety, setup, pages, consistency, steering, memory, frontend, deploy)
- **Priority**: P1 | P2 | P3
- **Status**: `✅ Done` | `🔄 In Progress` | `⬜ Not Started`
- **Files**: primary files created or significantly modified
- **Depends**: IDs that must be complete first

---

## MVP Cut Line

The minimum shippable hackathon submission covers:

**Core MVP** (must be complete before demo):
- T-001 through T-027 — all scaffold, data, WebSocket, safety, setup, page generation, and character consistency tasks
- T-034 through T-043 — all frontend components and both deploy tasks

**Stretch goals** (defer if time is short; product remains demonstrable without them):
- T-028 through T-033 — mid-story steering, voice commands, session memory, tone carry-forward
- T-044 through T-045 — Cloud Logging structured events and WebSocket reconnect recovery

The demo story experience — voice setup, 5-page generation, character consistency, safety rewriting — is fully deliverable with the core MVP set alone. Steering enriches the live demo but is not required to show the primary value proposition.

---

## Phase 0 — Project Scaffold

### T-001 · Monorepo root + backend scaffold

**Priority**: P1
**Status**: ✅ Done — PR #2 merged. `backend/` scaffold, `config.py`, `main.py`, `Dockerfile`, `.env.example` all verified. `GET /health` returns `{"status":"ok"}` with zero credentials.
**Files**:
- `voice-story-agent/backend/requirements.txt`
- `voice-story-agent/backend/app/main.py`
- `voice-story-agent/backend/app/config.py`
- `voice-story-agent/backend/Dockerfile`
- `voice-story-agent/backend/.env.example`

**Description**:
Create the monorepo root directory. Scaffold the FastAPI backend:
- `requirements.txt` with pinned versions: `fastapi`, `uvicorn[standard]`, `google-cloud-aiplatform`, `google-generativeai`, `google-cloud-firestore`, `google-cloud-storage`, `google-cloud-texttospeech`, `google-adk`, `pydantic>=2`, `python-dotenv`, `pytest`, `pytest-asyncio`, `httpx`
- `config.py` reads all env vars from `.env` via `pydantic-settings`: `GCP_PROJECT_ID`, `GCP_REGION`, `GCS_BUCKET_NAME`, `FIRESTORE_DATABASE`, `GEMINI_PRO_MODEL`, `GEMINI_FLASH_MODEL`, `IMAGEN_MODEL`, `TTS_VOICE_NAME`
- `main.py` creates the FastAPI app, mounts routers (stub), and defines a `GET /health` handler
- Multi-stage `Dockerfile`: builder stage installs deps, runtime stage copies app, exposes port 8080

**Done when**:
- `docker build` succeeds on the backend directory
- `uvicorn app.main:app --reload` starts without import errors
- `GET /health` returns `{"status": "ok"}`

**Depends**: —

---

### T-002 · Frontend scaffold (Next.js 14 + Tailwind)

**Priority**: P1
**Status**: ✅ Done — PR #2 merged. `frontend/` scaffold with Next.js 14, Tailwind, `/story` stub. `npm run build` passes cleanly. `.env.local.example` created.
**Files**:
- `voice-story-agent/frontend/package.json`
- `voice-story-agent/frontend/next.config.ts`
- `voice-story-agent/frontend/tailwind.config.ts`
- `voice-story-agent/frontend/src/app/page.tsx`
- `voice-story-agent/frontend/src/app/story/page.tsx` (stub)
- `voice-story-agent/frontend/src/styles/globals.css`

**Description**:
Bootstrap Next.js 14 app with TypeScript and Tailwind CSS. Root page redirects to `/story`.
`/story` renders a centred placeholder `<h1>Voice Story Agent</h1>`. Configure
`NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_WS_BASE_URL` in `.env.local.example`.

**Done when**:
- `npm run dev` starts without errors
- `/story` renders the placeholder heading
- `npm run build` produces a clean build

**Depends**: —

---

### T-003 · Google Cloud project config + IAM

**Priority**: P1
**Status**: ✅ Done — PR #3 open. `infra/setup.sh` verified against `whispertale-dev`: APIs enabled, Firestore created, GCS bucket created, service account + IAM roles provisioned, SA key saved to `.credentials/sa-key.json`.
**Files**:
- `voice-story-agent/infra/setup.sh`
- `voice-story-agent/infra/README.md`

**Description**:
Write a `setup.sh` that provisions required GCP resources. The script should use `|| true`
guards or `--quiet` flags so that it does not fail when resources already exist — re-running
on a partially configured project should be safe, though it may print warnings for existing
resources. It is **not** expected to handle every edge case (e.g., pre-existing Firestore with
wrong settings requires manual intervention, which `README.md` should document).

The script should:
1. Enable required APIs: `firestore.googleapis.com`, `storage.googleapis.com`, `aiplatform.googleapis.com`, `texttospeech.googleapis.com`, `run.googleapis.com`, `firebase.googleapis.com`
2. Attempt to create a Firestore database in Native mode (us-central1); skip gracefully if one exists
3. Attempt to create GCS bucket `{GCP_PROJECT_ID}-story-assets`; skip gracefully if it exists
4. Create or update service account `voice-story-agent-sa` with roles: `roles/datastore.user`, `roles/storage.objectAdmin`, `roles/aiplatform.user`, `roles/logging.logWriter`
5. Download a key JSON to `.credentials/sa-key.json` (git-ignored)

`README.md` documents any manual steps needed when re-running on an already-configured project.

**Done when**:
- Running on a fresh GCP project produces working Firestore, GCS bucket, and service account
- Re-running on an already-configured project exits without error (resource-exists warnings are acceptable)
- `README.md` lists any known cases requiring manual intervention

**Depends**: —

---

## Phase 1 — Data Layer (Pydantic Models + Stores)

### T-004 · Pydantic models: Session, StoryBrief, UserTurn

**Priority**: P1
**Status**: ✅ Done — `app/models/session.py` created with `Session`, `UserTurn`, `StoryBrief`, `SessionStatus`, `TurnPhase`, `Speaker`, `Tone` enums. All field validators (page_count=5, story_arc non-empty, max_length) verified. Ruff clean.
**Files**:
- `voice-story-agent/backend/app/models/session.py`

**Description**:
Define all Pydantic v2 models matching the data model spec:

- `SessionStatus` enum: `setup | generating | complete | error`
- `TurnPhase` enum: `setup | steering | narration`
- `Tone` enum: `silly | sleepy | adventurous | warm | curious`
- `Session` model with all fields from data-model.md §1
- `StoryBrief` model with all fields from data-model.md §3
- `UserTurn` model with all fields from data-model.md §2

All timestamps are `datetime` (UTC). Nullable fields use `Optional[T] = None`.
Include `model_config = ConfigDict(use_enum_values=True)`.

**Done when**:
- `from app.models.session import Session, StoryBrief, UserTurn` succeeds
- `Session(session_id=uuid4(), status="setup", ...)` constructs without error
- All field validations (maxLength, minimum, etc.) match the data model spec

**Depends**: T-001

---

### T-005 · Pydantic models: CharacterBible, StyleBible, ContentPolicy, CharacterRef

**Priority**: P1
**Status**: ✅ Done — `app/models/character_bible.py` created with `ProtagonistProfile`, `StyleBible`, `ContentPolicy`, `CharacterRef`, `CharacterBible`. notable_traits 2–4 bound + non-empty validator, introduced_on_page 1–5 bound. All models exported from `app/models/__init__.py`. 34 new tests (102 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/models/character_bible.py`

**Description**:
Define:
- `CharacterRef` model (char_id, name, description, reference_image_gcs_uri, introduced_on_page, voice_command_id)
- `StyleBible` model (art_style, color_palette, mood, negative_style_terms, last_updated_by_command_id)
- `ContentPolicy` model (exclusions: list[str], derived_from_safety_decisions: list[UUID])
- `ProtagonistProfile` model (name, species_or_type, color, attire, notable_traits, reference_image_gcs_uri)
- `CharacterBible` model embedding ProtagonistProfile, StyleBible, ContentPolicy, list[CharacterRef]

**Done when**:
- All models import cleanly
- `CharacterBible(protagonist=ProtagonistProfile(...), style_bible=StyleBible(...), ...)` constructs
- `CharacterBible.content_policy.exclusions` is a list of strings

**Depends**: T-001

---

### T-006 · Pydantic models: Page, PageAsset, VoiceCommand, SafetyDecision

**Priority**: P1
**Status**: ✅ Done — `app/models/page.py`, `app/models/voice_command.py`, `app/models/safety.py` created. PageStatus/AssetType/AssetStatus/CommandType/SafetyCategory/SafetyPhase enums defined. SafetyResult dataclass and SAFE_FALLBACK_REWRITE constant added. SafetyPhase restricted to setup|steering (not narration). All symbols exported from __init__.py. 77 new tests (179 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/models/page.py`
- `voice-story-agent/backend/app/models/voice_command.py`
- `voice-story-agent/backend/app/models/safety.py`

**Description**:
Define:
- `PageStatus` enum: `pending | text_ready | assets_generating | complete | error`
- `AssetType` enum: `illustration | narration`
- `AssetStatus` enum: `pending | generating | ready | failed`
- `Page` model (page_number, status, beat, text, narration_script, illustration_failed, audio_failed, steering_applied, generated_at)
- `PageAsset` model (asset_id, page_number, asset_type, generation_status, gcs_uri, signed_url, signed_url_expires_at, error_detail, generated_at)
- `CommandType` enum: `tone_change | pacing_change | element_reintroduction | character_introduction`
- `VoiceCommand` model (all fields from data-model.md §9)
- `SafetyCategory` enum (all 6 categories)
- `SafetyDecision` model (all fields from data-model.md §8)
- `SafetyResult` dataclass: `safe: bool`, `category: SafetyCategory | None`, `rewrite: str | None`

Also define a module-level constant:

```python
SAFE_FALLBACK_REWRITE = (
    "How about a story where our character goes on a fun adventure "
    "and helps a friend along the way?"
)
```

This constant is used as the fallback rewrite whenever the safety classifier fails or is
unavailable. It is child-safe, warm, and actionable as a story premise.

**Done when**:
- All models import cleanly
- `PageAsset(asset_type="illustration", generation_status="pending", ...)` constructs
- `SafetyResult(safe=False, category="physical_harm", rewrite="...")` constructs
- `SAFE_FALLBACK_REWRITE` is importable from `app.models.safety`

**Depends**: T-001

---

### T-007 · SessionStore — Session + StoryBrief + UserTurn CRUD

**Priority**: P1
**Status**: ✅ Done — `app/services/session_store.py` implemented with all 8 methods. `SessionNotFoundError` in `app/exceptions.py`. Firestore client injected via constructor for testability. 22 mock-based tests (201 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/session_store.py`

**Description**:
Implement `SessionStore` using the `google-cloud-firestore` async client. Methods:

```python
async def create_session(session: Session) -> None
async def get_session(session_id: str) -> Session
async def update_session_status(session_id: str, status: SessionStatus) -> None
async def update_story_arc(session_id: str, arc: list[str]) -> None
async def save_story_brief(session_id: str, brief: StoryBrief) -> None
async def get_story_brief(session_id: str) -> StoryBrief | None
async def save_user_turn(session_id: str, turn: UserTurn) -> None
async def list_user_turns(session_id: str) -> list[UserTurn]
```

Firestore paths match data-model.md. Raise `SessionNotFoundError` (custom exception) when
a session document does not exist. All methods accept and return Pydantic models; Firestore
serialisation/deserialisation is internal.

**Done when**:
- All methods can be called against a real Firestore emulator (`firebase emulators:start --only firestore`)
- `create_session → get_session` round-trip preserves all fields
- `update_session_status("id", "generating")` reflects in `get_session`
- `SessionNotFoundError` is raised for unknown session IDs

**Depends**: T-004

---

### T-008 · SessionStore — Page + PageAsset CRUD

**Priority**: P1
**Status**: ✅ Done — `session_store.py` extended with 7 methods: save_page, get_page, list_pages, save_page_asset, get_page_asset, list_page_assets, update_page_asset_status. Document IDs are str(page_number) and asset_type. update_page_asset_status sets generated_at on ready/failed only. 24 mock-based tests (225 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/session_store.py` (extend)

**Description**:
Add methods:

```python
async def save_page(session_id: str, page: Page) -> None
async def get_page(session_id: str, page_number: int) -> Page | None
async def list_pages(session_id: str) -> list[Page]
async def save_page_asset(session_id: str, asset: PageAsset) -> None
async def get_page_asset(session_id: str, page_number: int, asset_type: AssetType) -> PageAsset | None
async def list_page_assets(session_id: str, page_number: int) -> list[PageAsset]
async def update_page_asset_status(
    session_id: str, page_number: int, asset_type: AssetType,
    status: AssetStatus, gcs_uri: str | None = None
) -> None
```

Firestore paths: `sessions/{id}/pages/{n}` and `sessions/{id}/pages/{n}/assets/{type}`.

**Done when**:
- `save_page → get_page` round-trip preserves all fields
- `save_page_asset → get_page_asset` round-trip for both asset types
- `update_page_asset_status` to `ready` with a GCS URI is reflected on `get_page_asset`

**Depends**: T-006, T-007

---

### T-009 · SessionStore — VoiceCommand + SafetyDecision + CharacterBible CRUD

**Priority**: P1
**Status**: ✅ Done — `session_store.py` extended with 9 methods. save_character_bible writes CharacterBible+StyleBible in a single Firestore batch. update_character_bible_field supports dot-notation field paths. 24 mock-based tests (249 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/session_store.py` (extend)

**Description**:
Add methods:

```python
async def save_voice_command(session_id: str, cmd: VoiceCommand) -> None
async def list_voice_commands(session_id: str) -> list[VoiceCommand]
async def save_safety_decision(session_id: str, decision: SafetyDecision) -> None
async def list_safety_decisions(session_id: str) -> list[SafetyDecision]
async def save_character_bible(session_id: str, bible: CharacterBible) -> None
async def get_character_bible(session_id: str) -> CharacterBible | None
async def update_character_bible_field(session_id: str, field: str, value: Any) -> None
async def save_style_bible(session_id: str, style: StyleBible) -> None
async def get_style_bible(session_id: str) -> StyleBible | None
```

`save_character_bible` writes to `sessions/{id}/character_bible/main`; also writes the
`style_bible` sub-map atomically in the same batch.

**Done when**:
- All CRUD round-trips verified against Firestore emulator
- `update_character_bible_field("id", "content_policy.exclusions", [...])` merges correctly
- Batch write for `save_character_bible` + `save_style_bible` is a single Firestore commit

**Depends**: T-005, T-006, T-007

---

### T-010 · MediaPersistenceService

**Priority**: P1
**Status**: ✅ Done — `app/services/media_persistence.py` implemented with 4 async methods. GCS sync client wrapped via `asyncio.to_thread()`. `MediaPersistenceError` added to `app/exceptions.py`. V4 signed URLs, `gs://` URI helpers. 26 mock-based tests (275 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/media_persistence.py`

**Description**:
Implement `MediaPersistenceService` using the `google-cloud-storage` async client:

```python
async def store_illustration(session_id: str, page: int, image_bytes: bytes) -> str
async def store_narration(session_id: str, page: int, audio_bytes: bytes) -> str
async def store_character_ref(session_id: str, char_id: str, image_bytes: bytes) -> str
async def get_signed_url(gcs_uri: str, expiry_seconds: int = 3600) -> str
```

GCS key patterns:
- `sessions/{session_id}/pages/{page}/illustration.png`
- `sessions/{session_id}/pages/{page}/narration.mp3`
- `sessions/{session_id}/characters/{char_id}_ref.png`

All store methods return `gs://{bucket}/{key}`. Signed URLs use `v4` signatures.
Raise `MediaPersistenceError` on GCS API failure.

**Done when**:
- Store methods return correctly formatted `gs://` URIs
- `get_signed_url` returns an `https://storage.googleapis.com/...` URL
- Methods can run against a GCS emulator or real bucket in CI

**Depends**: T-001

---

## Phase 2 — WebSocket Foundation

### T-011 · REST: POST /sessions + GET /sessions/{id} + page endpoints

**Priority**: P1
**Status**: ✅ Done — 6 REST endpoints implemented across `app/routers/sessions.py` and `app/routers/pages.py`. Shared `get_store()` dependency in `app/dependencies.py`; `ErrorResponse` schema in `app/schemas.py`. Custom HTTPException handler normalises all 4xx to `{"error": str}`. `POST /sessions` returns `wss://{host}/ws/story/{session_id}`. 38 mock-based tests (313 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/routers/sessions.py`
- `voice-story-agent/backend/app/routers/pages.py`
- `voice-story-agent/backend/app/main.py` (mount routers)

**Description**:
Implement REST endpoints exactly matching `contracts/api-spec.yaml`:

- `POST /sessions` → creates Session (status=setup), returns `{session_id, ws_url}`
- `GET /sessions/{session_id}` → returns full `Session` JSON; 404 on unknown ID
- `GET /sessions/{session_id}/pages/{n}` → returns `Page`; 404 if not yet generated
- `GET /sessions/{session_id}/pages/{n}/assets` → returns `{page_number, assets: [PageAsset]}`
- `GET /sessions/{session_id}/pages/{n}/assets/{type}` → returns single `PageAsset`
- `POST /sessions/{session_id}/voice-commands` → creates `VoiceCommand` (no generation yet; stubs command routing); returns `VoiceCommand`

All endpoints validate path parameters and return the `Error` schema on failure.

**Done when**:
- All endpoints return correct HTTP status codes
- `POST /sessions` returns a `ws_url` of the form `wss://{host}/ws/story/{session_id}`
- 404 responses use the `Error` schema
- `pytest tests/test_rest_endpoints.py` passes with httpx TestClient

**Depends**: T-007, T-008, T-009

---

### T-012 · WebSocket handler skeleton

**Priority**: P1
**Status**: ✅ Done — `app/websocket/story_ws.py` implemented with `emit()` helper, token validation (stub: any non-empty string), session lookup via `SessionStore`, and full message dispatch (`ping→pong`, `session_start→voice_session_ready`, unknown→`session_error`). Mounted in `app/main.py`. 26 WebSocket tests (339 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py`
- `voice-story-agent/backend/app/main.py` (mount WS route)

**Description**:
Implement the WebSocket endpoint `/ws/story/{session_id}`:

1. On connect: validate Bearer token from `?token=` query param; reject with 4001 close code if invalid. Emit `connected` event with current `session_status`.
2. Dispatch incoming text frames by `type`:
   - `session_start` → acknowledge; emit `voice_session_ready` (stub: no ADK yet)
   - `ping` → emit `pong`
   - unknown type → emit `session_error` with code `unknown_message_type`
3. On close: log session_id + close reason.

All outbound JSON emissions go through a single `emit(ws, event_type, **fields)` helper
to ensure consistent `{"type": "..."}` framing.

**Done when**:
- WS client connecting to `/ws/story/{id}` receives `connected` immediately
- Sending `{"type": "ping"}` receives `{"type": "pong"}`
- Sending `{"type": "session_start"}` receives `{"type": "voice_session_ready"}`
- Connecting without a valid token closes with code 4001

**Depends**: T-011

---

### T-013 · VoiceSessionService — ADK bidi-stream open/close/audio

**Priority**: P1
**Status**: ✅ Done — `app/services/adk_voice_service.py` implemented with `start`, `send_audio`, `end` lifecycle. Mock-based client injection for testability. `VoiceSessionNotFoundError` + `VoiceSessionError` in `app/exceptions.py`. No ADK private-class references. 26 mock-based tests (365 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/adk_voice_service.py`

**Description**:
Implement `VoiceSessionService` wrapping the Google ADK SDK. The service must expose
the following behaviour; avoid coupling the implementation to specific ADK internal class
names or method signatures that may change between SDK versions — use the ADK's public
API surface and wrap it behind this interface:

```python
async def start(session_id: str, system_prompt: str) -> None
async def send_audio(session_id: str, pcm_bytes: bytes) -> None
async def end(session_id: str) -> None
```

`start` opens a bidi-streaming session with the Gemini Live model via the ADK SDK.
Store active sessions in an in-memory dict keyed by `session_id`.
`send_audio` forwards one PCM audio chunk (16-bit, 16 kHz, mono) to the open stream.
`end` closes the stream and removes the session entry.
Raise `VoiceSessionNotFoundError` if `session_id` is not open.
Raise `VoiceSessionError` on ADK API failures.

**Done when**:
- `start` + `end` lifecycle works against Gemini Live (or against an ADK mock in unit test)
- `send_audio` on a non-existent session raises `VoiceSessionNotFoundError`
- `end` on an already-closed session is a no-op (not an error)
- Implementation does not reference ADK internal/private classes directly (`_` prefix)

**Depends**: T-001

---

### T-014 · VoiceSessionService — stream_turns + speak

**Priority**: P1
**Status**: ✅ Done — `stream_turns` and `speak` added to `VoiceSessionService`. `VoiceTurn` dataclass (`role`, `transcript`, `audio_bytes`, `is_final`) defined in same module. `stream_turns` normalises all ADK event shapes (input_transcription + model_turn) into `VoiceTurn`; handles both `finished` and `is_final` field names across SDK versions. `speak` sends via `send_client_content` and awaits `turn_complete` with a configurable 10 s timeout raising `VoiceSessionError`. 26 mock-based tests (391 total passing). Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/adk_voice_service.py` (extend)

**Description**:
Add:

```python
async def stream_turns(session_id: str) -> AsyncIterator[VoiceTurn]
async def speak(session_id: str, text: str) -> None
```

`VoiceTurn` dataclass: `role: Literal["user", "agent"]`, `transcript: str`,
`audio_bytes: bytes | None`, `is_final: bool`.

`stream_turns` yields `VoiceTurn` objects as the ADK stream delivers them — partial user
transcripts arrive with `is_final=False`, followed by a final transcript and then agent turns.
The exact event names and payload structure from the ADK may vary by version; normalise
them into `VoiceTurn` inside this service so no other code depends on ADK event shapes.

`speak` sends the given text to the Gemini Live model for voice synthesis and waits until
the agent's audio response has been fully delivered before returning. Apply a 10 s timeout;
raise `VoiceSessionError` if the response does not complete within the timeout.

**Done when**:
- `stream_turns` yields at least one partial and one final `VoiceTurn` in an ADK mock scenario
- `speak` resolves once the agent audio response is complete (verified via mock)
- `speak` raises `VoiceSessionError` after a 10 s timeout with no completion signal
- `VoiceTurn.audio_bytes` is non-None for agent turns

**Depends**: T-013

---

### T-015 · WebSocket handler — audio streaming + turn routing

**Priority**: P1
**Status**: ✅ Done — `story_ws.py` extended: binary frames forwarded to `VoiceSessionService.send_audio` (VoiceSessionNotFoundError silently swallowed before session_start); `session_start` now calls `voice_svc.start` with `_SETUP_SYSTEM_PROMPT` and spawns `_turn_loop` background task; `_turn_loop` iterates `stream_turns`, emits `transcript` JSON events + binary audio frames for agent turns, routes final user turns via `_route_user_turn` → `turn_detected` events; `transcript_input` message creates synthetic `VoiceTurn` and routes directly to `turn_detected`; `voice_svc.end` called in `finally` block. `get_voice_service` singleton dependency added to `app/dependencies.py`. 33 new mock-based tests (424 total passing). Existing T-012 tests updated to override `get_voice_service`. Ruff clean.
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)

**Description**:
Wire VoiceSessionService into the WebSocket handler:

1. On `session_start`: call `VoiceSessionService.start` with the setup system prompt; emit `voice_session_ready`.
2. On binary frame: call `VoiceSessionService.send_audio`; ignore if session not yet started.
3. Start background task `_turn_loop` that calls `VoiceSessionService.stream_turns` and for each `VoiceTurn`:
   - Emit `transcript` with `role`, `text`, `is_final`, `phase`, `turn_id`
   - On `is_final = True` and `role = "user"`: route to the appropriate pipeline (stub: log only for now)
4. On `transcript_input` text message: wrap in a synthetic `VoiceTurn(is_final=True)` and inject into the same routing path.

**Done when**:
- Sending binary audio frames triggers `transcript` events back on the WebSocket
- `transcript_input` produces a `turn_detected` event
- Agent audio chunks arrive as binary frames after `speak` is called

**Depends**: T-012, T-014

---

## Phase 3 — Safety Layer (US3)

### T-016 · SafetyService — classifier + rewriter

**Priority**: P1
**Status**: ✅ Done — `app/services/safety_service.py` implemented with `evaluate(utterance, *, session_id="") -> SafetyResult`. Single Gemini 2.5 Flash call with `response_mime_type="application/json"` and a detailed system prompt defining permitted/forbidden content + rewrite rules. Fail-safe: any Gemini exception or malformed response returns `SafetyResult(safe=False, category=None, rewrite=SAFE_FALLBACK_REWRITE)`; original utterance never logged or passed downstream. Unknown category strings degrade to `None` gracefully. Injectable `genai.Client` for testability. TEST-S01: 30 mock-based unit tests (454 total passing, 30 integration tests deselected). TEST-S02: 30 integration tests registered under `@pytest.mark.integration` (run with `-m integration`). `pytest.ini` updated with `integration` marker. Ruff clean.
**Files**:
- `voice-story-agent/backend/app/services/safety_service.py`

**Description**:
Implement `SafetyService`:

```python
async def evaluate(utterance: str) -> SafetyResult
```

Single Gemini 2.5 Flash call with structured output. System prompt defines:
- Permitted content: emotional realism (sadness, fear, loneliness, conflict, mild peril)
- Forbidden content: physical harm, character death, gore, destruction, sexual content, fear escalation
- Output schema: `{ "safe": bool, "category": str|null, "rewrite": str|null }`

If `safe=False`, `rewrite` MUST be a complete child-safe alternative that:
- Does NOT quote, reference, or paraphrase the forbidden elements from the input
- Is warm, age-appropriate, and actionable as a story premise
- Is ≤ 80 words

**On any Gemini API exception or malformed response** (fail-safe behavior, aligned with
"Child Safety First" constitution principle):
- Log the error with `session_id` and error type (NOT the original utterance)
- Return `SafetyResult(safe=False, category=None, rewrite=SAFE_FALLBACK_REWRITE)`
- The original utterance MUST NOT be passed to any generation call when the classifier fails
- `SAFE_FALLBACK_REWRITE` is the pre-defined constant from `app.models.safety`

This ensures the system fails closed: when the classifier is uncertain, the child-facing
story flow always uses a known-safe premise rather than potentially unsafe user input.

**Done when**:
- `evaluate("a story where the dragon kills everyone")` returns `safe=False`, `category="character_death"`, `rewrite` non-empty
- `evaluate("the bunny feels very sad and cries")` returns `safe=True`, `category=None`
- `evaluate("make the monster hurt the bird")` returns `safe=False`, `category="physical_harm"`
- On mocked Gemini exception: returns `SafetyResult(safe=False, rewrite=SAFE_FALLBACK_REWRITE)`; the original utterance is not surfaced

**Depends**: T-006

---

### TEST-S01 · Safety transformation unit tests — forbidden categories

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_safety_service.py`

**Description**:
Unit tests using `pytest-asyncio`. Mock `SafetyService._call_gemini` to return controlled
JSON responses. Test one representative input per forbidden category:

| Input | Expected category | Expected safe |
|-------|-------------------|---------------|
| `"a story where the dragon kills everyone"` | `character_death` | `False` |
| `"the monster punches the rabbit really hard"` | `physical_harm` | `False` |
| `"blood everywhere and gory wounds"` | `gore` | `False` |
| `"burn the village to the ground"` | `destruction` | `False` |
| `"sexual content involving characters"` | `sexual_content` | `False` |
| `"building dread, horror, sustained terror"` | `fear_escalation` | `False` |

Additionally verify emotional realism pass-through:

| Input | Expected safe |
|-------|---------------|
| `"the bunny feels very sad and cries"` | `True` |
| `"the character is scared of the dark"` | `True` |
| `"loneliness is the theme of the story"` | `True` |
| `"they face a really tough obstacle"` | `True` |

Additionally verify fail-safe behavior on classifier error:

| Scenario | Expected result |
|----------|----------------|
| `_call_gemini` raises any exception | `safe=False`, `rewrite == SAFE_FALLBACK_REWRITE` |
| `_call_gemini` returns malformed JSON | `safe=False`, `rewrite == SAFE_FALLBACK_REWRITE` |

**Done when**:
- All 12 test cases pass
- Each `safe=False` forbidden-category test asserts `result.category` is the expected enum value
- Each `safe=True` test asserts `result.rewrite is None`
- Exception and malformed-response tests assert the original utterance does not appear in the result

**Depends**: T-016

---

### TEST-S02 · Safety rewrite content tests — no unsafe leakage

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_safety_service.py` (extend)

**Description**:
Test that `SafetyResult.rewrite` never contains words from the original unsafe utterance.

For each forbidden-category input from TEST-S01, use real Gemini (integration test, marked
`@pytest.mark.integration`) and assert:

1. `result.rewrite` does NOT contain any of the following from the original input:
   - Forbidden action verbs: "kills", "punches", "burns", "destroy", "hurt", "harm", "gore"
   - Forbidden nouns in harm context: "blood", "wound", "death"
2. `result.rewrite` is ≤ 80 words
3. `result.rewrite` is a complete, coherent sentence (does not start or end mid-word)
4. `result.rewrite` contains at least one of: a character type, an action verb, or a setting — making it actionable as a story premise

**Done when**:
- All 6 integration tests pass against real Gemini 2.5 Flash
- Zero occurrences of forbidden terms in any rewrite

**Depends**: T-016, TEST-S01

---

### TEST-S03 · Safety decision persistence integration tests

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_safety_persistence.py`

**Description**:
Integration tests (Firestore emulator) verifying that accepting a safety rewrite correctly
mutates the ContentPolicy:

1. Create a session with a CharacterBible that has `content_policy.exclusions = ["no gore"]`
2. Create a `SafetyDecision` with `exclusion_added = "no character harm"` and `user_accepted = True`
3. Call `SessionStore.save_safety_decision`
4. Call `SessionStore.update_character_bible_field("content_policy.exclusions", [...existing + new])`
5. Assert `get_character_bible().content_policy.exclusions` contains both `"no gore"` and `"no character harm"`
6. Assert `get_character_bible().content_policy.derived_from_safety_decisions` contains the decision's UUID
7. Assert `get_session().status` is set to `error` if a `SafetyDecision(user_accepted=False, phase="setup")` is saved

**Done when**:
- All 7 assertions pass against Firestore emulator
- ContentPolicy update is transactional (no partial writes on emulator crash simulation)

**Depends**: T-009, T-016

---

### T-017 · WebSocket safety gate integration

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)

**Description**:
In the `_turn_loop`, for every `is_final=True` user turn:

1. Call `SafetyService.evaluate(turn.transcript)` **before** any routing. This call always
   returns a result (never raises); if the classifier is unavailable, it returns the
   pre-defined fallback rewrite (fail-safe behavior — see T-016).

2. If `safe=False`:
   a. Emit `safety_rewrite` event: `{decision_id, turn_id, detected_category, proposed_rewrite, phase}`
   b. Call `VoiceSessionService.speak(proposed_rewrite)` so the child hears the safe alternative
   c. Await the next user turn as the acknowledgement
   d. Treat any response as acceptance — the agent's rewrite is used as the story premise.
      For MVP, there is no re-evaluation of the acknowledgement; the agent's proposed rewrite
      is already safe and the child's response is assumed to be a go-ahead
   e. Persist `SafetyDecision(user_accepted=True)` → append exclusion to ContentPolicy → emit `safety_accepted`

3. If `safe=True`: proceed to normal routing.

4. On WebSocket disconnect before acknowledgement is received: persist
   `SafetyDecision(user_accepted=False)` and close the session if `phase = setup`.

**Done when**:
- Sending an unsafe utterance via `transcript_input` triggers `safety_rewrite` before any other routing event
- Any follow-up response from the user triggers `safety_accepted` with the agent's rewrite as `final_premise`
- `get_character_bible().content_policy.exclusions` contains the new exclusion after acceptance
- A classifier failure (mocked exception) triggers `safety_rewrite` with `SAFE_FALLBACK_REWRITE` — the original unsafe utterance does not reach any generation call

**Depends**: T-015, T-016, T-009

---

## Phase 4 — Story Setup Flow (US1 + US2)

### T-018 · StoryPlannerService — create_arc

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/services/story_planner.py`

**Description**:
Implement:

```python
async def create_arc(
    brief: StoryBrief,
    bible: CharacterBible
) -> list[str]  # 5 beat strings
```

Single Gemini 2.5 Pro call with structured output. System prompt includes:
- Story parameters from `StoryBrief` (protagonist, setting, tone)
- All `ContentPolicy.exclusions` as hard constraints
- Required narrative structure: opening (p1), complication (p2–3), climax (p4), resolution (p5)
- Output schema: `{ "beats": ["...", "...", "...", "...", "..."] }` — each ≤ 40 words

Return the `beats` list. Raise `StoryPlannerError` on API failure.

**Done when**:
- Returns a list of exactly 5 non-empty strings
- No beat contains any string in `ContentPolicy.exclusions`
- Retry logic fires on transient Gemini error (2 retries → falls back to Gemini Flash on 3rd)

**Depends**: T-005, T-006

---

### T-019 · CharacterBibleService — initialise

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/services/character_bible_service.py`

**Description**:
Implement:

```python
async def initialise(session_id: str, brief: StoryBrief) -> CharacterBible
```

Uses a single Gemini 2.5 Flash call to derive:
- `ProtagonistProfile` (species_or_type, color, attire, notable_traits) from `brief.protagonist_description`
- `StyleBible` (art_style, color_palette, mood, negative_style_terms) from `brief.tone`
- `ContentPolicy` with base exclusions: `["no gore", "no character death", "no physical harm", "no sexual content", "no fear escalation", "no destruction of characters"]`

Persists both `CharacterBible` and `StyleBible` via `SessionStore` in a single batch.
Returns the constructed `CharacterBible`.

**Done when**:
- `initialise` produces a `CharacterBible` where `protagonist.color` matches the color mentioned in `brief.protagonist_description`
- `style_bible.mood` reflects the `brief.tone` value
- Base exclusions are all present in `content_policy.exclusions`
- `SessionStore.get_character_bible(session_id)` returns the persisted bible after `initialise`

**Depends**: T-005, T-006, T-009

---

### T-020 · WebSocket setup parameter extraction flow

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)
- `voice-story-agent/backend/app/websocket/setup_handler.py` (new)

**Description**:
Implement the setup turn routing pipeline in `SetupHandler`:

1. After safety check passes, extract story parameters (protagonist, setting, tone) using Gemini Flash structured output
2. Emit `story_brief_updated` for each newly confirmed parameter
3. If any parameter is missing and < 3 turns used: `VoiceSessionService.speak(follow_up_question)`
4. If all 3 parameters confirmed: emit `story_brief_confirmed` with the full `StoryBrief` and `agent_summary`
5. On `story_brief_confirmed`: call `StoryPlannerService.create_arc` → `CharacterBibleService.initialise` → emit `character_bible_ready` → update `Session.status = generating`

If the user provides all 3 parameters in the first utterance (e.g., parent gives full description): confirm immediately, ask zero follow-up questions.

**Done when**:
- Single-utterance full setup (all 3 params given) produces `story_brief_confirmed` with zero follow-up `speak` calls
- Partial setup (1 param given) produces exactly one follow-up question before the next `story_brief_updated`
- `character_bible_ready` event is emitted after `story_brief_confirmed`
- `Session.status = "generating"` after `character_bible_ready`

**Depends**: T-018, T-019, T-017

---

### T-021 · REST: POST /sessions/{id}/voice-session + character-bible/generate

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/routers/sessions.py` (extend)

**Description**:
Implement:

- `POST /sessions/{session_id}/voice-session` → opens a bidi-streaming session with Gemini Live via `VoiceSessionService.start`; returns `{session_id, ready: true, voice_model}`; returns 409 if session not in `setup` status
- `POST /sessions/{session_id}/character-bible/generate` → calls `CharacterBibleService.initialise` (using stored `StoryBrief`); returns `CharacterBible`; returns 409 if session not in `generating` or `StoryBrief` not confirmed

**Done when**:
- `POST /sessions/{id}/voice-session` on a `setup` session returns 200 with `ready: true`
- `POST /sessions/{id}/voice-session` on a `generating` session returns 409
- `POST /sessions/{id}/character-bible/generate` returns the full `CharacterBible` JSON
- Both endpoints return 404 for unknown session IDs

**Depends**: T-011, T-013, T-019

---

## Phase 5 — Page Generation (US6)

### T-022 · StoryPlannerService — expand_page

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/services/story_planner.py` (extend)

**Description**:
Implement:

```python
async def expand_page(
    beat: str,
    page_history: list[str],  # one-sentence summaries of prior pages
    bible: CharacterBible
) -> tuple[str, str]  # (display_text, narration_script)
```

Single Gemini 2.5 Flash call. System prompt includes:
- The current `beat` (what happens on this page)
- `page_history` for narrative coherence
- Protagonist name, description, and tone from `CharacterBible`
- All `ContentPolicy.exclusions` as negative constraints
- Output schema: `{ "text": str (60–120 words), "narration_script": str }`

Validate that `text` word count is 60–120; if outside range, retry once with a stricter prompt.

**Done when**:
- Returns `(text, narration_script)` where `text` is 60–120 words
- None of the `content_policy.exclusions` strings appear in `text`
- `page_history` items are referenced (at least one character or event from prior pages appears) when `page_number ≥ 3`

**Depends**: T-005, T-006

---

### T-023 · ImageGenerationService

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/services/image_generation.py`

**Description**:
Implement `ImageGenerationService`:

```python
@dataclass
class ImagePrompt:
    text_prompt: str
    reference_urls: list[str]  # gs:// URIs for reference images

async def generate(prompt: ImagePrompt) -> bytes
```

Call Imagen 3 on Vertex AI (`imagegeneration@006` or latest). Pass `reference_urls` as
reference images in the API request. Return raw PNG bytes.

Retry policy: 1 retry with 2 s backoff. After both failures, raise `ImageGenerationError`.
Log the Imagen prompt text (without reference URLs) to Cloud Logging.

**Done when**:
- `generate(ImagePrompt(text_prompt="...", reference_urls=[]))` returns PNG bytes > 0
- `generate(ImagePrompt(text_prompt="...", reference_urls=["gs://..."]))` includes reference image in Imagen call
- `ImageGenerationError` is raised after 2 consecutive failures (mock test)

**Depends**: T-001

---

### T-024 · TTSService

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/services/tts_service.py`

**Description**:
Implement `TTSService`:

```python
@dataclass
class VoiceConfig:
    voice_name: str  # e.g. "en-US-Neural2-C"
    language_code: str
    speaking_rate: float  # 0.85 for children's narration

async def synthesize(script: str, voice_config: VoiceConfig) -> bytes
```

Call Cloud Text-to-Speech `synthesize_speech`. Request MP3 audio. Return raw MP3 bytes.
Retry: 1 retry with 1 s backoff. After both failures, raise `TTSError`.

**Done when**:
- `synthesize(script, voice_config)` returns non-empty bytes
- The default voice config uses `speaking_rate=0.85` and a Neural2 voice
- `TTSError` raised after 2 consecutive failures (mock test)

**Depends**: T-001

---

### T-025 · PageOrchestrator

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/websocket/page_orchestrator.py`

**Description**:
Implement `PageOrchestrator` as an async coroutine (not a class):

```python
async def run_page(
    session_id: str,
    page_number: int,
    beat: str,
    page_history: list[str],
    emit: Callable,  # async (event_type, **fields) → None
    # injected services:
    story_planner: StoryPlannerService,
    character_bible_svc: CharacterBibleService,
    image_svc: ImageGenerationService,
    tts_svc: TTSService,
    media_svc: MediaPersistenceService,
    session_store: SessionStore,
) -> None
```

Sequence:
1. Emit `page_generating`; `session_store.save_page(Page(status="pending"))`
2. Call `story_planner.expand_page` → `(text, narration_script)` → emit `page_text_ready`; update `Page.status = text_ready`
3. Get `CharacterBible`; call `character_bible_svc.build_image_prompt(bible, text, page_number)`
4. Launch image and TTS generation **in parallel** via `asyncio.gather(return_exceptions=True)`:
   - Image: `image_svc.generate(prompt)` → `media_svc.store_illustration` → signed URL → emit `page_image_ready` OR emit `page_asset_failed(asset_type="illustration")` on error
   - TTS: `tts_svc.synthesize(narration_script)` → `media_svc.store_narration` → signed URL → emit `page_audio_ready` OR emit `page_asset_failed(asset_type="narration")` on error
5. If page 1 image succeeded: `character_bible_svc.set_reference_image(gcs_uri)`
6. `session_store.save_page(page)` with final status + asset flags
7. Emit `page_complete`

Asset failures MUST NOT raise; they must emit `page_asset_failed` and set `illustration_failed`/`audio_failed` flags. `page_complete` MUST always fire.

**Done when**:
- Happy path: events fired in order: `page_generating` → `page_text_ready` → `page_image_ready` + `page_audio_ready` (order not guaranteed) → `page_complete`
- Image failure: `page_asset_failed(asset_type="illustration")` fires, `page_complete(illustration_failed=True)` fires
- Audio failure: `page_asset_failed(asset_type="narration")` fires, `page_complete(audio_failed=True)` fires
- Both fail: `page_complete(illustration_failed=True, audio_failed=True)` fires
- `page_complete` fires even when both assets fail

**Depends**: T-022, T-023, T-024, T-010, T-007, T-008

---

### TEST-P01 · Page streaming event sequence tests

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_page_orchestrator.py`

**Description**:
Unit tests for `PageOrchestrator` with all services mocked.

**Test cases**:

1. **Happy path event order**: mock all services to succeed. Capture all events emitted.
   Assert the sequence: `page_generating` → `page_text_ready` → (`page_image_ready` AND `page_audio_ready` in any order) → `page_complete`.
   Assert `page_complete.illustration_failed = False` and `page_complete.audio_failed = False`.

2. **Page text content**: assert `page_text_ready.text` equals the mocked `expand_page` return value.

3. **page_complete always fires**: assert `page_complete` is the final event emitted regardless of service outcomes (test with succeed + mock the assertion using a "finally fires" pattern).

4. **Event count**: assert exactly 4 unique event types are emitted in the happy path (no duplicates for image/audio events).

5. **Page number propagation**: assert every emitted event carries `page = page_number` matching what was passed to `run_page`.

**Done when**:
- All 5 test cases pass with mocked services
- Event capture uses an in-memory emit list (no real WebSocket)

**Depends**: T-025

---

### TEST-P02 · Page streaming — asset failure tests

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_page_orchestrator.py` (extend)

**Description**:
**Test cases**:

1. **Image fails, audio succeeds**:
   - Mock `image_svc.generate` to raise `ImageGenerationError`
   - Assert `page_asset_failed` is emitted with `asset_type = "illustration"`
   - Assert `page_audio_ready` IS emitted (audio unaffected)
   - Assert `page_complete(illustration_failed=True, audio_failed=False)` is the final event

2. **Audio fails, image succeeds**:
   - Mock `tts_svc.synthesize` to raise `TTSError`
   - Assert `page_asset_failed` with `asset_type = "narration"`
   - Assert `page_image_ready` IS emitted
   - Assert `page_complete(illustration_failed=False, audio_failed=True)` is the final event

3. **Both fail**:
   - Mock both to raise
   - Assert two `page_asset_failed` events (one per type)
   - Assert `page_complete(illustration_failed=True, audio_failed=True)` fires
   - Assert no `page_image_ready` or `page_audio_ready` events

4. **Reference image set on page 1 success**: mock page_number=1, image succeeds.
   Assert `character_bible_svc.set_reference_image` is called with the stored GCS URI.

5. **Reference image NOT set on page 1 failure**: mock page_number=1, image fails.
   Assert `character_bible_svc.set_reference_image` is NOT called.

**Done when**:
- All 5 test cases pass

**Depends**: TEST-P01

---

### TEST-P03 · Page streaming — text validation tests

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_story_planner.py`

**Description**:
Unit tests for `StoryPlannerService.expand_page` word count and content policy enforcement.

**Test cases**:

1. **Word count validation — too short**: mock Gemini to return text with 45 words on first call, then 75 words on retry. Assert a retry fires and the 75-word result is returned.

2. **Word count validation — too long**: mock Gemini to return 155 words on first call, then 100 on retry. Assert retry fires and 100-word result is returned.

3. **Word count validation — within range**: mock Gemini to return 90 words. Assert no retry fires.

4. **Content policy enforcement**: mock `CharacterBible.content_policy.exclusions = ["no destruction"]`. Mock Gemini to return text containing "destruction" on first call, clean text on retry. Assert retry fires and clean text is returned.

5. **Page history injection**: assert that the string `page_history[0]` appears in the Gemini prompt text passed on call 1 (capture via mock call args).

**Done when**:
- All 5 test cases pass
- Retry assertions use `mock.call_count` to verify exactly 1 or 2 Gemini calls

**Depends**: T-022

---

### T-026 · REST: POST /sessions/{id}/pages/generate + WebSocket page loop

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/routers/pages.py` (extend)
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)

**Description**:
1. `POST /sessions/{id}/pages/generate` REST endpoint: validates preconditions (status=generating, no open steering window), enqueues `run_page` for `current_page + 1`, returns `{session_id, page_number, status: "generating"}`.
2. In `WebSocketHandler`: after `character_bible_ready` is emitted, start the page generation loop:
   ```
   for page_number in 1..5:
       await run_page(page_number, ...)
       open_steering_window(page_number)  # 10 s timer
       await steering_window_closed      # yield to steering handler
   emit story_complete
   update Session.status = complete
   ```
3. After all 5 pages: emit `story_complete`.

**Done when**:
- Full 5-page generation loop completes in integration test (all services mocked)
- `story_complete` is the final WS event
- `Session.status = "complete"` after `story_complete`
- REST endpoint returns 409 if steering window is open

**Depends**: T-025, T-020

---

## Phase 6 — Character Visual Consistency (US5)

### T-027 · CharacterBibleService — build_image_prompt

**Priority**: P1
**Files**:
- `voice-story-agent/backend/app/services/character_bible_service.py` (extend)

**Description**:
Implement (pure function, no I/O):

```python
def build_image_prompt(
    bible: CharacterBible,
    page_scene: str,
    page_number: int
) -> ImagePrompt
```

Rules:
- `text_prompt` always includes: art style, color palette, mood, negative style terms, protagonist name + species + color + notable traits + attire
- `text_prompt` includes `page_scene` as the action description
- `reference_urls`: empty list for page 1; for pages 2–5 includes `protagonist.reference_image_gcs_uri` (if set)
- For any `CharacterRef` whose `name` appears in `page_scene`: append that character's `reference_image_gcs_uri` to `reference_urls` (if set)
- `negative_style_terms` are prefixed with `"no "` and joined into the prompt as a negative clause

**Done when**:
- `build_image_prompt(bible, scene, page_number=1).reference_urls == []`
- `build_image_prompt(bible_with_ref, scene, page_number=2).reference_urls == [protagonist_gcs_uri]`
- Scene mentioning a secondary character includes that character's reference URL
- Scene NOT mentioning a secondary character does NOT include that character's reference URL

**Depends**: T-005, T-006

---

### TEST-C01 · Character consistency metadata unit tests — build_image_prompt

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_character_bible_service.py`

**Description**:
Pure unit tests (no I/O mocking needed — `build_image_prompt` is a pure function).

**Test cases**:

1. **Page 1 — no reference URLs**: bible has a protagonist with `reference_image_gcs_uri = None`. Call `build_image_prompt(bible, "...", 1)`. Assert `prompt.reference_urls == []`.

2. **Page 2 — protagonist reference URL present**: set `bible.protagonist.reference_image_gcs_uri = "gs://bucket/protagonist_ref.png"`. Call `build_image_prompt(bible, "...", 2)`. Assert `"gs://bucket/protagonist_ref.png" in prompt.reference_urls`.

3. **Page 2 — protagonist reference URL None**: set `reference_image_gcs_uri = None`. Call `build_image_prompt(bible, "...", 2)`. Assert `prompt.reference_urls == []` (no None values in list).

4. **Secondary character in scene**: add `CharacterRef(char_id="yellow_bird", name="Yellow Bird", reference_image_gcs_uri="gs://bucket/yellow_bird.png")` to bible. Call `build_image_prompt(bible, "Yellow Bird flew by", 3)`. Assert `"gs://bucket/yellow_bird.png" in prompt.reference_urls`.

5. **Secondary character NOT in scene**: same bible as above. Call `build_image_prompt(bible, "The bunny hopped alone", 3)`. Assert `"gs://bucket/yellow_bird.png" not in prompt.reference_urls`.

6. **Secondary character with None reference**: set char's `reference_image_gcs_uri = None`. Call `build_image_prompt(bible, "Yellow Bird flew by", 3)`. Assert `None not in prompt.reference_urls`.

7. **Negative style terms in prompt**: set `style_bible.negative_style_terms = ["realistic", "dark"]`. Assert `prompt.text_prompt` contains both terms (or their negations).

8. **Protagonist traits in prompt**: set `notable_traits = ["big round eyes", "stubby legs"]`. Assert both traits appear verbatim in `prompt.text_prompt`.

**Done when**:
- All 8 test cases pass with no external calls

**Depends**: T-027

---

### TEST-C02 · Character consistency — reference image propagation tests

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_character_bible_service.py` (extend)

**Description**:
Integration tests using Firestore emulator to verify reference image propagation across the service boundary.

**Test cases**:

1. **set_reference_image persists to protagonist**: call `character_bible_svc.set_reference_image(session_id, "gs://bucket/page1.png")`. Call `character_bible_svc.get(session_id)`. Assert `bible.protagonist.reference_image_gcs_uri == "gs://bucket/page1.png"`.

2. **After set_reference_image, build_image_prompt includes it**: call `set_reference_image`; then `bible = svc.get(session_id)`; then `build_image_prompt(bible, "...", 2)`. Assert `"gs://bucket/page1.png" in prompt.reference_urls`.

3. **set_secondary_reference persists**: add secondary character with `char_id = "bird"`. Call `set_secondary_reference(session_id, "bird", "gs://bucket/bird.png")`. Get bible. Assert `CharacterRef(char_id="bird").reference_image_gcs_uri == "gs://bucket/bird.png"`.

4. **Multiple reference pages**: set protagonist + one secondary reference. Call `build_image_prompt(bible, "Yellow Bird appeared", 4)`. Assert both URIs in `reference_urls` (length = 2).

5. **Reference image not overwritten on page 2+**: call `set_reference_image` for page 1. Simulate a page 2 orchestration completing. Assert `protagonist.reference_image_gcs_uri` is still the page 1 URI (set_reference_image is only called for page 1).

**Done when**:
- All 5 test cases pass against Firestore emulator

**Depends**: T-027, T-009

---

### TEST-C03 · Character consistency — CharacterBible full persistence round-trip

**Priority**: P1
**Files**:
- `voice-story-agent/backend/tests/test_character_bible_service.py` (extend)

**Description**:
Integration tests verifying CharacterBible survives Firestore serialisation/deserialisation
without field loss.

**Test cases**:

1. **Full CharacterBible round-trip**: construct a `CharacterBible` with all fields populated (including `StyleBible`, `ContentPolicy`, 2 `CharacterRef`s). Call `save_character_bible`. Call `get_character_bible`. Assert all nested fields equal the originals (deep equality check).

2. **StyleBible sync**: after `save_character_bible`, call `get_style_bible`. Assert `StyleBible` fields match `CharacterBible.style_bible`.

3. **ContentPolicy mutation survives reload**: call `add_content_exclusion(session_id, "no shadows")`. Call `get_character_bible`. Assert `"no shadows"` in `content_policy.exclusions`.

4. **Secondary character addition**: call `add_secondary_character` with a new `CharacterRef`. Call `get_character_bible`. Assert the character appears in `secondary_characters` list.

5. **mood update via StyleBible**: call `save_style_bible` with a new `mood = "funnier"`. Call `get_character_bible`. Assert `character_bible.style_bible.mood == "funnier"`.

**Done when**:
- All 5 tests pass with Firestore emulator

**Depends**: T-009, T-019

---

## Phase 7 — Steering + Voice Commands (US4) *(stretch)*

> **Stretch goal**: complete after core MVP is working. The product is fully demonstrable
> without steering. These tasks add live mid-story voice control, which significantly
> enhances the hackathon demo but is not required.

### T-028 · SteeringRouter (classify_steering)

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/websocket/steering_router.py`

**Description**:
Implement pure synchronous function:

```python
def classify_steering(
    utterance: str,
    safety_result: SafetyResult
) -> SteeringClassification
```

`SteeringClassification` dataclass: `type: CommandType | Literal["ambiguous", "unsafe"]`, `confidence: float`, `detail: str | None`.

Classification heuristics (regex + keyword matching, no Gemini call):
- `tone_change`: "funnier", "sillier", "calmer", "scarier", "more exciting", "sleepier"
- `pacing_change`: "faster", "slower", "shorter", "longer", "more detail"
- `element_reintroduction`: "bring back", "remember the", "what happened to"
- `character_introduction`: "add a", "give him/her/them a", "introduce", "new friend", "new character"
- `ambiguous`: none of the above patterns match
- `unsafe`: `safety_result.safe == False` (always wins)

**Done when**:
- `classify_steering("make it funnier", safe_result)` returns `type = tone_change`
- `classify_steering("give him a bird friend", safe_result)` returns `type = character_introduction`
- `classify_steering("make it different", safe_result)` returns `type = ambiguous`
- `classify_steering("hurt the bird", unsafe_result)` returns `type = unsafe` regardless of utterance

**Depends**: T-006

---

### T-029 · StoryPlannerService — apply_steering

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/services/story_planner.py` (extend)

**Description**:
Implement:

```python
async def apply_steering(
    arc: list[str],          # current 5-beat arc
    command: VoiceCommand,
    from_page: int
) -> list[str]               # updated arc; pages 1..from_page-1 unchanged
```

Single Gemini 2.5 Flash call. System prompt includes:
- Current beats for pages `from_page..5`
- The interpreted intent from `command.interpreted_intent`
- Content exclusions from the current `ContentPolicy`

Returns a new arc list with pages `from_page..5` updated; pages before `from_page` are copied unchanged.

**Done when**:
- Pages 1..from_page-1 in returned arc are identical to input arc
- Pages from_page..5 are modified to reflect the command intent (verified by keyword presence in mocked test)
- No ContentPolicy exclusion appears in any updated beat

**Depends**: T-006

---

### T-030 · Steering window + VoiceCommand flow

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)
- `voice-story-agent/backend/app/websocket/steering_handler.py` (new)

**Description**:
Implement `SteeringHandler`:

1. On `page_complete(N)`: emit `steering_window_open(N, 10000)` and start a 10 s asyncio timer
2. During the window, if a user turn arrives:
   a. Run safety check
   b. If safe: `classify_steering` → emit `voice_command_received` with `interpreted_as`
   c. If `ambiguous`: `speak("Different how — funnier, shorter, or something else?")` and await one clarifying turn; classify again
   d. If classified: call `StoryPlannerService.apply_steering` → `SessionStore.update_story_arc` → emit `voice_command_applied`; create + persist `VoiceCommand`; if `character_introduction` → `CharacterBibleService.add_secondary_character`
   e. Emit `steering_window_closed(reason="voice_command_applied")`
3. On 10 s timeout with no command: emit `steering_window_closed(reason="timeout")`
4. On timeout with only user_silent: emit `steering_window_closed(reason="user_silent")`

**Done when**:
- 10 s timeout fires `steering_window_closed(reason="timeout")` in integration test
- `classify_steering → tone_change` flow emits `voice_command_received` then `voice_command_applied`
- `ambiguous` command triggers one clarifying `speak` call before re-classification
- `character_introduction` adds the character to `CharacterBible.secondary_characters`
- `steering_window_closed` fires after `voice_command_applied` (not before)

**Depends**: T-028, T-029, T-019, T-026

---

### T-031 · Interrupt handling + voice_feedback client message

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)

**Description**:
1. `interrupt` client message: if a page is currently being narrated (page_audio_ready emitted but steering_window_open not yet emitted), set a cancellation flag that pauses the narration audio stream. Transition to the steering window flow immediately. Emit `steering_window_open`.
2. `voice_feedback` client message: wrap `raw_transcript` + `command_type` in a synthetic `VoiceTurn` and inject into the steering handler as if it came from voice. Used by non-audio clients and integration tests.

**Done when**:
- `interrupt` during narration immediately emits `steering_window_open`
- `voice_feedback` with a valid `command_type` triggers `voice_command_received` + `voice_command_applied`
- `voice_feedback` with an unsafe transcript triggers the safety flow

**Depends**: T-030

---

## Phase 8 — Session Memory (US7) *(stretch)*

> **Stretch goal**: complete after steering (Phase 7) works. These tasks enhance narrative
> coherence across pages but the demo works without them — the `page_history` list can be
> seeded from a simple in-process accumulator with minimal summary logic.

### T-032 · Page history accumulation

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/websocket/story_ws.py` (extend)
- `voice-story-agent/backend/app/services/session_store.py` (extend if needed)

**Description**:
After each `page_complete` event:
1. Take the first 25 words of the page text as the history entry for that page. No Gemini
   call is needed for this step — simple in-process string slicing is sufficient for the MVP.
   Example: `" ".join(page_text.split()[:25])` produces a usable coherence anchor.
2. Append the entry to an in-memory `page_history` list that persists for the session lifetime
3. Pass `page_history` to all subsequent `StoryPlannerService.expand_page` calls
4. Persist `page_history` as a field on the `Session` document for reconnect recovery

**Done when**:
- `page_history` has length N after page N completes
- `expand_page` call for page 3 receives `page_history` of length 2
- `GET /sessions/{id}` returns `story_arc` that includes updated arc after steering

**Depends**: T-026

---

### T-033 · Tone carry-forward in page generation

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/services/story_planner.py` (extend)
- `voice-story-agent/backend/app/services/character_bible_service.py` (extend)

**Description**:
When a `tone_change` `VoiceCommand` is accepted:
1. Call `CharacterBibleService` to update `StyleBible.mood` with the new tone description
2. Persist updated `StyleBible` via `SessionStore.save_style_bible`
3. The `expand_page` calls for all subsequent pages receive the updated `CharacterBible` (which includes the new `style_bible.mood`) — no special injection needed; the bible read from Firestore is always current

Verify clean-state on new session: `GET /sessions/{old_id}` characters and arc do not appear in any part of the new session's generation calls.

**Done when**:
- After a `tone_change` command, `get_style_bible().mood` reflects the new tone
- `expand_page` for page N+1 uses the updated `mood` in the Gemini prompt (verified via captured Gemini call args)
- A new session created after a prior session is complete has empty `page_history` and fresh `ContentPolicy`

**Depends**: T-032, T-030

---

## Phase 9 — Frontend

### T-034 · wsClient.ts — typed WebSocket client

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/lib/wsClient.ts`

**Description**:
Implement a typed WebSocket client class:
- Connects to `${NEXT_PUBLIC_WS_BASE_URL}/ws/story/{session_id}?token={token}`
- Sends binary frames (`sendAudio(pcm: ArrayBuffer)`) and JSON text frames (`send(msg: WsClientMessage)`)
- Receives: binary frames (agent audio, forwarded to a registered `onAudioChunk` callback) and text frames (routed by `type` to a `Map<string, EventHandler>` registry)
- Exposes `on(type: string, handler: (payload: unknown) => void)`
- Auto-reconnects on unintended disconnect (exponential backoff, max 5 retries); on reconnect, re-emits `session_start`
- All inbound and outbound message types are defined as TypeScript interfaces matching `contracts/api-spec.yaml` schemas

**Done when**:
- `wsClient.on("transcript", handler)` receives transcript events
- `wsClient.sendAudio(pcm)` sends a binary frame
- Disconnect simulation triggers reconnect with exponential backoff
- TypeScript compiles with no `any` types in public API

**Depends**: T-002

---

### T-035 · useVoiceSession hook

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/hooks/useVoiceSession.ts`

**Description**:
React hook that manages the full voice session lifecycle:
- Calls `POST /sessions` on mount to get `session_id` + `ws_url`
- Calls `POST /sessions/{id}/voice-session` to reserve ADK slot
- Connects `wsClient` and sends `session_start`
- Requests microphone permission; on permission granted, starts streaming `getUserMedia` PCM as `sendAudio` binary frames
- Exposes: `sessionId`, `sessionStatus`, `isListening`, `startSession()`, `stopSession()`
- Subscribes to `session_error` → sets error state; `story_complete` → updates status

**Done when**:
- `useVoiceSession()` in a test render triggers `POST /sessions` and WS connect
- `startSession()` requests mic permission and starts audio streaming
- `session_error` event sets the hook's error state

**Depends**: T-034

---

### T-036 · useStoryState hook

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/hooks/useStoryState.ts`

**Description**:
React hook that accumulates the complete story state:
- `pages`: `Map<number, PageState>` where `PageState = { text, imageUrl, audioUrl, illustrationFailed, audioFailed, status }`
- `captions`: ordered list of `{role, text}` objects
- `steeringWindowOpen`: boolean
- `steeringWindowPage`: number | null
- Subscribes to all page events (`page_text_ready`, `page_image_ready`, `page_audio_ready`, `page_asset_failed`, `page_complete`) and updates the `pages` map
- Subscribes to `transcript` events and updates `captions`
- Subscribes to `steering_window_open` / `steering_window_closed` to toggle `steeringWindowOpen`
- Exposes `hydrate(session: Session)` that pre-fills state from a `GET /sessions/{id}` response (for reconnect recovery)

**Done when**:
- Simulating `page_image_ready({ page: 1, image_url: "https://..." })` sets `pages.get(1).imageUrl`
- `page_asset_failed({ asset_type: "illustration" })` sets `pages.get(N).illustrationFailed = true`
- `hydrate(session)` populates pages from existing session data

**Depends**: T-034

---

### T-037 · VoiceButton component

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/components/VoiceButton.tsx`

**Description**:
Accessible mic toggle button:
- Idle: circular button with mic icon; click = `speak` / `interrupt`
- Listening: pulsing ring animation
- Steering window open: amber pulsing ring (child-friendly "you can speak" cue)
- Disabled: greyed out during page generation (not during steering window)
- ARIA: `role="button"`, `aria-label` changes with state, `aria-pressed`

Props: `isListening`, `steeringWindowOpen`, `isGenerating`, `onInterrupt`, `onFeedback`.

**Done when**:
- Renders in all 4 states without TypeScript errors
- Correct ARIA attributes present in each state
- `onInterrupt` is called when clicked during `isGenerating = true`
- Tailwind animation applies the pulsing ring when `steeringWindowOpen = true`

**Depends**: T-002

---

### T-038 · CaptionBar component

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/components/CaptionBar.tsx`

**Description**:
Scrolling caption strip at the bottom of the viewport:
- User bubbles (right-aligned, light blue) for `role = "user"` captions
- Agent bubbles (left-aligned, warm cream) for `role = "agent"` captions
- Safety rewrite display: when `safety_rewrite` event fires, shows a distinct amber card: "I can make it better! [proposed_rewrite]"
- Safety accepted: replaces amber card with a brief green confirmation before fading
- Auto-scrolls to the latest caption
- Partial transcripts (streamed words) update the last bubble in-place

Props: `captions`, `safetyRewrite`, `safetyAccepted`.

**Done when**:
- Renders user and agent bubbles with correct alignment
- Safety rewrite amber card appears when `safetyRewrite` is non-null
- Auto-scroll triggers when new caption is added
- Partial transcript updates (same `turn_id`, `is_final=false`) update existing bubble text

**Depends**: T-002

---

### T-039 · StoryPage component

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/components/StoryPage.tsx`

**Description**:
Renders one page of the storybook:
- Story text (visible after `page_text_ready`)
- Illustration: `<img>` with signed URL; placeholder (friendly "painting" SVG) if `illustrationFailed`
- Audio player: hidden `<audio autoPlay>` element that plays when `audioUrl` is set; text-only fallback if `audioFailed`
- Page number indicator (e.g., "Page 2 of 5")
- Fade-in animation for each asset as it arrives

Props: `page: PageState`, `pageNumber`, `totalPages`.

**Done when**:
- Renders correctly with text only (no image, no audio)
- Illustration placeholder appears when `illustrationFailed = true`
- Audio element has `autoPlay` and `src = audioUrl` when audio is ready
- Text fades in when `page.text` changes from null to a string

**Depends**: T-002

---

### T-040 · HoldAnimation + StoryBook components

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/components/HoldAnimation.tsx`
- `voice-story-agent/frontend/src/components/StoryBook.tsx`

**Description**:
`HoldAnimation`:
- Gentle looping animation (soft bouncing dots or floating sparkles using CSS/Tailwind keyframes)
- Shown when `page_generating` is emitted; hidden when `page_complete` fires
- MUST remain visible at all times during generation (never blank)

`StoryBook`:
- Page carousel (horizontal scroll snapping or single-page view)
- Renders one `StoryPage` per delivered page in order
- Shows `HoldAnimation` as the "next page" placeholder while generating
- Shows a closing card ("The End! What a great adventure.") after `story_complete`
- Subtle "you can speak" indicator badge when `steeringWindowOpen = true`

**Done when**:
- `HoldAnimation` is visible between `page_generating` and `page_complete`
- `StoryBook` renders 0–5 `StoryPage` components based on `useStoryState.pages`
- Closing card appears after `story_complete`
- Steering indicator appears/disappears with `steeringWindowOpen`

**Depends**: T-039, T-036

---

### T-041 · Story page assembly (app/story/page.tsx)

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/src/app/story/page.tsx`

**Description**:
Assemble the main story page using all hooks and components:

```tsx
export default function StoryPage() {
  const voice = useVoiceSession()
  const story = useStoryState(/* subscribe to wsClient events */)

  return (
    <main className="h-screen flex flex-col">
      <StoryBook pages={story.pages} steeringWindowOpen={story.steeringWindowOpen} />
      <CaptionBar captions={story.captions} safetyRewrite={...} />
      <VoiceButton isListening={voice.isListening} ... />
    </main>
  )
}
```

Wire reconnect recovery: on `voice.reconnected`, call `GET /sessions/{id}` and `story.hydrate(session)`.

**Done when**:
- `npm run build` succeeds with no TypeScript errors
- Page renders without runtime errors in `npm run dev`
- Reconnect recovery calls `GET /sessions/{id}` and hydrates story state

**Depends**: T-035, T-036, T-037, T-038, T-039, T-040

---

## Phase 10 — Deploy + Observability

### T-042 · Cloud Run backend deploy config

**Priority**: P1
**Files**:
- `voice-story-agent/backend/Dockerfile` (finalise)
- `voice-story-agent/infra/cloud-run-deploy.sh`
- `voice-story-agent/.github/workflows/deploy-backend.yml` (optional CI)

**Description**:
Finalise multi-stage Dockerfile. Write `cloud-run-deploy.sh`:
1. `docker build -t gcr.io/{PROJECT}/voice-story-backend:latest .`
2. `docker push gcr.io/{PROJECT}/voice-story-backend:latest`
3. `gcloud run deploy voice-story-backend --image ... --region us-central1 --allow-unauthenticated --service-account voice-story-agent-sa@...`
4. Print the deployed service URL

Set Cloud Run env vars: all config values from `config.py`.

**Done when**:
- `./cloud-run-deploy.sh` deploys the service to Cloud Run
- `GET https://{cloud-run-url}/health` returns `{"status": "ok"}`
- WebSocket connection to `wss://{cloud-run-url}/ws/story/{id}` succeeds

**Depends**: T-001

---

### T-043 · Firebase App Hosting frontend deploy config

**Priority**: P1
**Files**:
- `voice-story-agent/frontend/apphosting.yaml`
- `voice-story-agent/frontend/.firebaserc`
- `voice-story-agent/infra/firebase-deploy.sh`

**Description**:
Configure Firebase App Hosting for Next.js (primary target — handles SSR natively):
- Add `apphosting.yaml` to `frontend/` with `NEXT_PUBLIC_API_BASE_URL` and
  `NEXT_PUBLIC_WS_BASE_URL` environment variables pointing to the Cloud Run service URL
- Run `firebase init apphosting` to link the project and connect the Git branch
- Write `firebase-deploy.sh` that pushes to the linked branch (App Hosting triggers
  an automatic build and deploy on push)
- Do NOT set `output: "export"` in `next.config.ts`; App Hosting handles Next.js builds natively

**Done when**:
- Pushing to the connected branch triggers a Firebase App Hosting build and deploy
- `https://{project}.web.app/story` loads the story page from the App Hosting deployment
- WebSocket connects to the Cloud Run backend from the Firebase App Hosting URL

**Depends**: T-002, T-042

---

### T-044 · Cloud Logging structured events *(stretch)*

**Priority**: P2
**Files**:
- `voice-story-agent/backend/app/logging_config.py`
- `voice-story-agent/backend/app/websocket/story_ws.py` (instrument)
- `voice-story-agent/backend/app/services/safety_service.py` (instrument)
- `voice-story-agent/backend/app/websocket/page_orchestrator.py` (instrument)

**Description**:
Configure Python `logging` with a JSON formatter compatible with Cloud Logging structured logs:

Each log record includes: `session_id`, `event_type`, `severity`, `timestamp`.

Key instrumentation points:
- Session created / status changed
- SafetyDecision triggered (category only; NOT `raw_input`)
- Page generation started / completed / asset failed
- VoiceCommand received / applied
- Gemini API call latency
- WebSocket connect / disconnect

**Done when**:
- `gcloud logging read "resource.type=cloud_run_revision" --limit=20` shows structured JSON logs with `session_id`
- `SafetyDecision` logs do NOT contain `raw_input` text
- Latency logs appear for Gemini calls

**Depends**: T-042

---

### T-045 · WebSocket reconnect recovery *(stretch)*

**Priority**: P2
**Files**:
- `voice-story-agent/frontend/src/hooks/useVoiceSession.ts` (extend)
- `voice-story-agent/frontend/src/hooks/useStoryState.ts` (extend)

**Description**:
On WS reconnect:
1. `useVoiceSession` detects disconnect (close event)
2. Exponential backoff reconnect (1 s, 2 s, 4 s, 8 s, 16 s; give up after 5 attempts → show error)
3. On reconnect: re-send `session_start`
4. After `voice_session_ready`: call `GET /sessions/{id}` REST endpoint
5. Call `story.hydrate(session)` to restore all page states, captions, and character bible

**Done when**:
- Simulated WS close triggers reconnect within 1 s
- After reconnect, pages already delivered are still visible (hydrated from REST)
- After 5 failed reconnect attempts, an error state is shown

**Depends**: T-041

---

## Task Summary

| Phase | Tasks | Test Tasks | P1 (MVP) | P2 (stretch) |
|-------|-------|-----------|----------|--------------|
| 0 — Scaffold | T-001..T-003 | — | 3 | — |
| 1 — Data layer | T-004..T-010 | — | 7 | — |
| 2 — WS foundation | T-011..T-015 | — | 5 | — |
| 3 — Safety | T-016..T-017 | TEST-S01..S03 | 4 | — |
| 4 — Setup flow | T-018..T-021 | — | 4 | — |
| 5 — Page generation | T-022..T-026 | TEST-P01..P03 | 7 | — |
| 6 — Char. consistency | T-027 | TEST-C01..C03 | 4 | — |
| 7 — Steering *(stretch)* | T-028..T-031 | — | — | 4 |
| 8 — Session memory *(stretch)* | T-032..T-033 | — | — | 2 |
| 9 — Frontend | T-034..T-041 | — | 8 | — |
| 10 — Deploy (MVP) | T-042..T-043 | — | 2 | — |
| 10 — Observability *(stretch)* | T-044..T-045 | — | — | 2 |
| **Total** | **42** | **6** | **44** | **8** |

**MVP task count**: T-001–T-027 (27 tasks) + T-034–T-043 (10 tasks) + 6 test tasks = **43 tasks**
**Stretch task count**: T-028–T-033 (6 tasks) + T-044–T-045 (2 tasks) = **8 tasks**

### Explicit test tracks

| Track | Tasks | Covers |
|-------|-------|--------|
| Safety transformation | TEST-S01, TEST-S02, TEST-S03 | Every forbidden category fires; fail-safe fallback on classifier error; rewrites contain no unsafe leakage; `SafetyDecision` + `ContentPolicy` persistence |
| Character consistency metadata | TEST-C01, TEST-C02, TEST-C03 | `build_image_prompt` reference URL rules (page 1 vs 2–5, secondary chars); reference propagation across service boundary; full bible round-trip |
| Page streaming | TEST-P01, TEST-P02, TEST-P03 | Event order correctness; asset failure isolation; word count + content policy validation |

### Suggested MVP execution order

```
T-001 → T-002 → T-003
T-004 → T-005 → T-006 → T-007 → T-008 → T-009 → T-010
T-011 → T-012 → T-013 → T-014 → T-015
T-016 → TEST-S01 → TEST-S02 → T-017 → TEST-S03
T-018 → T-019 → T-020 → T-021
T-022 → T-023 → T-024 → T-025 → TEST-P01 → TEST-P02 → TEST-P03 → T-026
T-027 → TEST-C01 → TEST-C02 → TEST-C03
T-034 → T-035 → T-036 → T-037 → T-038 → T-039 → T-040 → T-041
T-042 → T-043

--- stretch (if time permits) ---
T-028 → T-029 → T-030 → T-031
T-032 → T-033
T-044 → T-045
```

---

## Refinement Notes

### What changed

**1. MVP Cut Line added** (new section near the top)
- T-001–T-027 and T-034–T-043 are the minimum shippable set. The full voice setup, 5-page
  generation, character consistency, and safety rewriting are all within this boundary.
- T-028–T-033 (steering + session memory) and T-044–T-045 (logging + reconnect) are stretch
  goals. Phase 7 and Phase 8 now carry a stretch callout banner.

**2. Safety fail-safe behavior corrected (constitution alignment)**
- **T-016**: Replaced the `fail-open` behavior (`return SafetyResult(safe=True)` on exception)
  with a `fail-safe` behavior. On any classifier exception or malformed response, the service
  now returns `SafetyResult(safe=False, rewrite=SAFE_FALLBACK_REWRITE)`. The original user
  utterance is never passed to generation when the classifier is unavailable.
- **T-006**: Added `SAFE_FALLBACK_REWRITE` as a module-level constant in `app.models.safety`
  so the fallback is defined in one place and importable by both the service and tests.
- **T-017**: Updated "Done when" to assert that a mocked classifier failure emits
  `safety_rewrite` with the fallback rewrite rather than passing the original utterance through.
- **TEST-S01**: Added two new test cases (exception and malformed response) that assert
  `safe=False` and `rewrite == SAFE_FALLBACK_REWRITE`. Total test cases increased from 10 to 12.

**3. Safety acknowledgement loop simplified (T-017)**
- Removed the re-evaluation step ("if still unsafe, repeat once"). For MVP, any user response
  after the agent speaks the rewrite is treated as acceptance. The agent's proposed_rewrite is
  already safe; re-evaluating the child's acknowledgement adds orchestration complexity for
  marginal safety benefit at hackathon scale.

**4. GCP setup brittleness reduced (T-003)**
- Removed the guarantee that `./setup.sh` "runs to completion without errors" as a "Done when"
  criterion. GCP resource creation commands are not fully idempotent (Firestore, bucket) and
  an already-configured project will emit warnings. Reframed as "best-effort safe re-run"
  with `|| true` guards and a README documenting manual intervention steps. The "Done when"
  now tests against a fresh project and explicitly permits warnings on re-runs.

**5. ADK internal coupling removed (T-013, T-014)**
- **T-013**: Removed the direct reference to `ADK LiveSession` as a class name. Replaced with
  behavioral language: "opens a bidi-streaming session with the Gemini Live model via the ADK
  SDK." Added a "Done when" criterion that the implementation must not reference ADK private
  classes (`_` prefix).
- **T-014**: Replaced "returns only after the agent has finished speaking (audio complete event
  received)" with behavioral language: "awaits confirmation that the agent audio response is
  complete before returning. The exact completion signal is SDK-version-dependent; wrap in a
  10 s timeout." Added a `VoiceSessionError` timeout test to "Done when."

**6. Page history Gemini call eliminated (T-032)**
- Replaced the Gemini Flash summarisation call (one LLM call per page for history) with simple
  in-process string truncation: first 25 words of the page text. This removes a latency and
  cost risk from the page delivery loop. The "Done when" criteria and behavior are identical
  from the user's perspective.

### Tasks now considered stretch

| Task(s) | Reason |
|---------|--------|
| T-028–T-031 | Mid-story steering enriches the demo but the primary value proposition (voice → 5-page illustrated story) works without it |
| T-032–T-033 | Session memory / tone carry-forward improves coherence; basic `page_history` accumulation (first 25 words) is already in T-026's loop |
| T-044 | Cloud Logging is useful for post-demo debugging but not required for the live demo |
| T-045 | Reconnect recovery is a reliability feature; losing a session mid-demo is an edge case the hackathon setting can tolerate |

### Where safety behavior was corrected

| Location | Old behavior | New behavior |
|----------|-------------|-------------|
| T-016 "On exception" | `return SafetyResult(safe=True)` — passes original utterance through | `return SafetyResult(safe=False, rewrite=SAFE_FALLBACK_REWRITE)` — blocks original utterance |
| T-006 | No fallback constant defined | `SAFE_FALLBACK_REWRITE` constant defined in `app.models.safety` |
| T-017 "Done when" | No assertion on classifier failure path | Asserts mocked classifier failure emits `safety_rewrite` with fallback; original utterance not surfaced |
| TEST-S01 | 10 test cases; exception → `safe=True` | 12 test cases; exception and malformed response → `safe=False, rewrite=SAFE_FALLBACK_REWRITE` |

The corrected behavior aligns directly with the constitution's **Child Safety First
(Non-Negotiable)** principle: "Safety middleware MUST execute before every Gemini generation
call; unsafe output MUST fail closed into safe rewrite." Failing open (passing an
unclassified utterance through) violated this principle. The system now fails closed in all
classifier error states.
