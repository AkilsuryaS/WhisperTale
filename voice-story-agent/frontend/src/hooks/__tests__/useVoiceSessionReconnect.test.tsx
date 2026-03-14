/**
 * useVoiceSessionReconnect.test.tsx — Unit tests for T-045 reconnect recovery.
 *
 * Done-when criteria verified:
 *  1. Simulated WS close triggers isReconnecting=true (reconnect in-flight)
 *  2. Second `connected` event (reconnect) identified; reconnectAttempt incremented
 *  3. After `voice_session_ready` on reconnect: GET /sessions/{id} is called
 *  4. story.hydrate() is invoked with the fetched session data
 *  5. After 5 failed reconnect attempts (onMaxRetriesExhausted), error state is shown
 *     with code="reconnect_failed"
 *  6. isReconnecting resets to false after hydration completes
 *  7. reconnectAttempt resets to 0 after hydration completes
 *  8. stopSession() resets isReconnecting and reconnectAttempt
 *
 * Additional tests:
 *  - WsClient is created with reconnectBaseMs=1000, maxRetries=5
 *  - onMaxRetriesExhausted callback is wired into WsClient options
 *  - Hydration fetch failure is non-fatal (session continues)
 *  - isReconnecting is false on initial connection
 *  - reconnectAttempt is 0 on initial connection
 */

import React from "react";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useVoiceSession } from "../useVoiceSession";
import type { WsClient } from "@/lib/wsClient";

// ---------------------------------------------------------------------------
// Mock WsClient
// ---------------------------------------------------------------------------

type EventMap = Record<string, (payload: unknown) => void>;
type OnMaxRetriesCb = () => void;

interface MockWsClientOpts {
  reconnectBaseMs?: number;
  maxRetries?: number;
  onMaxRetriesExhausted?: OnMaxRetriesCb;
}

class MockWsClient {
  private _handlers: EventMap = {};
  connectCalled = false;
  disconnectCalled = false;
  isConnected = true;
  retryCount = 0;
  capturedOpts: MockWsClientOpts;

  constructor(opts: MockWsClientOpts) {
    this.capturedOpts = opts;
  }

  on(type: string, handler: (payload: unknown) => void): this {
    this._handlers[type] = handler;
    return this;
  }
  connect(): void { this.connectCalled = true; }
  disconnect(): void { this.disconnectCalled = true; }
  sendAudio(_buf: ArrayBuffer): void { /* noop */ }

  emit(type: string, payload: unknown = {}): void {
    this._handlers[type]?.(payload);
  }

  simulateMaxRetriesExhausted(): void {
    this.capturedOpts.onMaxRetriesExhausted?.();
  }
}

// ---------------------------------------------------------------------------
// Minimal media stubs
// ---------------------------------------------------------------------------

class MockMediaStreamTrack { stop = jest.fn(); }
class MockMediaStream {
  private _tracks = [new MockMediaStreamTrack()];
  getTracks() { return this._tracks; }
}
class MockMediaRecorder {
  private _listeners: Record<string, ((e: unknown) => void)[]> = {};
  start = jest.fn();
  stop = jest.fn();
  addEventListener(event: string, cb: (e: unknown) => void): void {
    if (!this._listeners[event]) this._listeners[event] = [];
    this._listeners[event].push(cb);
  }
}

