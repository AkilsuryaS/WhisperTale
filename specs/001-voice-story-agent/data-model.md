# Data Model: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12
**Storage**: Firestore (Native mode) for all documents; Cloud Storage for binary assets.

---

## Entity Overview

```
StorySession
  ├── StoryPreferences         (1:1)
  ├── CharacterBible           (1:1)
  │     ├── ProtagonistProfile (embedded)
  │     ├── StyleBible         (embedded)
  │     ├── ContentPolicy      (embedded)
  │     └── CharacterRef[]     (embedded array — secondary characters)
  ├── StoryPage[]              (1:5)
  ├── SteeringCommand[]        (0:many)
  └── SafetyEvent[]            (0:many)
```

---

## 1. StorySession

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
| `error_message` | string | no | Set only when status = `error` |

**State transitions**:
```
setup ──(setup_complete event)──► generating ──(all 5 pages complete)──► complete
  │                                   │
  └──(unrecoverable error)────────────┴──(unrecoverable error)──► error
```

**Validation rules**:
- `session_id` MUST be a valid UUID4.
- `page_count` MUST equal 5 (enforced at model level; flexible for future extension).
- `story_arc` MUST contain exactly 5 non-empty strings before generation begins.

---

## 2. StoryPreferences

**Firestore path**: `sessions/{session_id}/preferences/main`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `protagonist_name` | string | yes | Name given to the main character during setup |
| `protagonist_description` | string | yes | Free-text description (color, species, appearance) |
| `setting` | string | yes | Story world/location (e.g., "a mushroom forest") |
| `tone` | enum | yes | `silly` \| `sleepy` \| `adventurous` \| `warm` \| `curious` |
| `additional_constraints` | string[] | no | Any extra user-provided constraints not covered above |
| `raw_setup_transcript` | string | yes | Full verbatim transcript of setup conversation (for debugging) |

**Validation rules**:
- `protagonist_name` max 80 characters.
- `setting` max 200 characters.
- `raw_setup_transcript` stored for testing safety rewrite verification only; never
  surfaced in UI.

---

## 3. CharacterBible

**Firestore path**: `sessions/{session_id}/character_bible/main`

### 3a. ProtagonistProfile (embedded)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Mirrors StoryPreferences.protagonist_name |
| `species_or_type` | string | yes | e.g., "purple monster", "small rabbit", "young girl" |
| `color` | string | yes | Primary color (e.g., "bright purple") |
| `attire` | string | no | Clothing or accessories (e.g., "red scarf") |
| `notable_traits` | string[] | yes | 2–4 visual traits (e.g., ["big round eyes", "stumpy legs"]) |
| `reference_image_url` | string (GCS URL) | no | Null until page 1 image generated; used for pages 2–5 |

### 3b. StyleBible (embedded)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `art_style` | string | yes | e.g., "soft colorful picture book illustration" |
| `color_palette` | string | yes | e.g., "pastel purples, warm yellows, soft greens" |
| `mood` | string | yes | e.g., "warm, gentle, playful" |
| `negative_style_terms` | string[] | yes | Terms to exclude from every image prompt (e.g., ["realistic", "dark", "scary"]) |

### 3c. ContentPolicy (embedded)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `exclusions` | string[] | yes | Active content exclusion constraints (e.g., ["no destruction", "no gore", "no fear escalation"]). Pre-populated with base policy; extended by SafetyEvents. |
| `derived_from_safety_events` | string[] | no | List of safety_event_ids that contributed constraints |

### 3d. CharacterRef[] (embedded array — secondary characters)

One entry per character introduced via steering commands.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `char_id` | string | yes | Slug (e.g., "yellow_bird") |
| `name` | string | yes | Character name as spoken by user |
| `description` | string | yes | Visual description for image prompts |
| `reference_image_url` | string (GCS URL) | no | Null until first page featuring this character |
| `introduced_on_page` | integer | yes | Page number when character first appeared |

---

## 4. StoryPage

**Firestore path**: `sessions/{session_id}/pages/{page_number}`
`page_number` is a string "1" through "5".

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `page_number` | integer | yes | 1–5 |
| `status` | enum | yes | `pending` → `text_ready` → `image_ready` → `audio_ready` → `complete` → `error` |
| `beat` | string | yes | Page beat from story arc outline used to generate this page |
| `text` | string | no | Generated story text (60–100 words); set when status ≥ `text_ready` |
| `narration_script` | string | no | TTS-optimised version of text; may differ from display text |
| `illustration_url` | string (GCS URL) | no | Signed URL; set when status ≥ `image_ready`; null if generation failed |
| `audio_url` | string (GCS URL) | no | Signed URL; set when status ≥ `audio_ready`; null if generation failed |
| `illustration_failed` | boolean | yes | Default false; true if Imagen call failed for this page |
| `audio_failed` | boolean | yes | Default false; true if TTS call failed for this page |
| `steering_applied` | string[] | no | IDs of SteeringCommands that influenced this page |
| `generated_at` | timestamp | no | Set when status = `complete` |

