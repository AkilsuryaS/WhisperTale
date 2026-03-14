/**
 * wsTypes.ts — TypeScript interfaces matching the api-spec.yaml WebSocket contract.
 *
 * All client→server and server→client message types are defined here as
 * discriminated unions keyed on the `type` field.  No `any` types appear
 * in the public surface.
 *
 * Generated from: contracts/api-spec.yaml (x-websocket-events section)
 */

// ---------------------------------------------------------------------------
// Shared enums (matching api-spec.yaml #/components/schemas)
// ---------------------------------------------------------------------------

export type SessionStatus = "setup" | "generating" | "complete" | "error";
export type CommandType =
  | "tone_change"
  | "pacing_change"
  | "element_reintroduction"
  | "character_introduction";
export type AssetType = "illustration" | "narration";
export type TurnPhase = "setup" | "steering" | "narration";
export type SafetyCategory =
  | "physical_harm"
  | "character_death"
  | "gore"
  | "destruction"
  | "sexual_content"
  | "fear_escalation";

// ---------------------------------------------------------------------------
// CLIENT → SERVER messages
// ---------------------------------------------------------------------------

/** Activate the reserved ADK bidi-stream slot. */
export interface SessionStartMessage {
  type: "session_start";
  session_id: string;
}

/** Text input fallback (non-audio clients, integration tests). */
export interface TranscriptInputMessage {
  type: "transcript_input";
  text: string;
  phase?: TurnPhase;
}

/** Signal that the user has spoken mid-narration; pauses narration. */
export interface InterruptMessage {
  type: "interrupt";
  page_number: number;
}

/** Explicit steering command submitted as text over WebSocket. */
export interface VoiceFeedbackMessage {
  type: "voice_feedback";
  raw_transcript: string;
  command_type: CommandType;
}

/** Keepalive ping; server responds with `pong`. */
export interface PingMessage {
  type: "ping";
}

/** Union of all client→server JSON messages. */
export type WsClientMessage =
  | SessionStartMessage
  | TranscriptInputMessage
  | InterruptMessage
  | VoiceFeedbackMessage
  | PingMessage;

// ---------------------------------------------------------------------------
// SERVER → CLIENT messages
// ---------------------------------------------------------------------------

/** Sent immediately after the WebSocket handshake. */
export interface ConnectedEvent {
  type: "connected";
  session_id: string;
  session_status: SessionStatus;
}

/** ADK bidi-stream is active; microphone audio is now processed. */
export interface VoiceSessionReadyEvent {
  type: "voice_session_ready";
  session_id: string;
  voice_model: string;
}

/** Real-time speech transcript for caption rendering. */
export interface TranscriptEvent {
  type: "transcript";
  turn_id: string;
  role: "user" | "agent";
  text: string;
  is_final: boolean;
  phase: TurnPhase;
}

/** A complete user utterance has been recognised and routed. */
export interface TurnDetectedEvent {
  type: "turn_detected";
  turn_id: string;
  sequence: number;
  phase: TurnPhase;
  caption_text: string;
  routed_to:
    | "setup_extraction"
    | "safety_check"
    | "steering_parser"
    | "narration_ack";
  voice_command_id: string | null;
  safety_decision_id: string | null;
}

/** One setup parameter confirmed by the agent. */
export interface StoryBriefUpdatedEvent {
  type: "story_brief_updated";
  param: "protagonist_name" | "protagonist_description" | "setting" | "tone";
  value: string;
  remaining_params: string[];
}

/** All three setup parameters locked; generation begins. */
export interface StoryBriefConfirmedEvent {
  type: "story_brief_confirmed";
  session_id: string;
  story_brief: {
    protagonist_name: string;
    protagonist_description: string;
    setting: string;
    tone: string;
    confirmed_by_agent: boolean;
    confirmed_at: string | null;
  };
  agent_summary: string;
}

/** CharacterBible and StyleBible generated and persisted. */
export interface CharacterBibleReadyEvent {
  type: "character_bible_ready";
  session_id: string;
  character_bible: {
    protagonist_name: string;
    species_or_type: string;
    color: string;
    attire: string | null;
    notable_traits: string[];
    reference_image_gcs_uri: string | null;
    style_bible: {
      art_style: string;
      color_palette: string;
      mood: string;
      negative_style_terms: string[];
      last_updated_by_command_id: string | null;
    };
    content_policy: {
      exclusions: string[];
      derived_from_safety_decisions: string[];
    };
    secondary_characters: Array<{
      char_id: string;
      name: string;
      description: string;
      reference_image_gcs_uri: string | null;
      introduced_on_page: number;
      voice_command_id: string;
    }>;
  };
}

