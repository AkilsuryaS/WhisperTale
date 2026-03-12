# Research: Voice Story Agent for Children

**Branch**: `001-voice-story-agent` | **Date**: 2026-03-12
**Purpose**: Resolve all technical decisions before Phase 1 design.

---

## 1. Live Voice Orchestration

**Decision**: Google Agent Development Kit (ADK) bidi-streaming with Gemini Live API

**Rationale**:
- ADK provides a first-class Python SDK for bidirectional audio streaming with Gemini Live,
  enabling sub-second voice turn-taking without polling.
- The Gemini Live Agent Challenge explicitly rewards ADK + Gemini Live usage — using this
  stack satisfies hackathon rule V (Cloud Compliance and Rule Traceability) directly.
- ADK sessions are stateful: the agent context (system prompt, conversation history) persists
  across audio turns within a session, making it natural to hold the character bible and
  content exclusions in the system prompt.
- bidi-streaming allows the frontend to interrupt narration mid-sentence (for steering
  commands) without tearing down and re-establishing the session.

**Alternatives considered**:
- REST polling for voice: rejected — too high latency for live conversational feel.
- WebRTC directly to Gemini: not yet generally available via public SDK at time of writing.
- OpenAI Realtime API: rejected — not Google Cloud; violates hackathon compliance rules.

**Integration pattern**:
```
Browser mic → PCM audio frames → WebSocket → FastAPI → ADK LiveSession
ADK LiveSession → audio frames → WebSocket → Browser speaker
ADK LiveSession → text transcripts → WebSocket event stream → CaptionBar
```

---

## 2. Story Text Generation

**Decision**: Gemini 2.5 Pro for 5-page story outline; Gemini 2.5 Flash for per-page
text expansion and live revisions

**Rationale**:
- Gemini 2.5 Pro produces more coherent long-horizon narrative planning. Using it once
  to generate a 5-page arc outline (≈200-300 tokens output) ensures pages are causally
  connected before any per-page generation begins.
- Gemini 2.5 Flash expands each page's outline bullet into full story text (≈80-150 words)
  with very low latency (< 2 s typical), satisfying the per-page timing constraint.
- Flash also handles steering revisions: when a steering command arrives, Flash regenerates
  only the affected page outline entries, not the full arc.

**Alternatives considered**:
- Single Flash call for full 5-page story: insufficient coherence for cross-page callbacks
  (e.g., a character introduced on page 1 reappearing on page 4).
- Single Pro call for all 5 full pages: too slow; total latency unacceptable for live demo.
- Claude / GPT-4: rejected — not Gemini; violates hackathon compliance rules.

**Prompt strategy for story outline (Pro)**:
```
System: You are a warm, playful children's story author for ages 4–11.
        CONTENT POLICY: {content_exclusions}
        CHARACTER BIBLE: {character_bible_json}
        STYLE: {style_bible}

User:   Create a 5-page arc for a story about {protagonist} in {setting},
        tone: {tone}. Return JSON: [{page: 1, beat: "..."}, ...]
```

**Prompt strategy for per-page text (Flash)**:
```
System: Same as above + STORY ARC CONTEXT: {arc_summary} + PAGES SO FAR: {page_history}
User:   Write page {N} story text (60-100 words) based on this beat: "{beat}".
        Return JSON: { text: "...", narration_script: "..." }
```

---

## 3. Safety Layer

**Decision**: Gemini 2.5 Flash as safety classifier and rewriter, executing before every
user utterance is routed to story generation

**Rationale**:
- Flash can return a structured JSON response classifying the utterance AND providing a
  rewritten alternative in a single call (~300 ms), keeping the conversational loop
  responsive.
- Combining classification and rewriting in one call avoids double latency (one call to
  classify, another to rewrite).
- The model is well-calibrated for nuanced content boundaries (e.g., distinguishing
  emotional realism from graphic harm) given a clear system prompt with examples.

