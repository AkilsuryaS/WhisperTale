# Feature Specification: Voice Story Agent for Children

**Feature Branch**: `001-voice-story-agent`
**Created**: 2026-03-12
**Status**: Draft
**Input**: User description: "Build a Creative Storyteller hackathon project called Voice Story Agent for Children"

---

## Clarifications

### Session 2026-03-12

- Q: Should the system stream pages to the user as each one finishes generating, or generate all 5 pages first and reveal the complete book? → A: Stream page by page — deliver each page as soon as it is ready so that mid-story voice steering can reshape the generation of future pages before they start.
- Q: Is the safety rewrite voiced aloud conversationally or applied silently before any response? → A: Voiced aloud and conversational — the agent proposes the safe alternative to the user (e.g., "How about a big noisy monster who makes a mess by accident and learns to calm down?"), the user acknowledges or redirects, and only then does generation proceed.
- Q: What tone-related question does the agent ask during setup? → A: Tone is one of the three explicit setup follow-up questions, asked after the protagonist and premise are confirmed (e.g., "Should it feel silly or sleepy?").
- Q: Does the character bible include content exclusion rules beyond visual description? → A: Yes — the character bible stores active content exclusion constraints derived from the safety rewrite (e.g., no destruction, no gore, no fear escalation) that apply to all 5 pages.
- Q: Does mid-story steering support additive character introduction, not just style changes? → A: Yes — the user can introduce new supporting characters mid-story (e.g., "Give him a bird friend!"); the new character is added from the next page onward and must remain visually consistent across all remaining pages.
- Q: Do parents and children share the same UI or are there separate modes? → A: Shared single UI — child and caregiver speak to the same voice agent in the same session with no mode switching required.
- Q: When a steering command arrives, does next-page generation begin in the background during current page narration or only after the steering window closes? → A: Generation starts only after the steering window closes — page N+1 is not pre-generated during page N narration, so there is no conflict to cancel or restart.
- Q: While a page is being generated, what should the user see and hear? → A: Animated visual cue plus a soft agent hold phrase — the agent says something like "Let me think of what happens next…" while a gentle animation plays on screen, keeping the child engaged during the wait.
- Q: Should narration audio come from the same live voice agent or a separate text-to-speech service? → A: Unified — the same live voice agent that conducts the conversation also narrates every story page and speaks all hold phrases; there is no separate TTS voice or audible voice switch during the session.
- Q: What is the precise content boundary for under-12 safety? → A: Emotional realism allowed, graphic harm forbidden. Characters MAY feel scared, sad, lonely, or face real obstacles and conflict. FORBIDDEN: physical harm to any character, death of any character, gore or graphic injury, destruction of characters, sexual content of any kind, and sustained fear escalation (building dread or horror atmosphere).
- Q: Should illustration consistency be enforced through reference-image-guided generation or through prompt-only text description? → A: Reference-image-guided — the page 1 illustration is stored as the canonical character reference and fed as a visual anchor into every subsequent image generation call for pages 2–5.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Voice-Initiated Story Setup (Priority: P1)

A parent, child, or caregiver opens the application and speaks to describe the story they want.
The agent listens, responds conversationally, and asks up to three short follow-up questions to
confirm the protagonist, setting, and tone (e.g., "Should it feel silly or sleepy?") before
beginning the story. Child and caregiver share the same voice interface — no mode switching or
separate logins required. No forms, no typing required to start.

**Why this priority**: Without a functional voice-initiated setup flow, the product cannot
demonstrate its core differentiator. This is the gateway to every other story feature.

**Independent Test**: Start the application, speak a one-sentence story request, answer the
agent's clarifying questions by voice, and verify the agent confirms the story parameters and
transitions to page generation — all without touching a keyboard.

**Acceptance Scenarios**:

1. **Given** the application is open and idle, **When** the user speaks a story request
   (e.g., "Tell me a story about a brave little rabbit"), **Then** the agent responds
   conversationally and asks at most three clarifying follow-up questions covering
   protagonist, setting, and tone (e.g., "Should it feel silly or sleepy?").
