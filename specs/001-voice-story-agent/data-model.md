# Data Model: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12
**Storage**: Firestore (Native mode) for all documents; Cloud Storage for binary assets.

---

## Entity Overview

```
Session
  ├── UserTurn[]               (0:many — every voice exchange in the session)
  ├── StoryBrief               (1:1 — confirmed story parameters)
  ├── CharacterBible           (1:1)
  │     ├── ProtagonistProfile (embedded)
  │     ├── StyleBible         (1:1 sub-document — extracted for clarity)
  │     ├── ContentPolicy      (embedded)
  │     └── CharacterRef[]     (embedded array — secondary characters)
  ├── Page[]                   (1:5)
  │     └── PageAsset[]        (0:2 per page — illustration + narration)
  ├── VoiceCommand[]           (0:many — mid-story steering inputs)
  └── SafetyDecision[]         (0:many — one per detected unsafe utterance)
```

### Cross-cutting relationships

| From | To | Cardinality | Key |
|------|----|-------------|-----|
| `UserTurn` | `VoiceCommand` | 0..1 | `UserTurn.voice_command_id` |
| `UserTurn` | `SafetyDecision` | 0..1 | `UserTurn.safety_decision_id` |
| `VoiceCommand` | `SafetyDecision` | 0..1 | `VoiceCommand.safety_decision_id` |
| `VoiceCommand` | `CharacterRef` | 0..1 | `VoiceCommand.new_character_ref_id` |
| `SafetyDecision` | `ContentPolicy` | 0..1 | `SafetyDecision.exclusion_added` written into `ContentPolicy.exclusions` |
| `Page` | `VoiceCommand[]` | 0:many | `Page.steering_applied` (list of command IDs) |
| `Page` | `PageAsset[]` | 0..2 | `PageAsset.page_number` |

---

## 1. Session

**Firestore path**: `sessions/{session_id}`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string (UUID4) | yes | Partition key; generated at session creation |
| `status` | enum | yes | `setup` → `generating` → `complete` → `error` |
| `created_at` | timestamp | yes | UTC ISO-8601 |
| `updated_at` | timestamp | yes | Updated on every state transition |
| `page_count` | integer | yes | Always 5 for MVP; tracks pages generated so far |
| `current_page` | integer | yes | 0 during setup; 1–5 during generation; 5 when complete |
| `story_arc` | string[] | yes | 5-element array of page beat summaries from Gemini Pro outline |
| `error_message` | string | no | Set only when `status = error` |

**Lifecycle state transitions**:
```
setup ──(StoryBrief confirmed)──► generating ──(all 5 pages complete)──► complete
  │                                   │
  └──(unrecoverable error)────────────┴──(unrecoverable error)──► error
```

Transition triggers:
- `setup → generating`: agent confirms the StoryBrief aloud and page 1 generation begins.
- `generating → complete`: `current_page` reaches 5 and all PageAssets are in a terminal state (`ready` or `failed`).
- `* → error`: any unrecoverable backend exception, or a `SafetyDecision` with `user_accepted = false` during setup phase.

**Validation rules**:
- `session_id` MUST be a valid UUID4.
- `page_count` MUST equal 5 (enforced at model level; flexible for future extension).
- `story_arc` MUST contain exactly 5 non-empty strings before `status` transitions to `generating`.

---

## 2. UserTurn

**Firestore path**: `sessions/{session_id}/turns/{turn_id}`

Represents every individual voice exchange (user utterance or agent response) within the session. Provides a complete, ordered conversation log for debugging, safety auditing, and replay.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `turn_id` | string (UUID4) | yes | Unique ID; monotonically orderable via `sequence` |
| `sequence` | integer | yes | 1-based turn counter within the session; enforces ordering |
| `phase` | enum | yes | `setup` \| `steering` \| `narration` |
| `speaker` | enum | yes | `user` \| `agent` |
| `raw_transcript` | string | yes | Verbatim transcript of this utterance (from Gemini Live ASR or agent text) |
| `caption_text` | string | yes | Text rendered to the on-screen caption strip (may be cleaned/truncated from `raw_transcript`) |
| `voice_command_id` | string (UUID4) | no | Set if this user turn produced a `VoiceCommand` |
| `safety_decision_id` | string (UUID4) | no | Set if this user turn triggered a `SafetyDecision` |
| `page_context` | integer | no | Page number being narrated or awaited when this turn occurred (null during setup) |
| `timestamp` | timestamp | yes | UTC; when this utterance began |

