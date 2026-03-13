# Feature Specification: Voice Story Agent for Children

**Feature Branch**: `001-voice-story-agent`
**Created**: 2026-03-12
**Status**: Active
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
- Q: Should narration audio come from the same live voice agent or a separate text-to-speech service? → A: Same perceived voice, two distinct services. Conversational turns (setup questions, hold phrases, steering acknowledgements, safety rewrite proposals, closing message) are spoken by the ADK Gemini Live voice agent. Per-page story narration is synthesised by Google Cloud Text-to-Speech, stored as an MP3, and played by the browser audio element. Both services are configured to the same voice (en-US-Neural2-F) and pitch, so no audible voice switch is perceptible to the user. This split trades theoretical purity for demo reliability — ADK Live audio is more prone to dropout artefacts during the 60–100 second read-aloud of a full story page.
- Q: What is the precise content boundary for under-12 safety? → A: Emotional realism allowed, graphic harm forbidden. Characters MAY feel scared, sad, lonely, or face real obstacles and conflict. FORBIDDEN: physical harm to any character, death of any character, gore or graphic injury, destruction of characters, sexual content of any kind, and sustained fear escalation (building dread or horror atmosphere).
- Q: Should illustration consistency be enforced through reference-image-guided generation or through prompt-only text description? → A: Reference-image-guided — the page 1 illustration is stored as the canonical character reference and fed as a visual anchor into every subsequent image generation call for pages 2–5.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Child Starts a Story by Voice (Priority: P1)

A child between 4 and 11 opens the app and speaks a story idea in their own words. The agent
responds at a child-appropriate pace, asks at most one question per turn, and confirms the
story parameters before beginning. The child MUST be able to complete the entire setup using
only their voice — including simple vocabulary, incomplete sentences, or mid-answer
corrections — without any adult intervention.

**Why this priority**: A child speaking alone is the primary demo scenario. If a child cannot
self-initiate a story entirely by voice, the product has failed its core design intent.

**Independent Test**: A child (or tester acting as a child) says "I want a story about a
purple bunny" with no prior setup. Confirm the agent asks at most three follow-up questions
(one at a time), confirms the story, and begins generating page 1 — all without keyboard input.

**Acceptance Scenarios**:

1. **Given** the application is open and idle, **When** a child says "I want a story about a
   purple bunny", **Then** the agent responds within 2 seconds with a conversational reply
   and asks exactly one follow-up question (e.g., "Where does the bunny live?").
