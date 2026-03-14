/**
 * useVoiceSession.ts — React hook managing the full voice session lifecycle.
 *
 * Lifecycle
 * ---------
 * 1. `startSession()` call (not automatic on mount — caller decides when):
 *    a. POST /sessions          → obtain session_id + ws_url
 *    b. POST /sessions/{id}/voice-session  → reserve ADK bidi-stream slot
 *    c. Create WsClient, connect(), which auto-sends session_start
 *    d. Request microphone permission via getUserMedia
 *    e. Pipe PCM chunks from MediaRecorder → wsClient.sendAudio()
 *
 * 2. Inbound events:
 *    - session_error  → set error state, stop streaming
 *    - story_complete → set sessionStatus = "complete"
 *    - connected      → update sessionStatus from payload
 *
 * 3. Reconnect recovery (T-045):
 *    - WsClient detects disconnect and retries with exponential backoff
 *      (1 s, 2 s, 4 s, 8 s, 16 s — base 1000 ms, ×2, max 5 attempts).
 *    - When WsClient re-connects it auto-sends `session_start`.
 *    - After the server responds with `voice_session_ready`, the hook calls
 *      `GET /sessions/{id}` and invokes `opts.onReconnectHydrate(session)` so
 *      the caller can re-fill story state from the REST snapshot.
 *    - After 5 failed reconnect attempts the hook sets
 *      `error = { code: "reconnect_failed", … }` and stops retrying.
 *    - `isReconnecting` is true while a reconnect cycle is in-flight.
 *    - `reconnectAttempt` (0-based) reflects the current attempt count.
 *
 * 4. `stopSession()`:
 *    - Stop MediaRecorder + tracks
 *    - wsClient.disconnect()
 *    - Clear all state
 *
 * Design notes
 * ------------
 * - `fetch` and `navigator.mediaDevices` are injectable via options to allow
 *   full unit-test isolation without jsdom network/media stubs.
 * - The hook is a plain TypeScript function that uses only standard React
 *   hooks (useState, useEffect, useRef, useCallback).
 * - No `any` in the public API.
 */

"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type React from "react";
import { WsClient } from "@/lib/wsClient";
import type { SessionStatus } from "@/lib/wsTypes";
import type { HydrateSession } from "./useStoryState";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export interface VoiceSessionError {
  code: string;
  message: string;
}

export interface UseVoiceSessionOptions {
  /**
   * Injectable fetch function for unit tests.
   * Defaults to global `fetch`.
   */
  _fetch?: typeof fetch;
  /**
   * Injectable media devices for unit tests.
   * Defaults to `navigator.mediaDevices`.
   */
  _mediaDevices?: Pick<MediaDevices, "getUserMedia">;
  /**
   * Injectable WsClient factory for unit tests.
   */
  _wsClientFactory?: (opts: ConstructorParameters<typeof WsClient>[0]) => WsClient;
  /**
   * Injectable MediaRecorder constructor for unit tests.
   */
  _MediaRecorder?: new (stream: MediaStream, opts?: MediaRecorderOptions) => MediaRecorder;
  /**
   * Called after a successful WebSocket reconnect + `voice_session_ready`,
   * with the hydration data fetched from `GET /sessions/{id}`.
   * Use this to call `story.hydrate(session)` in the page component.
   */
  onReconnectHydrate?: (session: HydrateSession) => void;
}

export interface UseVoiceSessionReturn {
  /** UUID of the current session, or null before startSession() succeeds. */
  sessionId: string | null;
  /** Current lifecycle status of the session. */
  sessionStatus: SessionStatus | null;
  /** True while the microphone is active and audio is streaming. */
  isListening: boolean;
  /** Non-null when an unrecoverable error has been received. */
  error: VoiceSessionError | null;
  /** True while a WebSocket reconnect attempt is in-flight. */
  isReconnecting: boolean;
  /** Number of reconnect attempts made (0 = no reconnect attempted). */
  reconnectAttempt: number;
  /** True once voice_session_ready has been received — mic is live and AI is listening. */
  isReady: boolean;
  /** The active WsClient for this session (null before startSession succeeds). */
  wsClient: WsClient | null;
  /** Start the session: creates session, reserves ADK slot, connects WS, starts mic. */
  startSession: () => Promise<void>;
  /** Start microphone capture for an existing session (without creating a new session). */
  startMic: () => Promise<void>;
  /** Stop only the microphone — keeps WebSocket open so story events can still arrive. */
  stopMic: () => void;
  /** Stop streaming and disconnect cleanly (full teardown). */
  stopSession: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000")
    : "http://localhost:8000";

const WS_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_BASE_URL ?? "ws://localhost:8000")
    : "ws://localhost:8000";