**State transitions**:
```
pending ──(text generated)──► text_ready ──(image generated or failed)──► image_ready
  └──(image generated or failed)──► image_ready ──(audio generated or failed)──► audio_ready
       └──(audio generated or failed)──► complete
```

**Validation rules**:
- `text` MUST pass content policy check before being stored (SafetyMiddleware applied
  at generation time, not at storage time).
- `illustration_url` and `audio_url` MUST be Cloud Storage URLs in the format
  `gs://{bucket}/sessions/{session_id}/pages/{page_number}/...`.

---

## 5. SteeringCommand

**Firestore path**: `sessions/{session_id}/steering_commands/{command_id}`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `command_id` | string (UUID4) | yes | Unique ID |
| `raw_transcript` | string | yes | Verbatim voice input |
| `interpreted_intent` | string | yes | Agent's semantic interpretation (e.g., "add yellow bird companion from page 3 onward") |
| `steering_type` | enum | yes | `tone_change` \| `pacing_change` \| `element_reintroduction` \| `character_introduction` |
| `applied_to_pages` | integer[] | yes | Pages not yet generated when command was received |
| `new_character_ref_id` | string | no | Set if `steering_type = character_introduction`; references CharacterRef.char_id |
| `safe` | boolean | yes | True if command passed safety check; false if rewritten |
| `safety_event_id` | string | no | Set if `safe = false`; references SafetyEvent.event_id |
| `received_at` | timestamp | yes | UTC |

---

## 6. SafetyEvent

**Firestore path**: `sessions/{session_id}/safety_events/{event_id}`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | string (UUID4) | yes | Unique ID |
| `phase` | enum | yes | `setup` \| `steering` |
| `raw_input` | string | yes | Original unsafe utterance (stored for test/audit; never surfaced in UI) |
| `detected_category` | enum | yes | `physical_harm` \| `character_death` \| `gore` \| `destruction` \| `sexual_content` \| `fear_escalation` |
| `proposed_rewrite` | string | yes | Child-safe alternative proposed to user |
| `user_accepted` | boolean | yes | True if user acknowledged/redirected; false if session abandoned |
| `final_premise` | string | no | The premise used for generation after user's acknowledgement (may differ from proposed_rewrite if user added details) |
| `exclusion_added` | string | no | Content exclusion constraint added to CharacterBible.ContentPolicy |
| `triggered_at` | timestamp | yes | UTC |

**Validation rules**:
- `raw_input` MUST be stored for audit purposes but MUST NOT appear in any UI response,
  caption, illustration prompt, or narration script.
- `user_accepted = false` MUST set StorySession.status = `error` if during setup phase.

---

## Cloud Storage Asset Layout

```
gs://{gcp_project}-story-assets/
└── sessions/
    └── {session_id}/
        ├── pages/
        │   ├── 1/
        │   │   ├── illustration.png     ← Imagen output; also protagonist reference image
        │   │   └── narration.mp3        ← Cloud TTS output
        │   ├── 2/
        │   │   ├── illustration.png
        │   │   └── narration.mp3
        │   ├── 3/ … 4/ … 5/            ← same structure
        └── characters/
            ├── protagonist_ref.png      ← copy of pages/1/illustration.png for reference use
            └── {char_id}_ref.png        ← first-appearance image for steering-introduced characters
```

**Access pattern**: Backend generates short-lived (1 hour) signed read URLs after each
asset is written. Signed URLs are sent to the frontend via the WebSocket event stream
(`page_image_ready`, `page_audio_ready`). Frontend never holds long-lived GCS credentials.

---

## Firestore Index Requirements

| Collection | Fields indexed | Query purpose |
|------------|---------------|---------------|
| `sessions` | `status ASC`, `created_at DESC` | Admin/debug listing of recent sessions |
| `sessions/{id}/pages` | `page_number ASC` | Ordered page retrieval |
| `sessions/{id}/steering_commands` | `received_at ASC` | Chronological steering history |
| `sessions/{id}/safety_events` | `triggered_at ASC` | Safety audit trail |

All other queries are point lookups by document ID and require no composite index.