**Safety prompt design**:
```
System: You are a content safety evaluator for children's stories (ages 4–11).
        PERMITTED: characters feeling scared, sad, lonely, facing obstacles or conflict.
        FORBIDDEN: physical harm, character death, gore, destruction of characters,
                   sexual content, sustained fear escalation.
        Return JSON: {
          "safe": true|false,
          "category": null|"physical_harm"|"character_death"|"gore"|
                      "destruction"|"sexual_content"|"fear_escalation",
          "rewrite": null|"<child-safe alternative premise>"
        }
User:   Evaluate this story request: "{utterance}"
```

**Alternatives considered**:
- Rule-based keyword blocklist: too brittle; fails on paraphrasing ("the hero gets hurt badly").
- Dedicated safety API (Perspective API, etc.): not tuned for children's story context;
  would double-classify without providing a rewrite.
- Gemini 2.5 Pro for safety: overkill for a classification task; Flash is faster and
  sufficient for this single-turn structured output.

---

## 4. Image Generation

**Decision**: Imagen 3 on Vertex AI with reference-image-guided generation

**Rationale**:
- Imagen 3 is Google's highest-quality image generation model available on Vertex AI.
- The Vertex AI Imagen API supports an `reference_images` parameter that accepts one or
  more images as visual anchors, directly satisfying FR-008 (reference-image-guided
  consistency).
- Imagen's cartoon/illustrated style output is well-suited to the "soft colorful picture
  book" style bible without additional fine-tuning.
- Vertex AI is GCP-native; satisfies hackathon Cloud Compliance rule.

**Reference image workflow**:
- Page 1: generate without reference (text-only prompt from character + style bible).
- Store page-1 image bytes in Cloud Storage; save GCS URL in CharacterBible.reference_image_url.
- Pages 2–5: build image prompt as before, plus `reference_images=[{url: reference_url, weight: 0.85}]`.
- Steering-introduced characters: generate a character sheet on first introduction; store
  as `secondary_references[char_name]`; attach to all subsequent Imagen calls.

**Prompt structure**:
```
"A soft colorful picture book illustration of {protagonist_description} in {setting_description}.
{page_scene_description}. Style: {style_bible.art_style}. 
Color palette: {style_bible.color_palette}. 
No violence, no scary imagery, no character harm."
```

**Alternatives considered**:
- Stable Diffusion / SDXL: not GCP-native; would require a separate Cloud Run instance
  just for image generation; violates simplicity principle.
- DALL-E: not Gemini/GCP; violates hackathon compliance.
- Prompt-only consistency (no reference image): spec clarification Q5 explicitly chose
  reference-image-guided; text alone cannot guarantee consistent visual identity.

---

## 5. Narration Audio

**Decision**: Cloud Text-to-Speech (Neural2 or WaveNet, child-friendly voice en-US-Neural2-F
or equivalent) for page narration; ADK Gemini Live voice for all conversational turns

**Rationale**:
- The spec clarification session chose "unified live voice agent" narration. After
  evaluating demo reliability, the following approach achieves the same user experience
  with higher stability:
  - **Conversational turns** (setup questions, hold phrases, steering acknowledgements,
    safety rewrites, closing message): spoken by ADK Gemini Live voice agent.
  - **Page narration** (the story text read-aloud): synthesised by Cloud TTS, stored as
    an MP3 in Cloud Storage, played by the frontend HTML5 audio element.
  - Both are configured to the same voice (en-US-Neural2-F) and pitch, so no audible
    voice switch is perceptible to the user.
- Using TTS for page narration removes a major source of demo instability: ADK Live
  audio streaming can have brief dropout artefacts under network congestion, which
  would be especially noticeable during the 60-100 second narration of a page.
- Cloud TTS synthesis latency is ~400-800 ms for 100-word scripts, within the total
  per-page budget.

**Alternatives considered**:
- ADK Live for full narration (full unification): theoretically cleaner; rejected due to
  dropout risk during live demo. Documented as a post-hackathon upgrade path.