/** Safety middleware detected forbidden content. */
export interface SafetyRewriteEvent {
  type: "safety_rewrite";
  decision_id: string;
  turn_id: string;
  detected_category: SafetyCategory;
  proposed_rewrite: string;
  phase: "setup" | "steering";
}

/** User acknowledged the safety rewrite. */
export interface SafetyAcceptedEvent {
  type: "safety_accepted";
  decision_id: string;
  final_premise: string;
  exclusion_added: string | null;
}

/** User abandoned the session after a safety rewrite. */
export interface SafetyRejectedEvent {
  type: "safety_rejected";
  decision_id: string;
  phase: "setup" | "steering";
  session_terminated: boolean;
}

/** Backend has begun generating a new page. */
export interface PageGeneratingEvent {
  type: "page_generating";
  page: number;
  voice_commands_applied: string[];
}

/** Story text for this page is available. */
export interface PageTextReadyEvent {
  type: "page_text_ready";
  page: number;
  text: string;
}

/** Illustration PageAsset is ready. */
export interface PageImageReadyEvent {
  type: "page_image_ready";
  page: number;
  asset_id: string;
  image_url: string;
  signed_url_expires_at: string;
}

/** Cloud TTS narration for this page is ready. */
export interface PageAudioReadyEvent {
  type: "page_audio_ready";
  page: number;
  asset_id: string;
  audio_url: string;
  signed_url_expires_at: string;
}

/** One asset could not be generated; session continues. */
export interface PageAssetFailedEvent {
  type: "page_asset_failed";
  page: number;
  asset_type: AssetType;
  asset_id: string | null;
  reason: string;
}

/** All assets for this page have reached a terminal state. */
export interface PageCompleteEvent {
  type: "page_complete";
  page: number;
  illustration_failed: boolean;
  audio_failed: boolean;
  generated_at: string;
}

/** Steering window is open; agent is listening for a VoiceCommand. */
export interface SteeringWindowOpenEvent {
  type: "steering_window_open";
  page_just_completed: number;
  timeout_ms: number;
}

/** A VoiceCommand has been heard and is being processed. */
export interface VoiceCommandReceivedEvent {
  type: "voice_command_received";
  command_id: string;
  turn_id: string;
  raw_transcript: string;
  interpreted_as: string;
}

/** VoiceCommand has passed safety check and been applied. */
export interface VoiceCommandAppliedEvent {
  type: "voice_command_applied";
  command_id: string;
  command_type: CommandType;
  pages_affected: number[];
  new_character_ref_id: string | null;
}

/** Steering window has ended. */
export interface SteeringWindowClosedEvent {
  type: "steering_window_closed";
  reason: "timeout" | "voice_command_applied" | "user_silent";
}

/** All 5 pages delivered; session is complete. */
export interface StoryCompleteEvent {
  type: "story_complete";
  session_id: string;
  page_count: 5;
  pages_with_failures: number[];
}

/** Unrecoverable session error; WebSocket will close. */
export interface SessionErrorEvent {
  type: "session_error";
  code: string;
  message: string;
  session_terminated: true;
}

/** Response to client `ping` keepalive. */
export interface PongEvent {
  type: "pong";
}

/** Union of all server→client JSON messages. */
export type WsServerEvent =
  | ConnectedEvent
  | VoiceSessionReadyEvent
  | TranscriptEvent
  | TurnDetectedEvent
  | StoryBriefUpdatedEvent
  | StoryBriefConfirmedEvent
  | CharacterBibleReadyEvent
  | SafetyRewriteEvent
  | SafetyAcceptedEvent
  | SafetyRejectedEvent
  | PageGeneratingEvent
  | PageTextReadyEvent
  | PageImageReadyEvent
  | PageAudioReadyEvent
  | PageAssetFailedEvent
  | PageCompleteEvent
  | SteeringWindowOpenEvent
  | VoiceCommandReceivedEvent
  | VoiceCommandAppliedEvent
  | SteeringWindowClosedEvent
  | StoryCompleteEvent
  | SessionErrorEvent
  | PongEvent;

/** Extract the payload type for a given server event type string. */
export type WsServerEventByType<T extends WsServerEvent["type"]> = Extract<
  WsServerEvent,
  { type: T }
>;