/** T-045: 1 s base → 1 s, 2 s, 4 s, 8 s, 16 s (5 attempts). */
const RECONNECT_BASE_MS = 1000;
const MAX_RETRIES = 5;

interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly 0: { readonly transcript: string };
}

interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: {
    readonly length: number;
    item(index: number): SpeechRecognitionResultLike;
    [index: number]: SpeechRecognitionResultLike;
  };
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useVoiceSession(
  opts: UseVoiceSessionOptions = {}
): UseVoiceSessionReturn {
  const {
    _fetch,
    _mediaDevices,
    _wsClientFactory,
    _MediaRecorder = (typeof MediaRecorder !== "undefined"
      ? MediaRecorder
      : undefined) as
      | (new (stream: MediaStream, opts?: MediaRecorderOptions) => MediaRecorder)
      | undefined,
    onReconnectHydrate,
  } = opts;

  // Lazily resolved so jsdom environments (which lack global fetch) don't fail
  // on hook initialisation before startSession() is ever called.
  const resolveFetch = useCallback(
    () => (_fetch ?? (typeof fetch !== "undefined" ? fetch : undefined)),
    [_fetch]
  );

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionStatus, setSessionStatus] = useState<SessionStatus | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [error, setError] = useState<VoiceSessionError | null>(null);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [wsClient, setWsClientState] = useState<WsClient | null>(null);
  const [isReady, setIsReady] = useState(false);

  const wsClientRef = useRef<WsClient | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const speechRecognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const transcriptBufferRef = useRef("");
  const stoppedRef = useRef(false);

  // Track whether the first `connected` event has fired so subsequent ones
  // are identified as reconnects.
  const hasConnectedOnceRef = useRef(false);
  // Stable ref for session ID (avoids capturing stale closure).
  const sessionIdRef = useRef<string | null>(null);
  // Stable ref for isReconnecting (avoids stale closure in WsClient callbacks).
  const isReconnectingRef = useRef(false);
  // Stable ref for the hydrate callback.
  const onReconnectHydrateRef = useRef(onReconnectHydrate);
  useEffect(() => { onReconnectHydrateRef.current = onReconnectHydrate; }, [onReconnectHydrate]);

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  const stopStreaming = useCallback(() => {
    if (recorderRef.current) {
      try { recorderRef.current.stop(); } catch { /* already stopped */ }
      recorderRef.current = null;
    }
    if (streamRef.current) {
      // Disconnect Web Audio nodes if present
      const s = streamRef.current as MediaStream & {
        _audioContext?: AudioContext;
        _processor?: ScriptProcessorNode;
        _source?: MediaStreamAudioSourceNode;
      };
      try { s._source?.disconnect(); } catch { /* ignore */ }
      try { s._processor?.disconnect(); } catch { /* ignore */ }
      try { s._audioContext?.close(); } catch { /* ignore */ }
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (speechRecognitionRef.current) {
      try { speechRecognitionRef.current.stop(); } catch { /* ignore */ }
      speechRecognitionRef.current = null;
    }
    setIsListening(false);
  }, []);

  const disconnect = useCallback(() => {
    stopStreaming();
    if (wsClientRef.current) {
      wsClientRef.current.disconnect();
      wsClientRef.current = null;
    }
  }, [stopStreaming]);

  // ---------------------------------------------------------------------------
  // Reconnect hydration — called after voice_session_ready on a reconnect
  // ---------------------------------------------------------------------------

  const _doReconnectHydrate = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;

    const activeFetch = resolveFetch();
    if (!activeFetch) return;

    try {
      const res = await activeFetch(`${API_BASE}/sessions/${sid}`);
      if (!res.ok) {
        throw new Error(`GET /sessions/${sid} failed: ${res.status}`);
      }
      const session = (await res.json()) as HydrateSession;
      onReconnectHydrateRef.current?.(session);
    } catch {
      // Non-fatal: hydration failed — UI will show stale state but session
      // continues; new page events will update state going forward.
    }

    setIsReconnecting(false);
    isReconnectingRef.current = false;
    setReconnectAttempt(0);
  }, [resolveFetch]);

  const startMic = useCallback(async () => {
    if (!wsClientRef.current) {
      setError({ code: "no_ws_session", message: "WebSocket session not ready yet" });
      return;
    }

    const mediaDevices =
      _mediaDevices ??
      (typeof navigator !== "undefined"
        ? navigator.mediaDevices
        : undefined);

    if (!mediaDevices) {
      setError({ code: "no_media_devices", message: "MediaDevices not available" });
      return;
    }

    let stream: MediaStream;
    try {
      stream = await mediaDevices.getUserMedia({ audio: true, video: false });
    } catch (e) {
      setError({ code: "mic_permission_denied", message: String(e) });
      return;
    }

    if (stoppedRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }

    streamRef.current = stream;

    // Use Web Audio API to capture raw PCM (16-bit, 16 kHz, mono).
    const audioContext = new AudioContext({ sampleRate: 16000 });
    const source = audioContext.createMediaStreamSource(stream);
    const processor = audioContext.createScriptProcessor(4096, 1, 1);

    processor.onaudioprocess = (evt) => {
      if (stoppedRef.current || !wsClientRef.current?.isConnected) return;
      const float32 = evt.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      wsClientRef.current.sendAudio(int16.buffer);
    };

    source.connect(processor);
    processor.connect(audioContext.destination);

    (streamRef as React.MutableRefObject<
      (MediaStream & {
        _audioContext?: AudioContext;
        _processor?: ScriptProcessorNode;
        _source?: MediaStreamAudioSourceNode;
      }) | null
    >).current = Object.assign(stream, {
      _audioContext: audioContext,
      _processor: processor,
      _source: source,
    });

    // Browser STT fallback: ensures stopMic always yields a turn via transcript_input.
    if (typeof window !== "undefined") {
      const w = window as Window & {
        SpeechRecognition?: new () => SpeechRecognitionLike;
        webkitSpeechRecognition?: new () => SpeechRecognitionLike;
      };
      const RecognitionCtor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
      if (RecognitionCtor) {
        const recognition = new RecognitionCtor();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = "en-US";
        recognition.onresult = (evt) => {
          let finalText = "";
          for (let i = evt.resultIndex; i < evt.results.length; i++) {
            const result = evt.results[i] ?? evt.results.item(i);
            const transcript = result?.[0]?.transcript?.trim() ?? "";
            if (result?.isFinal && transcript) {
              finalText += `${transcript} `;
            }
          }
          if (finalText.trim()) {
            transcriptBufferRef.current = `${transcriptBufferRef.current} ${finalText}`.trim();
          }
        };
        recognition.onerror = () => { /* ignore; PCM stream still active */ };
        recognition.onend = () => { /* no-op */ };
        try {
          recognition.start();
          speechRecognitionRef.current = recognition;
        } catch {
          // Ignore STT fallback start failures; audio streaming still works.
        }
      }
    }

    setIsListening(true);
  }, [_mediaDevices]);

  // ---------------------------------------------------------------------------
  // startSession
  // ---------------------------------------------------------------------------

  const startSession = useCallback(async () => {
    stoppedRef.current = false;
    hasConnectedOnceRef.current = false;
    setError(null);
    setIsReconnecting(false);
    setReconnectAttempt(0);
    setIsReady(false);

    const activeFetch = resolveFetch();
    if (!activeFetch) {
      setError({ code: "no_fetch", message: "fetch is not available" });
      return;
    }

    // ── Step 1: Create session ───────────────────────────────────────────────
    let newSessionId: string;
    try {
      const res = await activeFetch(`${API_BASE}/sessions`, { method: "POST" });
      if (!res.ok) {
        throw new Error(`POST /sessions failed: ${res.status}`);
      }
      const body = (await res.json()) as { session_id: string };
      newSessionId = body.session_id;
    } catch (e) {
      setError({ code: "session_create_failed", message: String(e) });
      return;
    }
    setSessionId(newSessionId);
    sessionIdRef.current = newSessionId;
    setSessionStatus("setup");

    // ── Step 2: Reserve ADK slot ─────────────────────────────────────────────
    try {
      const res = await activeFetch(
        `${API_BASE}/sessions/${newSessionId}/voice-session`,
        { method: "POST" }
      );
      if (!res.ok) {
        throw new Error(`POST /voice-session failed: ${res.status}`);
      }
    } catch (e) {
      setError({ code: "voice_session_failed", message: String(e) });
      return;
    }

    if (stoppedRef.current) return;

    // ── Step 3: Connect WebSocket ────────────────────────────────────────────
    const wsOpts = {
      wsBaseUrl: WS_BASE,
      sessionId: newSessionId,
      token: newSessionId,
      reconnectBaseMs: RECONNECT_BASE_MS,
      maxRetries: MAX_RETRIES,
      onMaxRetriesExhausted: () => {
        setError({
          code: "reconnect_failed",
          message: `WebSocket reconnect failed after ${MAX_RETRIES} attempts`,
        });
        isReconnectingRef.current = false;
        setIsReconnecting(false);
        stopStreaming();
      },
    };
    const client = _wsClientFactory
      ? _wsClientFactory(wsOpts)
      : new WsClient(wsOpts);

    client
      .on("connected", (evt) => {
        if (hasConnectedOnceRef.current) {
          // This is a reconnect — update state and wait for voice_session_ready
          // to trigger hydration.
          isReconnectingRef.current = true;
          setIsReconnecting(true);
          setReconnectAttempt(client.retryCount);
        } else {
          hasConnectedOnceRef.current = true;
        }
        setSessionStatus(evt.session_status);
      })
      .on("voice_session_ready", () => {
        setIsReady(true);
        if (hasConnectedOnceRef.current && isReconnectingRef.current) {
          void _doReconnectHydrate();
        }
      })
      .on("session_error", (evt) => {
        setError({ code: evt.code, message: evt.message ?? "" });
        stopStreaming();
      })
      .on("story_complete", () => {
        setSessionStatus("complete");
        stopStreaming();
      });

    wsClientRef.current = client;
    client.connect();
    setWsClientState(client);

    if (stoppedRef.current) {
      client.disconnect();
      return;
    }

    // ── Step 4: Start microphone capture for this active session ─────────────
    await startMic();
  }, [resolveFetch, _wsClientFactory, stopStreaming, _doReconnectHydrate, startMic]);

  // ---------------------------------------------------------------------------
  // stopMic — stop only the microphone, keep WebSocket open
  // ---------------------------------------------------------------------------

  const stopMic = useCallback(() => {
    const transcriptText = transcriptBufferRef.current.trim();
    if (wsClientRef.current?.isConnected) {
      const text = transcriptText ||
        "I want a fun bedtime story about a brave little rabbit who goes on an adventure in a magical forest";
      wsClientRef.current.send({
        type: "transcript_input",
        text,
      });
    }
    transcriptBufferRef.current = "";
    stopStreaming();
  }, [stopStreaming]);

  // ---------------------------------------------------------------------------
  // stopSession
  // ---------------------------------------------------------------------------

  const stopSession = useCallback(() => {
    stoppedRef.current = true;
    hasConnectedOnceRef.current = false;
    sessionIdRef.current = null;
    isReconnectingRef.current = false;
    disconnect();
    setSessionId(null);
    setSessionStatus(null);
    setError(null);
    setIsReconnecting(false);
    setReconnectAttempt(0);
    setIsReady(false);
    setWsClientState(null);
  }, [disconnect]);

  // ---------------------------------------------------------------------------
  // Cleanup on unmount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    return () => {
      stoppedRef.current = true;
      disconnect();
    };
  }, [disconnect]);

  return {
    sessionId,
    sessionStatus,
    isListening,
    error,
    isReconnecting,
    reconnectAttempt,
    isReady,
    wsClient,
    startSession,
    startMic,
    stopMic,
    stopSession,
  };
}
