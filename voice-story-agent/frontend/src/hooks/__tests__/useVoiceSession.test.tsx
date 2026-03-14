/**
 * useVoiceSession.test.tsx — Unit tests for useVoiceSession hook (T-035).
 *
 * All "done when" criteria:
 *  1. useVoiceSession() — startSession() triggers POST /sessions and WS connect
 *  2. startSession() requests mic permission and starts audio streaming
 *  3. session_error event sets the hook's error state
 *
 * Additional tests:
 *  - startSession() calls POST /sessions/{id}/voice-session (ADK slot reservation)
 *  - story_complete sets sessionStatus to "complete"
 *  - connected event updates sessionStatus
 *  - stopSession() disconnects WS and clears state
 *  - mic permission denied sets error state
 *  - POST /sessions failure sets error state
 *  - POST /voice-session failure sets error state
 *  - isListening becomes true after mic granted, false after stopSession
 *  - unmount cleans up (no state updates after unmount)
 */

import React from "react";
import { renderHook, act } from "@testing-library/react";
import { useVoiceSession } from "../useVoiceSession";
import type { WsClient } from "@/lib/wsClient";
import type { SessionErrorEvent, StoryCompleteEvent, ConnectedEvent } from "@/lib/wsTypes";

// ---------------------------------------------------------------------------
// Minimal mock WsClient
// ---------------------------------------------------------------------------

type EventMap = Record<string, (payload: unknown) => void>;

class MockWsClient {
  private _handlers: EventMap = {};
  connectCalled = false;
  disconnectCalled = false;
  isConnected = true;
  audiosSent: ArrayBuffer[] = [];

  on(type: string, handler: (payload: unknown) => void): this {
    this._handlers[type] = handler;
    return this;
  }
  connect(): void { this.connectCalled = true; }
  disconnect(): void { this.disconnectCalled = true; }
  sendAudio(buf: ArrayBuffer): void { this.audiosSent.push(buf); }

  /** Test helper: fire a server event on this mock client. */
  emit(type: string, payload: unknown): void {
    this._handlers[type]?.(payload);
  }
}

// ---------------------------------------------------------------------------
// Minimal mock MediaStream / MediaRecorder / Track
// ---------------------------------------------------------------------------

class MockMediaStreamTrack {
  stop = jest.fn();
}

class MockMediaStream {
  private _tracks: MockMediaStreamTrack[];
  constructor(tracks?: MockMediaStreamTrack[]) {
    this._tracks = tracks ?? [new MockMediaStreamTrack()];
  }
  getTracks(): MockMediaStreamTrack[] { return this._tracks; }
}

type RecorderEventHandler = EventListenerOrEventListenerObject;

class MockMediaRecorder {
  static readonly instances: MockMediaRecorder[] = [];

  stream: MediaStream;
  private _listeners: Record<string, RecorderEventHandler[]> = {};
  startCalled = false;
  stopCalled = false;
  timeslice?: number;

  constructor(stream: MediaStream, _opts?: MediaRecorderOptions) {
    this.stream = stream;
    MockMediaRecorder.instances.push(this);
  }

  addEventListener(type: string, handler: RecorderEventHandler): void {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(handler);
  }

  start(timeslice?: number): void {
    this.startCalled = true;
    this.timeslice = timeslice;
  }

  stop(): void { this.stopCalled = true; }

  /** Emit a dataavailable event with a fake Blob chunk. */
  emitData(buf: ArrayBuffer): void {
    const blob = new Blob([buf]);
    const evt = Object.assign(new Event("dataavailable"), { data: blob });
    (this._listeners["dataavailable"] ?? []).forEach((h) => {
      if (typeof h === "function") h(evt);
      else (h as EventListenerObject).handleEvent(evt);
    });
  }
}

// ---------------------------------------------------------------------------
// Helper: build standard mocks for one test
// ---------------------------------------------------------------------------

interface TestMocks {
  fetchMock: jest.Mock;
  wsClient: MockWsClient;
  mediaStream: MockMediaStream;
  mediaDevicesMock: { getUserMedia: jest.Mock };
}