- ElevenLabs / Murf: third-party; not GCP-native; violates hackathon compliance.

---

## 6. Session Persistence

**Decision**: Firestore (Native mode) for all session state, preferences, character bible
metadata, page records, and steering history

**Rationale**:
- Firestore is GCP-native (satisfies hackathon rule), has a real-time SDK, and stores
  JSON documents natively — a natural fit for the session and page data model.
- Real-time listeners allow the frontend to optionally subscribe to Firestore for session
  recovery after WebSocket reconnect.
- Free tier is generous enough for hackathon demo load.
- The spec requirement for ephemeral sessions in the browser is satisfied by not
  surfacing historical sessions in the UI; Firestore still persists data for demo
  recovery and logging purposes.

**Alternatives considered**:
- PostgreSQL (Cloud SQL): relational schema is unnecessarily rigid for the JSON-heavy
  story state; higher operational overhead.
- Redis (Memorystore): insufficient durability for demo recovery; volatile under restart.
- In-memory only: rejected — fails demo recovery requirement from the deferred COPPA
  section and the constitution's session state persistence rule.

**Collection schema** (summary — full detail in data-model.md):
```
sessions/{session_id}
  preferences/
  character_bible/
  pages/{page_number}
  steering_commands/
  safety_events/
```

---

## 7. Asset Storage

**Decision**: Cloud Storage (GCS) for generated images and narration audio files

**Rationale**:
- GCS is GCP-native, integrates natively with Imagen and Cloud TTS output.
- Signed URLs allow the frontend to fetch assets directly from GCS with time-limited
  access, avoiding routing large binary responses through the FastAPI backend.
- Standard storage class is cost-effective for hackathon demo volumes.

**Bucket structure**:
```
gs://{project}-story-assets/
  sessions/{session_id}/
    pages/{page_number}/
      illustration.png
      narration.mp3
    characters/
      protagonist_ref.png
      {char_name}_ref.png
```

---

## 8. Hosting and Deployment

**Decision**: Cloud Run (backend FastAPI) + Firebase Hosting (Next.js frontend)

**Rationale**:
- Cloud Run: fully managed, auto-scales from zero, HTTPS by default, GCP-native.
  WebSocket support is available (HTTP/2 or HTTP/1.1 upgrade with `--session-affinity`).
- Firebase Hosting: GCP-native, CDN-backed, integrates with Firebase SDK for
  Firestore real-time listeners on the frontend.
- Both satisfy hackathon rule V.

**Cloud Run configuration notes**:
- `--session-affinity` required for WebSocket connections to avoid reconnect storms.
- `--min-instances=1` recommended during demo to eliminate cold-start latency.
- Service account needs roles: `aiplatform.user`, `datastore.user`,
  `storage.objectAdmin`, `logging.logWriter`.

---

## 9. Observability

**Decision**: Cloud Logging via structured JSON logs emitted to stdout from the
FastAPI backend; augmented with session_id and page_number as log labels.

**Rationale**:
- Cloud Run automatically ships stdout to Cloud Logging — zero configuration.
- Structured JSON logs allow filtering by `jsonPayload.session_id` for per-session
  debugging during the demo.
- No additional observability agent or library required; satisfies simplicity principle.

**Log events to emit**:
```
session_created, setup_complete, safety_triggered, safety_accepted,
page_generation_started, page_text_ready, page_image_ready,
page_audio_ready, page_complete, page_asset_failed, steering_received,
steering_applied, story_complete, error
```

---

## 10. COPPA / Child Data Privacy (Deferred)

**Status**: Deferred to post-hackathon.

**Current position**: Sessions are ephemeral in the browser. No PII (name, age, email)
is collected. Firestore stores story content and session IDs (random UUIDs) only.
No user account system exists in MVP. This profile does not trigger COPPA's actual
knowledge threshold for the hackathon demo context.

**Post-hackathon action required**: Legal review before any public production launch;
add explicit COPPA notice, parental consent flow, and data deletion mechanism.