**Lifecycle state transitions**:
```
received ──(ASR complete)──► transcribed ──(routing complete)──► processed
```

Turn routing outcomes (mutually exclusive):
- Routed to **setup parameter extraction** → updates `StoryBrief`
- Routed to **safety check** → creates `SafetyDecision`; sets `safety_decision_id`
- Routed to **steering parser** → creates `VoiceCommand`; sets `voice_command_id`
- Routed to **narration acknowledgment** → no child entity; advances page flow

**Validation rules**:
- `sequence` values within a session MUST be unique and contiguous starting at 1.
- `speaker = user` turns MUST always have a corresponding subsequent `speaker = agent` turn (the agent's reply), except for the final user utterance if the session ends abruptly.
- `caption_text` MUST NOT contain any string from a `SafetyDecision.raw_input` field.

---

## 3. StoryBrief

**Firestore path**: `sessions/{session_id}/story_brief/main`

The confirmed, agent-validated set of story parameters assembled during setup. Replaces `StoryPreferences` (prior name) to reflect that this is the compiled brief used as the authoritative input to generation — not just a raw preferences bag.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `protagonist_name` | string | yes | Name given to the main character during setup |
| `protagonist_description` | string | yes | Free-text description (color, species, appearance) |
| `setting` | string | yes | Story world/location (e.g., "a mushroom forest") |
| `tone` | enum | yes | `silly` \| `sleepy` \| `adventurous` \| `warm` \| `curious` |
| `additional_constraints` | string[] | no | Extra user-provided constraints not captured by the above fields |
| `raw_setup_transcript` | string | yes | Full verbatim transcript of the setup conversation (audit/debug only; never surfaced in UI) |
| `confirmed_at` | timestamp | yes | UTC; when the agent read back the one-sentence summary and the user acknowledged |
| `confirmed_by_agent` | boolean | yes | True once the agent has voiced the summary confirmation and generation may begin |

**Lifecycle state transitions**:
```
draft ──(all 3 params present)──► ready_to_confirm
  └──(agent voices summary & user acknowledges)──► confirmed
```

`Session.status` MUST NOT transition from `setup → generating` until `StoryBrief.confirmed_by_agent = true`.

**Validation rules**:
- `protagonist_name` max 80 characters.
- `setting` max 200 characters.
- `tone` MUST be one of the five enum values; if user provides an unmapped tone (e.g., "funny"), the agent maps it to the nearest value (`silly`) before storing.
- `raw_setup_transcript` stored for testing safety rewrite verification only; MUST NOT appear in any UI response.

---

## 4. CharacterBible

**Firestore path**: `sessions/{session_id}/character_bible/main`

### 4a. ProtagonistProfile (embedded)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Mirrors `StoryBrief.protagonist_name` |
| `species_or_type` | string | yes | e.g., "purple monster", "small rabbit", "young girl" |
| `color` | string | yes | Primary color (e.g., "bright purple") |
| `attire` | string | no | Clothing or accessories (e.g., "red scarf") |
| `notable_traits` | string[] | yes | 2–4 visual traits (e.g., `["big round eyes", "stumpy legs"]`) |
| `reference_image_gcs_uri` | string (GCS URI) | no | Null until page 1 `PageAsset` (illustration) is `ready`; used as visual anchor for pages 2–5 |

### 4b. StyleBible (1:1 sub-document — see Section 5)

`CharacterBible` holds a `style_bible` map field that mirrors the top-level `StyleBible` sub-document. Both are written atomically when the CharacterBible is first created.

### 4c. ContentPolicy (embedded)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `exclusions` | string[] | yes | Active content exclusion constraints (e.g., `["no destruction", "no gore", "no fear escalation"]`). Pre-populated with base policy; extended when a `SafetyDecision` is accepted. |
| `derived_from_safety_decisions` | string[] | no | List of `SafetyDecision.decision_id` values that contributed exclusion constraints |

### 4d. CharacterRef[] (embedded array — secondary characters)

One entry per character introduced via a `VoiceCommand` of type `character_introduction`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `char_id` | string | yes | Slug (e.g., `"yellow_bird"`) |
| `name` | string | yes | Character name as spoken by user |
| `description` | string | yes | Visual description used in image prompts |
| `reference_image_gcs_uri` | string (GCS URI) | no | Null until first-appearance `PageAsset` (illustration) is `ready` |
| `introduced_on_page` | integer | yes | Page number when this character first appeared |
| `voice_command_id` | string (UUID4) | yes | ID of the `VoiceCommand` that introduced this character |

---

## 5. StyleBible

**Firestore path**: `sessions/{session_id}/style_bible/main`

Extracted from the `CharacterBible` embedding so that style parameters can be read and updated independently — for example, when a tone-change `VoiceCommand` updates `mood` without touching protagonist visuals. The `CharacterBible.style_bible` embedded map is always kept in sync with this document via a Firestore batch write.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `art_style` | string | yes | e.g., "soft colorful picture book illustration" |
| `color_palette` | string | yes | e.g., "pastel purples, warm yellows, soft greens" |
| `mood` | string | yes | e.g., "warm, gentle, playful" — updated by tone-change `VoiceCommand` |
| `negative_style_terms` | string[] | yes | Terms excluded from every image prompt (e.g., `["realistic", "dark", "scary"]`) |
| `last_updated_by_command_id` | string (UUID4) | no | ID of the `VoiceCommand` that last mutated `mood`; null if never changed |

**Lifecycle state transitions**:
```
draft ──(CharacterBible created)──► active
  └──(tone-change VoiceCommand applied)──► active  [mood field updated in-place]
```

`mood` is the only field that changes after initial creation; all other fields are set once at `CharacterBible` creation time.

---

## 6. Page

**Firestore path**: `sessions/{session_id}/pages/{page_number}`
`page_number` is a string `"1"` through `"5"`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `page_number` | integer | yes | 1–5 |
| `status` | enum | yes | `pending` → `text_ready` → `assets_generating` → `complete` → `error` |
| `beat` | string | yes | Page beat from `Session.story_arc` used to drive generation |
| `text` | string | no | Generated story text (60–120 words); set when `status ≥ text_ready` |
| `narration_script` | string | no | TTS-optimised version of `text`; may differ from display text |
| `illustration_failed` | boolean | yes | Default `false`; `true` if all illustration `PageAsset` generation attempts failed |
| `audio_failed` | boolean | yes | Default `false`; `true` if all narration `PageAsset` generation attempts failed |
| `steering_applied` | string[] | no | IDs of `VoiceCommand` documents that influenced this page's generation |
| `generated_at` | timestamp | no | Set when `status = complete` |

**Lifecycle state transitions**:
```
pending
  └──(text generated)──► text_ready
       └──(PageAsset generation started)──► assets_generating
            └──(both PageAssets reach terminal state)──► complete
pending / text_ready / assets_generating ──(unrecoverable text error)──► error
```

Asset failures do NOT set `Page.status = error`; they set `illustration_failed` or `audio_failed` and `Page.status` still advances to `complete` so the session continues.

**Validation rules**:
- `text` MUST pass content policy check before being stored (`ContentPolicy.exclusions` applied at generation time).
- `text` word count MUST be 60–120 words; values outside this range are treated as generation failures.

---

## 7. PageAsset

**Firestore path**: `sessions/{session_id}/pages/{page_number}/assets/{asset_type}`
`asset_type` is `"illustration"` or `"narration"`.

Extracted from `Page` so that each binary asset has its own lifecycle, retry state, and signed URL independently of the page's text state.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `asset_id` | string (UUID4) | yes | Unique ID |
| `page_number` | integer | yes | 1–5 |
| `asset_type` | enum | yes | `illustration` \| `narration` |
| `generation_status` | enum | yes | `pending` → `generating` → `ready` \| `failed` |
| `gcs_uri` | string | no | `gs://{bucket}/sessions/{session_id}/pages/{page_number}/...`; set when `generation_status = ready` |
| `signed_url` | string | no | Short-lived (1 hour) HTTPS read URL; generated after `gcs_uri` is set; sent to frontend via WebSocket event |
| `signed_url_expires_at` | timestamp | no | UTC expiry of `signed_url` |
| `error_detail` | string | no | Set when `generation_status = failed`; contains the Imagen/TTS error code/message |
| `generated_at` | timestamp | no | UTC; when `generation_status` transitioned to `ready` or `failed` |

**Lifecycle state transitions**:
```
pending ──(generation call dispatched)──► generating
  └──(API success + GCS write)──► ready
  └──(API error or GCS write failure)──► failed
```

On transition to `ready`:
- `gcs_uri` is set.
- A signed URL is generated and stored in `signed_url`.
- The corresponding WebSocket event (`page_image_ready` or `page_audio_ready`) is emitted to the frontend.
- If `asset_type = illustration` and `page_number = 1`, `CharacterBible.ProtagonistProfile.reference_image_gcs_uri` is set to `gcs_uri`.

On transition to `failed`:
- `Page.illustration_failed` or `Page.audio_failed` is set to `true`.
- The parent `Page` continues to `complete` status; the session does NOT terminate.

**Validation rules**:
- `gcs_uri` MUST match the pattern `gs://{bucket}/sessions/{session_id}/pages/{page_number}/{asset_type}.*`.
- `signed_url` MUST use HTTPS and be generated with a 1-hour TTL.
- Once `generation_status` is `ready` or `failed` it MUST NOT be changed (terminal states).

---

## 8. SafetyDecision

**Firestore path**: `sessions/{session_id}/safety_decisions/{decision_id}`

Renamed from `SafetyEvent` to emphasise that this is an active decision record — the system decided how to handle an unsafe utterance — not merely a passive log event.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `decision_id` | string (UUID4) | yes | Unique ID |
| `turn_id` | string (UUID4) | yes | ID of the `UserTurn` that triggered this decision |
| `phase` | enum | yes | `setup` \| `steering` |
| `raw_input` | string | yes | Original unsafe utterance; stored for audit only; MUST NOT appear in any UI channel |
| `detected_category` | enum | yes | `physical_harm` \| `character_death` \| `gore` \| `destruction` \| `sexual_content` \| `fear_escalation` |
| `proposed_rewrite` | string | yes | Child-safe alternative voiced to the user |
| `user_accepted` | boolean | yes | `true` if the user acknowledged or redirected; `false` if the session was abandoned |
| `final_premise` | string | no | The premise used for generation after acknowledgment; may include user-added detail beyond `proposed_rewrite` |
| `exclusion_added` | string | no | The content exclusion string added to `ContentPolicy.exclusions` upon acceptance |
| `triggered_at` | timestamp | yes | UTC |

**Lifecycle state transitions**:
```
detected ──(agent voices proposed_rewrite)──► awaiting_acknowledgment
  └──(user accepts or redirects)──► accepted   [final_premise set; exclusion written to ContentPolicy]
  └──(user abandons session)──► rejected       [Session.status → error if phase = setup]
```

**Validation rules**:
- `raw_input` MUST be stored for audit but MUST NOT appear in any UI response, caption, illustration prompt, or narration script.
- `user_accepted = false` during `phase = setup` MUST set `Session.status = error`.
- `exclusion_added` MUST be appended to `CharacterBible.ContentPolicy.exclusions` and `derived_from_safety_decisions` atomically within the same Firestore transaction.

---

## 9. VoiceCommand

**Firestore path**: `sessions/{session_id}/voice_commands/{command_id}`

Renamed from `SteeringCommand` to make the entity name reflect its origin (a voice utterance) rather than its effect (steering), enabling it to encompass setup corrections and ambiguous turns that were resolved before steering was applied.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command_id` | string (UUID4) | yes | Unique ID |
| `turn_id` | string (UUID4) | yes | ID of the `UserTurn` that produced this command |
| `raw_transcript` | string | yes | Verbatim voice input |
| `interpreted_intent` | string | yes | Agent's semantic interpretation (e.g., "add yellow bird companion from page 3 onward") |
| `command_type` | enum | yes | `tone_change` \| `pacing_change` \| `element_reintroduction` \| `character_introduction` |
| `applied_to_pages` | integer[] | yes | Page numbers not yet generated when the command was received |
| `new_character_ref_id` | string | no | Set if `command_type = character_introduction`; references `CharacterRef.char_id` |
| `safe` | boolean | yes | `true` if the command passed the safety check; `false` if it was rewritten |
| `safety_decision_id` | string (UUID4) | no | Set if `safe = false`; references `SafetyDecision.decision_id` |
| `received_at` | timestamp | yes | UTC |

**Lifecycle state transitions**:
```
received
  └──(safety check passes)──► safe_pending_application
  └──(safety check fails)──► rewrite_proposed ──(user accepts)──► safe_pending_application
                                               └──(user rejects)──► abandoned
safe_pending_application ──(applied to all target pages)──► applied
```

**Validation rules**:
- `applied_to_pages` MUST contain only page numbers greater than `Session.current_page` at the time the command was received; already-generated pages MUST NOT be listed.
- If `command_type = character_introduction`, `new_character_ref_id` MUST be set and a corresponding `CharacterRef` entry MUST be written to `CharacterBible` in the same Firestore batch.
- If `safe = false`, `safety_decision_id` MUST be set; the command MUST NOT influence any page until `SafetyDecision.user_accepted = true`.

---

## Cloud Storage Asset Layout

```
gs://{gcp_project}-story-assets/
└── sessions/
    └── {session_id}/
        ├── pages/
        │   ├── 1/
        │   │   ├── illustration.png     ← Imagen output; also protagonist reference image
        │   │   └── narration.mp3        ← Cloud Text-to-Speech output
        │   ├── 2/
        │   │   ├── illustration.png
        │   │   └── narration.mp3
        │   ├── 3/ … 4/ … 5/            ← same structure
        └── characters/
            ├── protagonist_ref.png      ← copy of pages/1/illustration.png for reference use
            └── {char_id}_ref.png        ← first-appearance illustration for steering-introduced characters
```

**Access pattern**: Backend generates short-lived (1 hour) signed read URLs immediately after each asset is written. Signed URLs are written to `PageAsset.signed_url` and emitted to the frontend via the WebSocket event stream (`page_image_ready`, `page_audio_ready`). Frontend never holds long-lived GCS credentials.

---

## Firestore Index Requirements

| Collection | Fields indexed | Query purpose |
|------------|---------------|---------------|
| `sessions` | `status ASC`, `created_at DESC` | Admin/debug listing of recent sessions |
| `sessions/{id}/turns` | `sequence ASC` | Ordered conversation replay |
| `sessions/{id}/pages` | `page_number ASC` | Ordered page retrieval |
| `sessions/{id}/pages/{n}/assets` | `asset_type ASC` | Retrieve both assets for a page |
| `sessions/{id}/voice_commands` | `received_at ASC` | Chronological command history |
| `sessions/{id}/safety_decisions` | `triggered_at ASC` | Safety audit trail |

All other queries are point lookups by document ID and require no composite index.