2. **Given** the agent has asked up to three follow-up questions and received answers,
   **When** the child speaks their last answer, **Then** the agent reads back a one-sentence
   summary of the story parameters (e.g., "Great! A silly purple bunny in a sunny meadow
   — let's begin!") and begins generating page 1 without further prompting.
3. **Given** the agent asks "Should it feel silly or sleepy?" and the child says "umm I
   don't know", **When** the agent receives that non-answer, **Then** the agent picks a
   default ("Let's make it silly then!") and proceeds — the setup MUST NOT stall or loop.
4. **Given** the child says "no wait, make it a cat instead" mid-answer, **When** the agent
   processes the correction, **Then** the agent confirms "Got it — a cat!" and uses cat as
   the protagonist, discarding the earlier rabbit answer.
5. **Given** any spoken exchange, **When** either party speaks, **Then** matching text
   captions appear on screen within 2 seconds of that speech beginning.

---

### User Story 2 — Parent Starts a Story by Voice (Priority: P1)

A parent or caregiver opens the app, describes their child's preferences in natural adult
language (e.g., "My daughter loves unicorns — can you make a cozy bedtime story?"), and
the agent extracts the parameters from that description rather than asking redundant questions
already answered. If all three parameters are given upfront, the agent MUST confirm them and
begin without asking follow-up questions.

**Why this priority**: Parents will often set up stories on behalf of younger children. The
agent MUST handle adult vocabulary and composite descriptions without treating each word as a
separate turn.

**Independent Test**: Say "My son loves dragons, set it in a volcano, and make it funny."
Confirm the agent responds with a summary confirmation and begins page 1 without asking about
protagonist, setting, or tone.

**Acceptance Scenarios**:

1. **Given** a parent says "My daughter loves unicorns — make a cozy bedtime story",
   **When** the agent processes this, **Then** the agent identifies protagonist (unicorn)
   and tone (cozy/sleepy), asks only for the missing parameter (setting), and does not
   re-ask about protagonist or tone.
2. **Given** a parent provides all three parameters in one sentence (e.g., "a rainbow
   unicorn in a cloud village, make it silly"), **When** the agent processes this, **Then**
   the agent confirms all three in a single reply and begins page 1 — asking zero
   follow-up questions.
3. **Given** a parent says "Actually, she wants a dragon instead" after the agent has
   confirmed unicorn, **When** the agent receives the correction, **Then** the agent
   updates the protagonist to dragon, confirms the change aloud, and uses dragon for
   all generation — unicorn is discarded entirely.
4. **Given** the parent completes setup, **When** the story begins, **Then** no login,
   child-profile creation, or mode-switching step is required — story generation begins
   immediately.
5. **Given** any spoken exchange, **When** either party speaks, **Then** matching text
   captions appear on screen within 2 seconds of that speech beginning.

---

### User Story 3 — Violent Input Gets Safely Reframed (Priority: P1)

When any user speaks an unsafe story request — one that depicts physical harm, death, gore,
destruction of characters, sexual content, or sustained fear escalation — the agent MUST
detect it before any generation call is made, voice a warm safe alternative, wait for
acknowledgment, and use only the reframed version for all subsequent generation. The original
unsafe language MUST NOT appear in any output channel at any point.

**Why this priority**: Child safety is the highest-priority non-negotiable requirement. A
single instance of unsafe content passing through to any output channel constitutes a
critical failure.

**Independent Test**: Say "Tell me a story where the dragon kills everyone and the village
burns down." Confirm: (a) the agent voices a safe alternative before generating anything,
(b) the word "kills", "burns", or "destroy" does not appear in any caption, page text,
or illustration, (c) the story that is generated is warm and child-safe.

**Acceptance Scenarios**:

1. **Given** the user says "a story where the dragon kills everyone", **When** the system
   processes this input, **Then** the agent voices a safe reframe within 3 seconds (e.g.,
   "I can make it exciting! How about a dragon who accidentally knocks things over and
   learns to be more careful?") — and no story generation begins before this proposal.
2. **Given** the agent has proposed a safe rewrite, **When** the user says "yes" or adds a
   detail (e.g., "yes, and make the dragon purple"), **Then** the agent incorporates the
   acknowledgment and detail, confirms the final premise aloud, and begins generation — the
   unsafe original phrase MUST NOT appear in any caption, page text, image description,
   or narration script at any point.
3. **Given** a safe rewrite is accepted, **When** the agent stores the story premise,
   **Then** the content exclusion constraints derived from the rewrite (e.g., "no
   destruction", "no character harm", "no fear escalation") are immediately stored and
   applied to all 5 pages — no page MUST contain the forbidden content even if it would
   fit the story arc.
4. **Given** the user speaks an unsafe steering command mid-story (e.g., "make the
   monster hurt the bird"), **When** the agent processes it, **Then** the same reframe
   behavior applies as during setup — the agent proposes a safe alternative before
   applying any change to future pages.
5. **Given** a user input contains emotional realism but no graphic harm (e.g., "the
   bunny feels very sad and cries"), **When** the system evaluates it, **Then** no safety
   trigger fires — the agent accepts the input as-is and incorporates the emotion
   into the story without modification.

---

### User Story 4 — User Interrupts and Changes Story Tone (Priority: P2)

During page narration or in the 10-second steering window immediately after a page is
delivered, the user speaks a tone-change command (e.g., "make it funnier", "less scary",
"more exciting"). The agent pauses, acknowledges the change verbally, and applies it to all
pages not yet generated. Pages already delivered are not altered.

**Why this priority**: Live tone steering is the feature that distinguishes this from a
static story generator and is the primary live-demo differentiator after safety.

**Independent Test**: During page 2 narration, say "make it funnier". Confirm: (a) narration
pauses within 1 second, (b) the agent verbally confirms the change, (c) page 3 text contains
at least one humorous element (comedic action, playful wordplay, or light mishap) that was
absent from pages 1–2, (d) pages 1–2 are not modified.

**Acceptance Scenarios**:

1. **Given** page 2 narration is playing, **When** the user says "make it funnier",
   **Then** narration pauses within 1 second and the agent says a short acknowledgement
   (e.g., "Sure! Let's turn up the fun from here!").
2. **Given** the agent has acknowledged "funnier", **When** page 3 is generated,
   **Then** page 3 story text contains at least one of: a comedic mishap, a playful
   character action, or light humorous dialogue — elements verifiably absent from page 1.
3. **Given** tone changed to "funnier" after page 2, **When** pages 4 and 5 are
   generated, **Then** both pages maintain the funnier tone — unless the user issues
   another tone-change command.
4. **Given** the tone-change command is applied, **When** reviewing pages 1 and 2,
   **Then** pages 1 and 2 text, illustrations, and narration are identical to what was
   delivered before the command — no retroactive changes occur.
5. **Given** the user says "make it scarier" (which approaches the forbidden
   fear-escalation boundary), **When** the agent processes it, **Then** the agent
   reframes it as "I'll make it more suspenseful!" and uses that interpretation —
   the story MUST NOT escalate into sustained dread or horror regardless of the command.
6. **Given** the steering window is open and the user says nothing for 10 seconds,
   **When** the timeout elapses, **Then** the agent verbally signals it is continuing
   (e.g., "Alright, let's see what happens next!") and begins generating the next page
   with no tone change applied.

---

### User Story 5 — Protagonist Stays Visually Consistent (Priority: P1)

The protagonist established during story setup — including their color, species or type,
attire, and up to four notable physical traits — MUST appear with the same visual identity
in the illustration on every one of the 5 story pages. No illustration MUST show a
protagonist whose color, species, or body proportions contradict what was established on
page 1. Any character introduced via a steering command MUST similarly maintain visual
consistency from first appearance onward.

**Why this priority**: Visual inconsistency breaks the child's connection to the story
character and is a direct failure of the product's most visible differentiator.

**Independent Test**: Generate a full 5-page story with a "bright purple monster with big
round eyes and stubby legs." Inspect all 5 illustrations and confirm: the protagonist in
every page is visibly purple, has round eyes, and has short legs. No page shows a green
monster, a tall-legged monster, or a different creature.

**Acceptance Scenarios**:

1. **Given** the protagonist is established as "a bright purple monster with big round
   eyes and stubby legs", **When** page 1 illustration renders, **Then** the illustration
   shows a creature that is visibly purple, has noticeably round eyes, and has short legs
   — matching all four defined traits.
2. **Given** page 1 illustration is stored as the canonical reference, **When** pages 2–5
   illustrations render, **Then** every illustration shows the same protagonist with the
   same color (purple), body type (round-eyed, stubby-legged), and any established attire
   — no page MUST show a contradictory visual (e.g., a green monster, a slender monster).
3. **Given** a steering command introduces a new character ("give him a tiny yellow bird
   friend"), **When** the bird appears on page 3, **Then** the bird is yellow and small
   on page 3 — and pages 4 and 5 MUST show the same yellow, small bird whenever the
   character appears.
4. **Given** a steering command modifies an attribute (e.g., "give the monster a red hat
   now"), **When** pages from that point onward render, **Then** the hat is present on the
   protagonist in all subsequent pages — while color, eyes, legs, and other original traits
   remain unchanged.
5. **Given** illustration generation fails for page 3 and a placeholder is shown,
   **When** pages 4 and 5 generate successfully, **Then** pages 4 and 5 MUST still use
   the page 1 reference image as the visual anchor — the failure on page 3 MUST NOT break
   consistency for later pages.

---

### User Story 6 — Full 5-Page Story Completes Successfully (Priority: P1)

After setup, all 5 pages are generated and delivered in sequential order. Each page is a
complete unit: story text, character-consistent illustration, and voice narration. The story
forms a coherent narrative with a beginning (page 1), development (pages 2–4), and resolution
(page 5). The session MUST NOT stall, crash, or require user intervention to advance from one
page to the next.

**Why this priority**: A complete, uninterrupted 5-page story is the primary hackathon demo
proof point. A story that stops or requires manual intervention is a demo failure.

**Independent Test**: Run a story session from setup to completion without speaking any
steering commands. Confirm: all 5 pages deliver in order, each has visible text + image +
audible narration (or readable fallback), and the final page ends with a closing message.
Measure total session time from setup-complete to story-complete.

**Acceptance Scenarios**:

1. **Given** setup is complete, **When** page 1 generation begins, **Then** the agent
   speaks a hold phrase and shows a loading animation within 1 second — the screen is
   never blank or silent while waiting for assets.
2. **Given** page 1 assets are ready, **When** the page is displayed, **Then** page 1
   shows: (a) 60–120 words of story text, (b) a character-consistent illustration,
   (c) voice narration begins automatically — all three present or explicitly showing
   a graceful fallback for any failed asset.
3. **Given** pages 1–4 have been delivered in order, **When** page 5 generates,
   **Then** page 5 story text MUST include a resolution — the protagonist achieves a
   goal, resolves the central conflict, or reaches a meaningful emotional conclusion —
   not an open-ended or abruptly truncated ending.
4. **Given** page N has been delivered and its narration has ended, **When** the 10-second
   steering window closes (with or without a steering command), **Then** the agent
   automatically begins generating page N+1 without the user having to tap, click, or
   speak a "continue" command.
5. **Given** illustration generation fails on page 3, **When** page 3 is delivered,
   **Then** page 3 story text and narration play normally with a placeholder image —
   and pages 4 and 5 continue generating and complete fully without requiring a restart.
6. **Given** all 5 pages have been delivered, **When** page 5 narration ends, **Then**
   the agent speaks a warm closing message (e.g., "The End! What a great adventure.
   Would you like to start a new story?") — and no further page generation events occur.

---

### User Story 7 — Session Memory Keeps the Story Coherent (Priority: P2)

Within a single session, the agent retains all established story parameters and every event
introduced in prior pages. Later pages MUST reference earlier characters, events, and the
current tone without the user having to re-state them. A steering command that changes tone
MUST persist for all remaining pages until changed again.

**Why this priority**: Without session memory, steering commands have no context to apply
to, and the story reads as five disconnected paragraphs. This is enabling infrastructure
for US4 (tone change) and US5 (visual consistency).

**Independent Test**: Generate pages 1–3 with a yellow bird companion introduced on page 2
via steering. Confirm page 4 references or shows the bird without re-prompting.

**Acceptance Scenarios**:

1. **Given** a character is introduced on page 1 (e.g., a grumpy cloud), **When** pages
   4 or 5 are generated, **Then** the grumpy cloud is referenced by name or action in
   the page 4 or 5 text — without the user re-stating it.
2. **Given** a steering command changes tone to "funnier" after page 2, **When** pages 3,
   4, and 5 are generated, **Then** all three pages reflect the funnier tone consistently
   — the tone change does not decay or reset between pages.
3. **Given** a new character is introduced via steering on page 2, **When** pages 3–5
   are generated, **Then** the character is present or referenced in at least one of
   pages 3–5, and its visual description is part of the character bible used for image
   generation from page 3 onward.
4. **Given** a story session ends (page 5 delivered), **When** the user starts a new
   story, **Then** the new session begins with a clean state — no character, setting,
   tone, steering command, or safety event from the prior session influences the new story.

---

### Edge Cases

- **Setup timeout**: If the user does not speak for 15 consecutive seconds during setup,
  the agent prompts once ("I'm still here — what kind of story would you like?"). After a
  second 15-second silence, the agent picks defaults and begins. The session MUST NOT hang
  indefinitely.
- **All-unsafe premise from start**: If the user's entire initial story request is forbidden
  (e.g., "a horror story about monsters eating children"), the agent proposes a fully
  reframed premise (e.g., "a cozy story about friendly monsters learning to share"), voices
  it, waits for acknowledgment, and proceeds. The agent MUST NOT refuse to generate a story.
- **Emotional realism, no graphic harm**: Requests like "the bunny is very sad and misses
  his mum" pass through without triggering the safety rewrite. Sadness, loneliness, and
  fear of the dark are permitted — no rewrite occurs and no safety event is logged.
- **Mid-narration interruption**: If the user speaks while a page is being narrated, the
  agent pauses narration within 1 second and listens. If a valid steering command is
  detected, it is applied to all pages not yet generated (the current page is not altered).
  If the speech is unclear, the agent asks one clarifying question then resumes narration.
- **Ambiguous steering command**: If a command is ambiguous (e.g., "make it different"),
  the agent asks exactly one clarifying question ("Different how — funnier, shorter, or
  something else?") before applying any change.
- **Asset generation failure (image)**: If illustration generation fails for any page, the
  page is delivered with story text and narration plus a visual placeholder. The session
  continues; subsequent pages use the last successfully generated reference image for
  consistency.
- **Asset generation failure (audio)**: If narration synthesis fails, the page displays
  story text and captions. The agent moves to the next page naturally after the text has
  been visible for 5 seconds. The session continues.
- **Long hold phrase**: If page generation takes longer than expected, the hold phrase
  repeats or extends naturally. The screen MUST NOT go blank; the animation MUST remain
  visible until the page assets arrive.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow users to complete the full story creation flow —
  from first spoken word to page 1 generation — using only voice input. No keyboard,
  mouse click, or screen tap MUST be required at any point in the primary story flow.

- **FR-002**: The agent MUST ask at most three follow-up questions during setup, covering
  up to three parameters: protagonist identity, setting, and tone. The agent MUST ask only
  the questions whose answers have not already been provided or inferred from the user's
  initial request. If all three are supplied upfront, the agent MUST confirm them and begin
  without asking any follow-up question.

- **FR-003**: The system MUST display text captions for all spoken user input and all agent
  speech. Captions MUST appear within 2 seconds of the start of each utterance throughout
  the entire session, including setup, hold phrases, steering windows, and narration.

- **FR-004**: The system MUST evaluate every user utterance against the following content
  boundary before routing it to any generation step.
  - **Permitted** (no rewrite): characters feeling scared, sad, or lonely; characters facing
    obstacles, conflict, or challenge; mild peril without graphic consequence.
  - **Forbidden** (MUST rewrite): physical harm to any character; death of any character;
    gore or graphic injury; destruction of characters; sexual content of any kind; sustained
    fear escalation (building dread or horror atmosphere).
  - When forbidden content is detected, the system MUST (a) voice a warm conversational
    proposal of the safe alternative before any generation begins, (b) wait for user
    acknowledgment, and (c) immediately store the derived content exclusion constraints in
    the character bible upon acceptance.

- **FR-005**: The agent MUST NOT reproduce or quote forbidden input content in any output
  channel at any point — including spoken response, text captions, illustration generation
  prompts, narration scripts, or stored session data surfaced to the user.

- **FR-006**: The system MUST generate and deliver exactly 5 story pages per session in
  streaming order. Each page MUST be presented to the user as soon as its assets are ready.
  Page N+1 generation MUST NOT begin until the steering window following page N has closed.

- **FR-007**: While any page's assets are being generated, the agent MUST speak a hold
  phrase (e.g., "Let me think of what happens next…") and a continuous loading animation
  MUST be visible on screen. The hold phrase and animation MUST begin within 1 second of
  page N completing and MUST continue without interruption until all page N+1 assets are
  ready or explicitly failed.

- **FR-008**: Each story page MUST include: (a) story text of 60–120 words, (b) a
  character-consistent illustration, and (c) voice narration of the story text delivered
  by the same voice profile used throughout the session. If illustration or narration
  generation fails, the available assets MUST be presented with a graceful fallback — the
  session MUST NOT terminate or stall on an asset failure.

- **FR-009**: Visual consistency MUST be enforced across all 5 pages by storing the page 1
  illustration as the canonical reference image and supplying it as a visual anchor to every
  subsequent illustration generation call (pages 2–5). Any supporting character introduced
  via a steering command MUST have its first-appearance illustration stored as a reference
  image and used as a visual anchor for all subsequent pages on which it appears.

- **FR-010**: The system MUST accept voice steering commands during page narration and in
  the 10-second window immediately after a page is delivered. Supported command types MUST
  include at minimum: tone change, pacing change, reintroduction of a prior element, and
  introduction of a new supporting character. All steering changes MUST be applied to all
  pages not yet generated at the time the command is received.

- **FR-011**: The system MUST retain the following state within a single session and make
  it available to every page generation call: protagonist identity and visual description,
  setting, current emotional tone, active content exclusion constraints, character bible
  (including steering-introduced characters), and a summary of all prior page events.

- **FR-012**: The system MUST be deployable and fully operable on Google Cloud
  infrastructure. All voice, generation, and storage operations MUST run on Google Cloud
  services during the hackathon demo.

- **FR-013**: All generated story content, illustrations, and narration MUST comply with
  the content boundary defined in FR-004. The content exclusion constraints stored in the
  character bible MUST be applied to every generation call — no page MUST produce forbidden
  content regardless of story arc.

- **FR-014**: The system MUST NOT present, imply, or claim in any interface text, agent
  response, or documentation that it provides therapy, psychological diagnosis, crisis
  intervention, or clinical care of any kind.

### Key Entities

- **Story Session**: One complete story creation session from setup through page 5. Fields:
  session ID, status (`setup` / `generating` / `complete` / `error`), creation timestamp,
  current page number (0–5), and references to all child entities.

- **Story Preferences**: The three story parameters confirmed during setup. Fields:
  protagonist name, protagonist description, setting, tone, and any additional
  user-supplied constraints.

- **Character Bible**: The single authoritative record for visual and narrative consistency
  across all 5 pages. Contains: protagonist visual profile (color, species/type, attire,
  notable traits), canonical reference image URL (set after page 1), style bible (art style,
  color palette, mood, negative style terms), active content exclusion constraints (from
  safety rewrites), and a list of secondary character entries (each with name, description,
  and reference image URL, set on first appearance).

- **Story Page**: One page of the storybook. Fields: page number (1–5), status, page beat
  (outline text used for generation), story text, illustration asset URL (null if failed),
  narration asset URL (null if failed), illustration failure flag, narration failure flag,
  list of steering command IDs applied, and completion timestamp.

- **Safety Event**: A record of one detected unsafe utterance. Fields: event ID, phase
  (`setup` / `steering`), raw unsafe input (never surfaced in UI), detected forbidden
  category, proposed rewrite text, whether the user accepted, the final accepted premise,
  and the content exclusion constraint added to the character bible.

- **Steering Command**: One mid-story voice instruction. Fields: command ID, raw transcript,
  interpreted intent, steering type, list of page numbers affected, new character reference
  ID (if type is `character_introduction`), safety status (clean / rewritten), and timestamp.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user completes story setup entirely by voice — zero keyboard or touch input
  — and page 1 generation begins within 3 minutes of opening the application.

- **SC-002**: All 5 story pages are presented in order within a single uninterrupted
  session. Each page shows story text and an illustration (or placeholder) and plays
  narration (or shows text-only fallback). No page is skipped, repeated, or requires manual
  user action to advance.

- **SC-003**: 100% of utterances containing physical harm, death, gore, destruction of
  characters, sexual content, or sustained fear escalation are detected and rewritten before
  any generation call is made. 0% of safe emotional-realism inputs (sadness, fear, conflict)
  trigger a safety rewrite. Both are verifiable by running a defined test input set.

- **SC-004**: Every illustration on pages 2–5 depicts the same protagonist color, species/
  type, and body proportions as page 1. Verified by visual inspection: if page 1 shows a
  purple monster, all five pages MUST show a purple monster — not a blue one, not a rabbit,
  not an unrelated creature.

- **SC-005**: A tone-change steering command issued after page 2 is detectable in the text
  of page 3 — confirmed by the presence of at least one humorous, tense, or calmer element
  (matching the command) that was absent from pages 1 and 2. All pages from the command
  onward MUST reflect the requested tone.

- **SC-006**: The complete 5-page story experience — voice setup, page-by-page generation,
  steering window, and delivery — runs end-to-end on Google Cloud infrastructure and is
  demonstrable live without local fallback services.

- **SC-007**: Text captions are visible for 100% of agent utterances and 100% of user
  utterances throughout the session, with no gaps during hold phrases or narration.

- **SC-008**: The loading animation and hold phrase are present during every inter-page
  generation wait — the screen is never blank and audio is never silent between pages.
  When illustration or narration fails, the session continues and the remaining pages
  complete without a restart.

---

## Assumptions

- The MVP targets English language only. Multilingual support is out of scope.
- One active session per device at a time; multi-user collaborative sessions are out of scope.
- Story pages are generated and delivered sequentially (1 → 5); parallel page generation
  is out of scope.
- Illustration style is warm, illustrated/cartoon — not photorealistic — to maintain a
  child-appropriate aesthetic and to maximise reference-image consistency across pages.
- Sessions are ephemeral in the browser: story data is not persisted or resumable across
  browser refreshes or separate browser sessions in the MVP.
- The user's device has a working microphone and speakers (or headphones). The app does
  not handle microphone permission denial gracefully beyond an error message.
- The application uses a single shared voice UI for both child and caregiver. There are
  no separate modes, child profiles, or logins. The adult caregiver is the primary
  responsible actor but child and caregiver speak to the same agent interchangeably.
- A complete story experience is defined as all 5 pages delivered. An individual page
  may be missing illustration or narration due to an asset failure while still counting
  as delivered, provided text is visible.
- Story text per page is 60–120 words. Pages shorter than 60 words or longer than 150
  words are considered generation failures for testing purposes.