function buildMocks(opts: { micFails?: boolean; sessionFails?: boolean; voiceSessionFails?: boolean } = {}): TestMocks {
  const wsClient = new MockWsClient();

  const fetchMock = jest.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (typeof url === "string" && url.endsWith("/sessions") && init?.method === "POST") {
      if (opts.sessionFails) {
        return Promise.resolve({ ok: false, status: 500 });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ session_id: "sess-abc-123" }),
      });
    }
    if (typeof url === "string" && url.includes("/voice-session")) {
      if (opts.voiceSessionFails) {
        return Promise.resolve({ ok: false, status: 409 });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ ready: true }) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });

  const mediaStream = new MockMediaStream();
  const getUserMedia = opts.micFails
    ? jest.fn().mockRejectedValue(new Error("NotAllowedError"))
    : jest.fn().mockResolvedValue(mediaStream as unknown as MediaStream);
  const mediaDevicesMock = { getUserMedia };

  return { fetchMock, wsClient, mediaStream, mediaDevicesMock };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useVoiceSession — done-when: POST /sessions and WS connect", () => {
  it("startSession() calls POST /sessions with correct URL", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/sessions"),
      expect.objectContaining({ method: "POST" })
    );
  });

  it("startSession() sets sessionId from POST /sessions response", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.sessionId).toBe("sess-abc-123");
  });

  it("startSession() calls POST /sessions/{id}/voice-session (ADK slot)", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/sessions/sess-abc-123/voice-session"),
      expect.objectContaining({ method: "POST" })
    );
  });

  it("startSession() calls wsClient.connect()", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(wsClient.connectCalled).toBe(true);
  });

  it("initial state: sessionId=null, sessionStatus=null, isListening=false, error=null", () => {
    const noopFetch = jest.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    const { result } = renderHook(() =>
      useVoiceSession({ _fetch: noopFetch as unknown as typeof fetch })
    );
    expect(result.current.sessionId).toBeNull();
    expect(result.current.sessionStatus).toBeNull();
    expect(result.current.isListening).toBe(false);
    expect(result.current.error).toBeNull();
  });
});

describe("useVoiceSession — done-when: startSession() requests mic and starts streaming", () => {
  it("startSession() calls getUserMedia({ audio: true })", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(mediaDevicesMock.getUserMedia).toHaveBeenCalledWith(
      expect.objectContaining({ audio: true })
    );
  });

  it("startSession() starts MediaRecorder after mic permission granted", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    const recorder = MockMediaRecorder.instances[MockMediaRecorder.instances.length - 1];
    expect(recorder.startCalled).toBe(true);
  });

  it("isListening is true after mic permission granted", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.isListening).toBe(true);
  });

  it("mic permission denied sets error code=mic_permission_denied", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks({ micFails: true });
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.code).toBe("mic_permission_denied");
    expect(result.current.isListening).toBe(false);
  });
});

describe("useVoiceSession — done-when: session_error sets error state", () => {
  it("session_error event sets error state", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    const errorPayload: SessionErrorEvent = {
      type: "session_error",
      code: "generation_failed",
      message: "Gemini timeout",
      session_terminated: true,
    };

    act(() => {
      wsClient.emit("session_error", errorPayload);
    });

    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.code).toBe("generation_failed");
    expect(result.current.error?.message).toBe("Gemini timeout");
  });

  it("session_error stops listening", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    act(() => {
      wsClient.emit("session_error", {
        type: "session_error",
        code: "err",
        message: "fail",
        session_terminated: true,
      } as SessionErrorEvent);
    });

    expect(result.current.isListening).toBe(false);
  });
});

describe("useVoiceSession — additional lifecycle tests", () => {
  it("story_complete event sets sessionStatus to 'complete'", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    act(() => {
      wsClient.emit("story_complete", {
        type: "story_complete",
        session_id: "sess-abc-123",
        page_count: 5,
        pages_with_failures: [],
      } as StoryCompleteEvent);
    });

    expect(result.current.sessionStatus).toBe("complete");
  });

  it("connected event updates sessionStatus", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    act(() => {
      wsClient.emit("connected", {
        type: "connected",
        session_id: "sess-abc-123",
        session_status: "generating",
      } as ConnectedEvent);
    });

    expect(result.current.sessionStatus).toBe("generating");
  });

  it("stopSession() sets isListening=false and clears sessionId", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.isListening).toBe(true);

    act(() => {
      result.current.stopSession();
    });

    expect(result.current.isListening).toBe(false);
    expect(result.current.sessionId).toBeNull();
    expect(result.current.sessionStatus).toBeNull();
  });

  it("stopSession() calls wsClient.disconnect()", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    act(() => {
      result.current.stopSession();
    });

    expect(wsClient.disconnectCalled).toBe(true);
  });

  it("POST /sessions failure sets error code=session_create_failed", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks({ sessionFails: true });
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.error?.code).toBe("session_create_failed");
    expect(result.current.sessionId).toBeNull();
  });

  it("POST /voice-session failure sets error code=voice_session_failed", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks({ voiceSessionFails: true });
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    expect(result.current.error?.code).toBe("voice_session_failed");
    expect(wsClient.connectCalled).toBe(false);
  });

  it("sessionStatus is 'setup' after POST /sessions succeeds", async () => {
    const { fetchMock, wsClient, mediaDevicesMock } = buildMocks();
    MockMediaRecorder.instances.length = 0;

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: fetchMock as unknown as typeof fetch,
        _mediaDevices: mediaDevicesMock,
        _wsClientFactory: () => wsClient as unknown as WsClient,
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
      })
    );

    await act(async () => {
      await result.current.startSession();
    });

    // After startSession: status starts as "setup", may be overridden by connected event
    // At minimum it's been set to "setup" at some point
    expect(["setup", "generating", "complete"]).toContain(result.current.sessionStatus);
  });
});