2. **Given** the agent has asked its clarifying questions and received answers,
   **When** the user speaks their last answer, **Then** the agent summarizes the confirmed
   story parameters in plain language (protagonist, setting, tone, and art style) and
   begins generating page 1.
3. **Given** any spoken exchange, **When** the agent or user speaks, **Then** matching
   text captions appear on screen within 2 seconds of the speech.
4. **Given** the user provides no input for 15 seconds during setup, **When** the timeout
   elapses, **Then** the agent gently prompts the user to speak before waiting again.

---

### User Story 2 — 5-Page Illustrated Story Delivery (Priority: P1)

After story setup, the agent generates and delivers a 5-page illustrated storybook. Each page
includes story text narrated by the same live voice agent that ran the conversation — there is
no voice switch between setup and storytelling. A matching illustration and text captions
accompany each page. Pages arrive sequentially so the child can follow along.

**Why this priority**: This is the core deliverable of the product and the primary hackathon
demo proof point. A complete, playable 5-page story is the minimum viable experience.

**Independent Test**: After completing setup, verify that all 5 pages are presented in order,
each with visible story text, a visible illustration, and audible narration (or text fallback).
No page is skipped or repeated.

**Acceptance Scenarios**:

1. **Given** story setup is complete, **When** the agent begins story generation, **Then**
   page 1 is presented with story text, an illustration, and narration audio before page 2
   begins.
2. **Given** page N assets (text, illustration, narration) are ready, **When** page N is
   delivered and its narration completes, **Then** the agent opens a brief steering window,
   then begins generating page N+1 incorporating any accepted steering commands.
3. **Given** any story page, **When** the illustration renders, **Then** the protagonist's
   name, physical appearance, and attire match what was established in story setup.
4. **Given** narration audio cannot be generated for a page, **When** the page is reached,
   **Then** the story text is displayed and the narration caption still appears so the
   experience degrades gracefully without breaking the session.
5. **Given** a story session completes all 5 pages, **When** page 5 narration ends,
   **Then** the agent delivers a warm closing message and invites the user to start a
   new story.

---

### User Story 3 — Live Mid-Story Voice Steering (Priority: P2)

