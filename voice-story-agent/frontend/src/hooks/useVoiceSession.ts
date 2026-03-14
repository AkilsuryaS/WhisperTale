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
 * 3. `stopSession()`:
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
import { WsClient } from "@/lib/wsClient";
import type { SessionStatus } from "@/lib/wsTypes";

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
  /** Start the session: creates session, reserves ADK slot, connects WS, starts mic. */
  startSession: () => Promise<void>;
  /** Stop streaming and disconnect cleanly. */
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

  const wsClientRef = useRef<WsClient | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const stoppedRef = useRef(false);

  // ---------------------------------------------------------------------------
  // Internal helpers
  // ---------------------------------------------------------------------------

  const stopStreaming = useCallback(() => {
    if (recorderRef.current) {
      try { recorderRef.current.stop(); } catch { /* already stopped */ }
      recorderRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
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
  // startSession
  // ---------------------------------------------------------------------------

  const startSession = useCallback(async () => {
    stoppedRef.current = false;
    setError(null);

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
      token: "",
    };
    const client = _wsClientFactory
      ? _wsClientFactory(wsOpts)
      : new WsClient(wsOpts);

    client
      .on("connected", (evt) => {
        setSessionStatus(evt.session_status);
      })
      .on("session_error", (evt) => {
        setError({ code: evt.code, message: evt.message });
        stopStreaming();
      })
      .on("story_complete", () => {
        setSessionStatus("complete");
        stopStreaming();
      });

    wsClientRef.current = client;
    client.connect();

    if (stoppedRef.current) {
      client.disconnect();
      return;
    }

    // ── Step 4: Request microphone & start streaming ─────────────────────────
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

    const RecorderCtor =
      _MediaRecorder ??
      (typeof MediaRecorder !== "undefined" ? MediaRecorder : undefined);

    if (!RecorderCtor) {
      setError({ code: "no_media_recorder", message: "MediaRecorder not available" });
      return;
    }

    const recorder = new RecorderCtor(stream, { mimeType: "audio/webm" });
    recorderRef.current = recorder;

    recorder.addEventListener("dataavailable", (evt) => {
      if (stoppedRef.current) return;
      const e = evt as BlobEvent;
      if (e.data.size > 0) {
        e.data.arrayBuffer().then((buf) => {
          if (!stoppedRef.current && wsClientRef.current?.isConnected) {
            wsClientRef.current.sendAudio(buf);
          }
        }).catch(() => { /* ignore */ });
      }
    });

    recorder.start(100); // emit chunks every 100 ms
    setIsListening(true);
  }, [resolveFetch, _mediaDevices, _wsClientFactory, _MediaRecorder, stopStreaming]);

  // ---------------------------------------------------------------------------
  // stopSession
  // ---------------------------------------------------------------------------

  const stopSession = useCallback(() => {
    stoppedRef.current = true;
    disconnect();
    setSessionId(null);
    setSessionStatus(null);
    setError(null);
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
    startSession,
    stopSession,
  };
}
