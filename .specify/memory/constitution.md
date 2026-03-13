<!--
Sync Impact Report
- Version change: template (unversioned) -> 1.0.0
- Modified principles:
  - [PRINCIPLE_1_NAME] -> I. Child Safety First (Non-Negotiable)
  - [PRINCIPLE_2_NAME] -> II. Voice-First UX with Captions
  - [PRINCIPLE_3_NAME] -> III. Interleaved Storytelling by Page
  - [PRINCIPLE_4_NAME] -> IV. Character Consistency Across Pages
  - [PRINCIPLE_5_NAME] -> V. Cloud Compliance and Rule Traceability
  - Added VI. Demo Reliability Over Feature Breadth
  - Added VII. Testing and Validation as Release Gates
  - Added VIII. Simplicity and Modular Interfaces
  - Added IX. Human-Centered Caregiver Positioning
- Added sections:
  - Runtime and Product Constraints
  - Delivery Workflow and Evidence
- Removed sections:
  - None
- Templates requiring updates:
  - ✅ reviewed, no edits required: .specify/templates/plan-template.md
  - ✅ reviewed, no edits required: .specify/templates/spec-template.md
  - ✅ reviewed, no edits required: .specify/templates/tasks-template.md
  - ✅ reviewed, no edits required: .cursor/commands/speckit.constitution.md
- Deferred TODOs:
  - None
-->

# Voice Story Agent for Children Constitution

## Core Principles

### I. Child Safety First (Non-Negotiable)
The system MUST never generate violent, graphic, abusive, sexual, or otherwise
age-inappropriate content for children under 12. When user input is disturbing,
the system MUST fail closed by rewriting it into a gentle, child-safe narrative
instead of reproducing unsafe details. Safety policy checks and rewrite behavior
MUST execute before story generation, image prompts, and narration synthesis.

### II. Voice-First UX with Captions
Primary interaction MUST be bidirectional voice using ADK bidi-streaming with Gemini Live-compatible session orchestration. The application MUST simultaneously present text
captions for prompts and responses to support accessibility, comprehension, and
caregiver transparency.

### III. Interleaved Storytelling by Page
Each story page MUST be delivered as one coherent mixed-media unit containing:
page text, character-consistent illustration, and narration audio. Pages MUST
not be considered complete until all three assets are successfully generated,
linked, and rendered in sequence.

### IV. Character Consistency Across Pages
The protagonist defined at story start MUST remain visually consistent across
all pages. The system MUST persist a character bible and use reference-image-
guided image generation on every page to preserve identity, attire, and key
traits unless a deliberate story event explicitly modifies them.

### V. Cloud Compliance and Rule Traceability
Final runtime MUST be hosted on Google Cloud and MUST use Gemini plus at least
one Google Cloud service to satisfy hackathon compliance. Every feature and
component MUST map explicitly to a hackathon requirement and to demo evidence.

### VI. Demo Reliability Over Feature Breadth
The team MUST prioritize a robust, low-friction 5-page storybook flow over
ambitious but fragile functionality. Features that threaten reliability or demo
clarity MUST be deferred, disabled, or simplified before release.

### VII. Testing and Validation as Release Gates
Critical flows MUST have automated tests and validation coverage for safety
rewriting, session orchestration, per-page asset generation, and asset
persistence/retrieval. A release candidate MUST not ship if any critical safety
or consistency test fails.

### VIII. Simplicity and Modular Interfaces
Architecture MUST prefer minimal dependencies and modular services with explicit
interfaces. New modules MUST have single, clear responsibilities and predictable
contracts to keep latency low and debugging straightforward.

### IX. Human-Centered Caregiver Positioning
The product MUST present itself as a caregiver-guided storytelling tool. It
MUST NOT claim to provide therapy, diagnosis, crisis intervention, or clinical
guidance, and copy/text MUST reinforce that boundary.

## Runtime and Product Constraints

- Child safety overrides stylistic fidelity, novelty, and user convenience.
- Unsafe outputs MUST fail closed into safe rewrites before any user-visible
  rendering.
- New features MUST preserve child safety, low latency, and story consistency.
- Voice interaction and caption rendering MUST remain available in every primary
  storytelling flow.
- Story session state MUST persist enough metadata to regenerate or recover the
  full 5-page experience during demos.

## Delivery Workflow and Evidence

- Each planned feature MUST include a requirement-to-implementation mapping and
  a demo evidence artifact (screen capture, logs, or deterministic test output).
- Pull requests MUST document impact on safety, latency, and character
  consistency, and include test results for affected critical flows.
- Architecture decisions MUST record which hackathon rule they satisfy and which
  runtime service proves compliance on Google Cloud.
- Scope reviews MUST explicitly reject additions that compromise reliability of
  the core 5-page interleaved storytelling journey.

## Governance

This constitution overrides convenience and all conflicting local practices.
Any amendment MUST document rationale, expected safety impact, and migration
steps for tests and runtime behavior.

Compliance reviews are mandatory at planning and pre-demo checkpoints. Any
change that weakens child safety, increases latency risk in core flow, or
reduces character consistency MUST be rejected or revised before merge.

Versioning policy uses semantic versioning:
- MAJOR: Removing or redefining a core principle in a backward-incompatible way.
- MINOR: Adding a principle or materially expanding governance requirements.
- PATCH: Clarifications that do not alter operational obligations.

**Version**: 1.0.0 | **Ratified**: 2026-03-12 | **Last Amended**: 2026-03-12