During page narration or in the brief window immediately after a page is delivered, the user
can speak a steering command to reshape future pages. Commands can adjust tone ("make it
funnier", "less scary"), pacing ("shorter"), reintroduce elements ("bring back the bird"),
or introduce new characters ("give him a bird friend"). The agent acknowledges the change,
applies it to all pages not yet generated, and maintains visual consistency for any new
characters introduced via steering across all remaining pages.

**Why this priority**: Live steering is what makes this a voice agent rather than a static
story generator. It is essential to the demo narrative but is not required for the first page
to be delivered, so it is slightly lower priority than end-to-end delivery.

**Independent Test**: After page 2 is delivered, say "make it funnier" and verify that page 3
and beyond adopt a noticeably lighter, more playful tone compared to a control run without
the command.

**Acceptance Scenarios**:

1. **Given** the agent has just delivered a story page, **When** the user speaks a
   steering command, **Then** the agent verbally acknowledges the adjustment and reflects
   it in all remaining pages not yet generated.
2. **Given** a steering command introduces a new character (e.g., "Give him a bird friend"),
   **When** the agent processes it, **Then** the new character appears from the next page
   onward and its visual description is added to the character bible so it remains consistent
   across all remaining pages.
3. **Given** a steering command refers to a previous element (e.g., "bring back the bird"),
   **When** the agent processes it, **Then** the referenced character or element reappears
   in the next page.
4. **Given** a steering command contains unsafe intent (e.g., "make it more violent"),
   **When** the agent receives it, **Then** the agent gently declines, proposes a safe
   alternative, and continues the story.
5. **Given** page narration has ended and the user has not spoken a steering command within
   10 seconds, **When** the timeout elapses, **Then** the agent automatically begins
   generating the next page with no steering applied.

---

### User Story 4 — Automatic Safety Rewriting (Priority: P1)

User input is evaluated against a precise content boundary. Characters may feel scared, sad,
lonely, or face real conflict and obstacles — emotional realism is permitted. Forbidden content
is any depiction of physical harm to a character, death, gore or graphic injury, destruction
of characters, sexual content, or sustained fear escalation. When forbidden content is
detected, the agent rewrites it conversationally — proposing the safe alternative aloud, and
waiting for the user to acknowledge or redirect before generation proceeds. The agent never
reproduces the forbidden content.

**Why this priority**: Child safety is the highest-priority system requirement per the project
constitution. The system must not be usable to generate harmful content under any
circumstance, making this a hard prerequisite for production readiness.

**Independent Test**: Speak a request that includes an explicitly violent theme (e.g., "a
story where the dragon kills everyone"). Verify the agent responds with a reframed safe
story concept and that no violent language appears in text, captions, illustration prompts,
or narration.

**Acceptance Scenarios**:

1. **Given** user input depicts physical harm, death, gore, destruction of characters,
   sexual content, or sustained fear escalation, **When** the system processes the input,
   **Then** the agent voices a warm conversational proposal of the safe alternative (e.g.,
   "I can make it exciting, but I'll keep it gentle and safe for kids. How about a big
   noisy monster who makes a mess by accident?") before any story generation begins.
1a. **Given** user input includes a character feeling scared, sad, or facing a hard
   challenge, **When** the system processes the input, **Then** it is accepted as-is —
   emotional realism is not a safety trigger and requires no rewrite.
2. **Given** the agent has proposed a safe rewrite, **When** the user acknowledges or
   redirects (e.g., "Yes, and make it purple!"), **Then** the agent incorporates their
   response and proceeds — the unsafe original is never quoted, stored in captions, or
   used in any generation prompt.
3. **Given** a safe rewrite is accepted, **When** the agent begins generating the story,
   **Then** the content exclusion constraints derived from the rewrite (e.g., no
   destruction, no gore, no fear escalation) are stored in the character bible and
   enforced on all 5 pages.
4. **Given** unsafe input appears in a mid-story steering command,
   **When** the agent processes it, **Then** the same safety rewrite behavior applies
   and does not differ from setup-phase handling.

---

### User Story 5 — Session State Memory (Priority: P2)

Within a single story session, the system remembers the protagonist, setting, emotional
tone, and all prior page events. Later pages reference earlier ones coherently, making
the story feel like a unified narrative rather than disconnected episodes.

**Why this priority**: Without session memory, steering commands and character consistency
cannot function. This is enabling infrastructure for Stories 3 and 4 but is surfaced as a
user-visible quality attribute.

**Independent Test**: After generating pages 1–3, verify that page 4 references a character
or event introduced in page 1 without being re-prompted.

**Acceptance Scenarios**:

1. **Given** a character or event is introduced in page 1, **When** page 4 or 5 is
   generated, **Then** that character or event is referenced coherently without the
   user having to re-state it.
2. **Given** a steering command changes the emotional tone (e.g., "make it funnier"),
   **When** subsequent pages are generated, **Then** the new tone is retained for all
   remaining pages unless changed again.
3. **Given** a session ends and a new story is started, **When** the new session begins,
   **Then** no state from the prior session influences the new story.

---

### Edge Cases

- What happens if the user speaks over the agent while a page is being narrated?
  The agent pauses narration and listens; if a steering command is detected, it is recorded
  and applied to all pages not yet generated (current page text/illustration already rendered
  is not altered). If unclear speech is detected, the agent asks the user to repeat then
  resumes narration.
- What happens if the user does not provide answers to follow-up questions?
  After two unanswered prompts, the agent falls back to sensible defaults (e.g., a friendly
  animal protagonist, a forest setting, a cheerful tone) and begins the story.
- What happens if the user requests a story entirely in unsafe themes from the start?
  The system rewrites the entire premise into a child-safe theme, informs the user warmly,
  and proceeds. It does not refuse to generate a story.
- What happens if the user requests a sad or scary moment (e.g., "the bunny feels lonely")?
  Emotional realism is not a safety trigger. The agent accepts it as-is and incorporates
  the emotion naturally. No rewrite occurs.
- What happens while the next page is being generated (loading state)?
  The agent speaks a warm hold phrase (e.g., "Let me think of what happens next…") and a
  gentle animation plays on screen. The user is never left in a silent blank state between
  pages. The hold phrase duration is uncapped — it repeats or extends naturally until the
  page assets are ready.
- What happens if illustration generation fails mid-session?
  The page is still delivered with story text and narration. A placeholder image is shown
  in place of the illustration. The hold phrase and animation transition normally into the
  available assets so the session does not terminate or stall.
- What happens if narration audio fails mid-session?
  The hold phrase concludes, story text and captions are displayed, and the agent
  transitions to the next page naturally. The user can read along. The session continues.
- What happens if a voice steering command is ambiguous?
  The agent asks one clarifying question (e.g., "Do you mean make the whole story funnier,
  or just the next part?") before applying the change.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow users to initiate the full story creation flow
  entirely by voice, without requiring keyboard or touch input.
- **FR-002**: The agent MUST ask at most three clarifying follow-up questions covering
  protagonist identity, setting, and tone (e.g., "Should it feel silly or sleepy?")
  before starting story generation.
- **FR-003**: The system MUST display real-time text captions for all spoken user input
  and all agent speech throughout the session.
- **FR-004**: The system MUST evaluate all user input against the following content
  boundary. Permitted: characters feeling scared, sad, lonely, or facing conflict and
  obstacles. Forbidden: physical harm to any character, death of any character, gore or
  graphic injury, destruction of characters, sexual content of any kind, and sustained
  fear escalation. When forbidden content is detected, the system MUST rewrite it,
  voice the rewrite conversationally before any generation begins, and store the derived
  content exclusion constraints in the character bible for enforcement across all 5 pages.
- **FR-005**: The agent MUST NOT reproduce or quote unsafe input content in any output
  channel (text, captions, illustration prompts, or narration).
- **FR-006**: The system MUST generate and deliver exactly 5 story pages per session in
  streaming page-by-page order: each page is presented to the user as soon as its text,
  illustration, and narration are ready, without waiting for subsequent pages to complete.
- **FR-007**: Each story page MUST include story text, a character-consistent illustration,
  and narration spoken by the same live voice agent that conducted the setup conversation —
  there MUST be no audible voice switch at any point in the session. While page assets are
  being generated, the agent MUST speak a warm hold phrase (e.g., "Let me think of what
  happens next…") accompanied by a gentle on-screen animation so the user is never left in
  a silent or blank state. If illustration generation fails, the available assets are
  presented with a graceful fallback and the session continues without interruption.
- **FR-008**: Visual consistency MUST be enforced through reference-image-guided
  generation: the illustration produced on page 1 is stored as the canonical character
  reference and MUST be supplied as a visual anchor to every image generation call for
  pages 2–5. Any supporting character introduced via a steering command MUST have its
  first appearance stored as a reference image and used as a visual anchor for all
  subsequent pages on which it appears.
- **FR-009**: The system MUST accept voice steering commands during page narration or in
  the steering window immediately after a page is delivered. Supported steering types
  include tone changes, pacing changes, reintroduction of prior elements, and additive
  character introduction. The system MUST apply the specified changes to all pages not
  yet generated; next-page generation begins only after the steering window closes.
- **FR-010**: The system MUST retain protagonist, setting, emotional tone, and all
  prior page events within a single session.
- **FR-011**: The system MUST be deployable and fully operable on Google Cloud
  infrastructure.
- **FR-012**: All generated story content, illustrations, and narration MUST comply with
  the content boundary defined in FR-004: emotional realism is permitted; physical harm,
  death, gore, destruction of characters, sexual content, and sustained fear escalation
  are forbidden in all output channels.
- **FR-013**: The system MUST NOT make any claim of providing therapy, diagnosis,
  crisis care, or clinical support in its interface, prompts, or documentation.

### Key Entities

- **Story Session**: Represents one complete story creation session from setup through
  page 5. Holds status (setup / generating / complete), session start time, and
  references to all pages and preferences.
- **Story Preferences**: Captures the protagonist name and description, setting,
  emotional tone, and any additional user-provided constraints gathered during setup.
- **Character Bible**: The persisted visual and narrative record used to maintain
  consistency across all 5 pages. Includes protagonist physical description, attire, and
  notable traits; the page 1 illustration stored as the canonical reference image and used
  as a visual anchor in every subsequent image generation call (pages 2–5); visual
  descriptions and reference images for any supporting characters introduced via steering
  commands; art style notes; and active content exclusion constraints derived from safety
  rewriting (e.g., no destruction, no gore, no fear escalation).
- **Story Page**: One page of the storybook. Contains page number (1–5), story text,
  illustration asset reference, and a record that narration was delivered live by the
  voice agent (no separate audio file stored for narration in MVP).
- **Safety Event**: A record of detected unsafe input, the rewritten safe alternative,
  and the phase in which it occurred (setup or steering). Used for validation and testing.
- **Steering Command**: A mid-story voice instruction that modifies future page
  generation. Contains the original transcript, the interpreted intent, and the pages
  it was applied to.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can complete story setup entirely by voice (no typing) and reach
  page 1 generation within 3 minutes of opening the application.
- **SC-002**: All 5 story pages are generated and presented with story text, illustration,
  and live narration by the same voice agent in a single uninterrupted session — no voice
  switch is audible between setup conversation and story delivery.
- **SC-003**: 100% of user inputs that depict physical harm, death, gore, destruction of
  characters, sexual content, or sustained fear escalation are rewritten before reaching
  story generation or rendering. Inputs expressing emotional realism (sadness, fear,
  conflict) pass through without triggering a rewrite.
- **SC-004**: The protagonist's visual appearance is recognizably consistent (same
  character identity, attire, and key traits) across all 5 story page illustrations,
  enforced by reference-image-guided generation using the page 1 illustration as the
  canonical anchor for pages 2–5.
- **SC-005**: A voice steering command issued after any page is reflected in the
  narrative content and tone of all subsequent pages in the same session.
- **SC-006**: The complete 5-page story experience — voice setup, generation, steering,
  and delivery — is demonstrable end-to-end on Google Cloud infrastructure.
- **SC-007**: Text captions are visible for 100% of spoken agent responses and user
  voice inputs throughout the session.
- **SC-008**: The user is never left in a silent or blank state between pages — a hold
  phrase and gentle animation play during every generation wait. When illustration or
  narration fails, the session continues without crashing and the affected page remains
  readable with available assets.

---

## Assumptions

- The MVP targets English language only. Multilingual support is out of scope.
- One active session per device at a time; multi-user collaborative sessions are out of scope.
- Story pages are generated and delivered sequentially (1 → 5), not in parallel.
- Illustration style is warm, illustrated/cartoon — not photorealistic — to maintain a
  child-appropriate aesthetic and to maximise reference-image consistency across pages.
- Sessions are ephemeral for MVP: story data is not persisted across browser refreshes
  or separate sessions.
- The user's device has a working microphone and speakers (or headphones).
- The application uses a single shared voice UI for both child and caregiver — no separate
  modes or logins. The adult caregiver is the primary responsible actor but child and
  caregiver speak to the same agent interchangeably.
- A complete story experience is defined as all 5 pages delivered, not necessarily with
  all three asset types per page (graceful fallback is acceptable).