const mockMediaDevices = {
  getUserMedia: jest.fn().mockResolvedValue(new MockMediaStream()),
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildMockFetch(sessionData: unknown = { session_id: "s1", pages: [] }) {
  return jest.fn().mockImplementation(async (url: string) => {
    if (url.includes("/sessions") && !url.includes("voice-session")) {
      if ((url as string).endsWith("/sessions")) {
        // POST /sessions
        return { ok: true, json: async () => ({ session_id: "session-abc" }) };
      }
      // GET /sessions/{id}
      return { ok: true, json: async () => sessionData };
    }
    if (url.includes("voice-session")) {
      return { ok: true, json: async () => ({}) };
    }
    return { ok: true, json: async () => ({}) };
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useVoiceSession reconnect recovery (T-045)", () => {
  let mockClient: MockWsClient;
  let capturedHydrate: jest.Mock;
  let mockFetch: jest.Mock;

  beforeEach(() => {
    mockClient = new MockWsClient({});
    capturedHydrate = jest.fn();
    mockFetch = buildMockFetch({
      session_id: "session-abc",
      pages: [{ page_number: 1, status: "complete", text: "Once upon a time" }],
    });
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  function renderHookWithMockClient() {
    return renderHook(() =>
      useVoiceSession({
        _fetch: mockFetch,
        _mediaDevices: mockMediaDevices as unknown as MediaDevices,
        _wsClientFactory: (opts) => {
          // Capture all opts so we can test them
          mockClient = new MockWsClient(opts as MockWsClientOpts);
          return mockClient as unknown as WsClient;
        },
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
        onReconnectHydrate: capturedHydrate,
      })
    );
  }

  // ── Initial state ──────────────────────────────────────────────────────────

  it("isReconnecting is false before startSession", () => {
    const { result } = renderHookWithMockClient();
    expect(result.current.isReconnecting).toBe(false);
  });

  it("reconnectAttempt is 0 before startSession", () => {
    const { result } = renderHookWithMockClient();
    expect(result.current.reconnectAttempt).toBe(0);
  });

  it("isReconnecting is false after initial connected event", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });
    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    expect(result.current.isReconnecting).toBe(false);
  });

  // ── WsClient configuration ─────────────────────────────────────────────────

  it("WsClient is created with reconnectBaseMs=1000", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });
    expect(mockClient.capturedOpts.reconnectBaseMs).toBe(1000);
  });

  it("WsClient is created with maxRetries=5", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });
    expect(mockClient.capturedOpts.maxRetries).toBe(5);
  });

  it("WsClient is created with onMaxRetriesExhausted callback", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });
    expect(typeof mockClient.capturedOpts.onMaxRetriesExhausted).toBe("function");
  });

  // ── Reconnect detection ────────────────────────────────────────────────────

  it("second connected event sets isReconnecting=true", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    // First connection
    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    expect(result.current.isReconnecting).toBe(false);

    // Simulate reconnect
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "setup" });
    });
    expect(result.current.isReconnecting).toBe(true);
  });

  it("reconnectAttempt reflects WsClient retryCount on reconnect", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });

    act(() => {
      mockClient.retryCount = 3;
      mockClient.emit("connected", { session_status: "setup" });
    });
    expect(result.current.reconnectAttempt).toBe(3);
  });

  // ── Hydration after reconnect ──────────────────────────────────────────────

  it("GET /sessions/{id} is called after voice_session_ready on reconnect", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    // First connect
    act(() => { mockClient.emit("connected", { session_status: "setup" }); });

    // Reconnect
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "generating" });
    });

    // voice_session_ready fires on reconnect
    await act(async () => {
      mockClient.emit("voice_session_ready", {});
    });

    // Should have called GET /sessions/session-abc
    const getCalls = (mockFetch as jest.Mock).mock.calls.filter(
      ([url]: [string]) => url.includes("/sessions/session-abc") && !url.includes("voice-session")
    );
    expect(getCalls.length).toBeGreaterThanOrEqual(1);
  });

  it("onReconnectHydrate is called with session data after reconnect", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });

    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "generating" });
    });

    await act(async () => {
      mockClient.emit("voice_session_ready", {});
    });

    await waitFor(() => expect(capturedHydrate).toHaveBeenCalled());
    const hydrateArg = capturedHydrate.mock.calls[0][0];
    expect(hydrateArg.session_id).toBe("session-abc");
  });

  it("hydrated pages data is passed to onReconnectHydrate", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "generating" });
    });

    await act(async () => { mockClient.emit("voice_session_ready", {}); });

    await waitFor(() => expect(capturedHydrate).toHaveBeenCalled());
    const hydrateArg = capturedHydrate.mock.calls[0][0];
    expect(Array.isArray(hydrateArg.pages)).toBe(true);
    expect(hydrateArg.pages[0].text).toBe("Once upon a time");
  });

  it("isReconnecting resets to false after hydration completes", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "generating" });
    });

    await act(async () => { mockClient.emit("voice_session_ready", {}); });

    await waitFor(() => expect(result.current.isReconnecting).toBe(false));
  });

  it("reconnectAttempt resets to 0 after hydration completes", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 2;
      mockClient.emit("connected", { session_status: "generating" });
    });

    await act(async () => { mockClient.emit("voice_session_ready", {}); });

    await waitFor(() => expect(result.current.reconnectAttempt).toBe(0));
  });

  it("voice_session_ready without prior reconnect does NOT call onReconnectHydrate", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    // Initial connection (not a reconnect)
    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    await act(async () => { mockClient.emit("voice_session_ready", {}); });

    expect(capturedHydrate).not.toHaveBeenCalled();
  });

  it("hydration fetch failure is non-fatal — no error state set", async () => {
    const failingFetch = jest.fn().mockImplementation(async (url: string) => {
      if ((url as string).endsWith("/sessions")) {
        return { ok: true, json: async () => ({ session_id: "session-abc" }) };
      }
      if (url.includes("voice-session")) {
        return { ok: true, json: async () => ({}) };
      }
      // GET /sessions/{id} fails
      return { ok: false, status: 503 };
    });

    const { result } = renderHook(() =>
      useVoiceSession({
        _fetch: failingFetch,
        _mediaDevices: mockMediaDevices as unknown as MediaDevices,
        _wsClientFactory: (opts) => {
          mockClient = new MockWsClient(opts as MockWsClientOpts);
          return mockClient as unknown as WsClient;
        },
        _MediaRecorder: MockMediaRecorder as unknown as typeof MediaRecorder,
        onReconnectHydrate: capturedHydrate,
      })
    );

    await act(async () => { await result.current.startSession(); });
    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "generating" });
    });
    await act(async () => { mockClient.emit("voice_session_ready", {}); });

    // Error should NOT be set due to hydration failure
    await waitFor(() => expect(result.current.isReconnecting).toBe(false));
    expect(result.current.error).toBeNull();
  });

  // ── Max retries exhausted ──────────────────────────────────────────────────

  it("error code is reconnect_failed after max retries exhausted", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });

    // Trigger max retries exhausted
    act(() => { mockClient.simulateMaxRetriesExhausted(); });

    await waitFor(() =>
      expect(result.current.error?.code).toBe("reconnect_failed")
    );
  });

  it("error message mentions number of attempts after max retries", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });
    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => { mockClient.simulateMaxRetriesExhausted(); });

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error?.message).toContain("5");
  });

  it("isReconnecting is false after max retries exhausted", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "setup" });
    });

    expect(result.current.isReconnecting).toBe(true);

    act(() => { mockClient.simulateMaxRetriesExhausted(); });

    await waitFor(() => expect(result.current.isReconnecting).toBe(false));
  });

  // ── stopSession cleanup ────────────────────────────────────────────────────

  it("stopSession resets isReconnecting to false", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 1;
      mockClient.emit("connected", { session_status: "setup" });
    });
    expect(result.current.isReconnecting).toBe(true);

    act(() => { result.current.stopSession(); });
    expect(result.current.isReconnecting).toBe(false);
  });

  it("stopSession resets reconnectAttempt to 0", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    act(() => { mockClient.emit("connected", { session_status: "setup" }); });
    act(() => {
      mockClient.retryCount = 3;
      mockClient.emit("connected", { session_status: "setup" });
    });
    expect(result.current.reconnectAttempt).toBe(3);

    act(() => { result.current.stopSession(); });
    expect(result.current.reconnectAttempt).toBe(0);
  });

  // ── WsClient.onMaxRetriesExhausted integration ────────────────────────────

  it("WsClient onMaxRetriesExhausted fires the hook error setter", async () => {
    const { result } = renderHookWithMockClient();
    await act(async () => { await result.current.startSession(); });

    const cb = mockClient.capturedOpts.onMaxRetriesExhausted;
    expect(cb).toBeDefined();

    act(() => { cb?.(); });

    await waitFor(() => expect(result.current.error?.code).toBe("reconnect_failed"));
  });
});

// ---------------------------------------------------------------------------
// WsClient onMaxRetriesExhausted unit test
// ---------------------------------------------------------------------------

describe("WsClient onMaxRetriesExhausted callback", () => {
  it("fires the callback when all retries are exhausted", () => {
    const { WsClient } = jest.requireActual<typeof import("@/lib/wsClient")>(
      "@/lib/wsClient"
    );

    const maxRetriesCb = jest.fn();
    let closeHandler: ((event: Partial<CloseEvent>) => void) = () => {};
    const mockWs = {
      binaryType: "arraybuffer",
      readyState: 1,
      send: jest.fn(),
      close: jest.fn(),
      addEventListener: jest.fn((event: string, handler: unknown) => {
        if (event === "close") closeHandler = handler as typeof closeHandler;
      }),
    };

    const client = new WsClient({
      wsBaseUrl: "ws://localhost",
      sessionId: "s1",
      token: "t",
      maxRetries: 2,
      reconnectBaseMs: 10,
      onMaxRetriesExhausted: maxRetriesCb,
      _factory: () => mockWs as unknown as WebSocket,
    });
    client.connect();

    // Exhaust retries: close 3 times (initial + 2 retries, then exhausted)
    jest.useFakeTimers();
    closeHandler({ code: 1006, reason: "error", wasClean: false });
    jest.advanceTimersByTime(10); // retry 1
    closeHandler({ code: 1006, reason: "error", wasClean: false });
    jest.advanceTimersByTime(20); // retry 2
    closeHandler({ code: 1006, reason: "error", wasClean: false }); // exhausted

    expect(maxRetriesCb).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
  });

  it("does not fire callback on intentional disconnect", () => {
    const { WsClient } = jest.requireActual<typeof import("@/lib/wsClient")>(
      "@/lib/wsClient"
    );

    const maxRetriesCb = jest.fn();
    const mockWs = {
      binaryType: "arraybuffer",
      readyState: 1,
      send: jest.fn(),
      close: jest.fn(),
      addEventListener: jest.fn(),
    };

    const client = new WsClient({
      wsBaseUrl: "ws://localhost",
      sessionId: "s1",
      token: "t",
      maxRetries: 1,
      reconnectBaseMs: 10,
      onMaxRetriesExhausted: maxRetriesCb,
      _factory: () => mockWs as unknown as WebSocket,
    });
    client.connect();
    client.disconnect();

    expect(maxRetriesCb).not.toHaveBeenCalled();
  });
});
