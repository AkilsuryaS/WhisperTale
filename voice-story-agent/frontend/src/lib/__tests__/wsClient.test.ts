/**
 * wsClient.test.ts — Unit tests for WsClient (T-034).
 *
 * Tests cover all "done when" criteria:
 *  1. wsClient.on("transcript", handler) receives transcript events
 *  2. wsClient.sendAudio(pcm) sends a binary frame
 *  3. Disconnect simulation triggers reconnect with exponential back-off
 *  4. TypeScript compiles with no `any` in public API (enforced by tsc --noEmit)
 *
 * Additional tests:
 *  - send() serialises JSON and sends over the socket
 *  - connect() sends session_start on open
 *  - disconnect() prevents auto-reconnect
 *  - Multiple event handlers can be registered independently
 *  - Binary frames are forwarded to onAudioChunk callback
 *  - Malformed JSON text frames are silently dropped
 *  - Unknown event types are ignored (no registered handler)
 *  - retryCount increments on each reconnect attempt
 *  - isConnected reflects socket state
 *  - Reconnect respects maxRetries limit
 */

import { WsClient } from "../wsClient";
import type {
  TranscriptEvent,
  ConnectedEvent,
  PageCompleteEvent,
  WsClientMessage,
} from "../wsTypes";

// ---------------------------------------------------------------------------
// MockWebSocket — minimal fake that captures sent data and exposes triggers
// ---------------------------------------------------------------------------

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState: number = MockWebSocket.CONNECTING;
  binaryType: string = "blob";
  url: string;

  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;

  /** All data passed to send() in order. */
  sent: Array<string | ArrayBuffer> = [];

  private _listeners: Map<string, Array<(e: Event) => void>> = new Map();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, handler: (e: Event) => void): void {
    if (!this._listeners.has(type)) {
      this._listeners.set(type, []);
    }
    this._listeners.get(type)!.push(handler);
  }

  removeEventListener(type: string, handler: (e: Event) => void): void {
    const arr = this._listeners.get(type);
    if (arr) {
      const idx = arr.indexOf(handler);
      if (idx !== -1) arr.splice(idx, 1);
    }
  }

  send(data: string | ArrayBuffer): void {
    this.sent.push(data);
  }

  close(_code?: number, _reason?: string): void {
    this.readyState = MockWebSocket.CLOSED;
    this._emit("close", { code: _code ?? 1000, reason: _reason ?? "" } as CloseEvent);
  }

  // --- Test helpers ---

  /** Simulate the connection opening. */
  triggerOpen(): void {
    this.readyState = MockWebSocket.OPEN;
    this._emit("open", new Event("open"));
  }

  /** Simulate receiving a text message from the server. */
  triggerTextMessage(data: string): void {
    const evt = { data } as MessageEvent<string>;
    this._emit("message", evt as unknown as Event);
  }

  /** Simulate receiving a binary message from the server. */
  triggerBinaryMessage(data: ArrayBuffer): void {
    const evt = { data } as MessageEvent<ArrayBuffer>;
    this._emit("message", evt as unknown as Event);
  }

  /** Simulate the connection closing unexpectedly. */
  triggerClose(code = 1006): void {
    this.readyState = MockWebSocket.CLOSED;
    this._emit("close", { code, reason: "" } as CloseEvent);
  }

  private _emit(type: string, event: Event): void {
    const handlers = this._listeners.get(type) ?? [];
    handlers.forEach((h) => h(event));
  }
}

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/** Create a WsClient with an injected factory that records MockWebSocket instances. */
function makeClient(
  opts: {
    onAudioChunk?: (pcm: ArrayBuffer) => void;
    maxRetries?: number;
    reconnectBaseMs?: number;
  } = {}
): { client: WsClient; sockets: MockWebSocket[] } {
  const sockets: MockWebSocket[] = [];
  const factory = (url: string): WebSocket => {
    const ws = new MockWebSocket(url);
    sockets.push(ws);
    return ws as unknown as WebSocket;
  };

  const client = new WsClient({
    wsBaseUrl: "ws://localhost:8000",
    sessionId: "test-session-id",
    token: "test-token",
    onAudioChunk: opts.onAudioChunk,
    maxRetries: opts.maxRetries ?? 3,
    reconnectBaseMs: opts.reconnectBaseMs ?? 10, // fast for tests
    _factory: factory,
  });

  return { client, sockets };
}

// ---------------------------------------------------------------------------
// Test suites
// ---------------------------------------------------------------------------

describe("WsClient — connection", () => {
  test("connect() opens WebSocket to correct URL", () => {
    const { client, sockets } = makeClient();
    client.connect();
    expect(sockets).toHaveLength(1);
    expect(sockets[0].url).toBe(
      "ws://localhost:8000/ws/story/test-session-id?token=test-token"
    );
  });

  test("connect() is a no-op when already OPEN", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    client.connect(); // second call should be no-op
    expect(sockets).toHaveLength(1);
  });

  test("binaryType is set to arraybuffer on connect", () => {
    const { client, sockets } = makeClient();
    client.connect();
    expect(sockets[0].binaryType).toBe("arraybuffer");
  });

  test("isConnected is false before open", () => {
    const { client } = makeClient();
    client.connect();
    expect(client.isConnected).toBe(false);
  });

  test("isConnected is true after open", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    expect(client.isConnected).toBe(true);
  });

  test("session_start sent automatically on open", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    const lastSent = sockets[0].sent[sockets[0].sent.length - 1];
    const parsed = JSON.parse(lastSent as string) as WsClientMessage;
    expect(parsed.type).toBe("session_start");
    expect((parsed as { session_id: string }).session_id).toBe(
      "test-session-id"
    );
  });
});

describe("WsClient — sending", () => {
  test("send() serialises message as JSON", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    client.send({ type: "ping" });
    const lastSent = sockets[0].sent[sockets[0].sent.length - 1];
    expect(JSON.parse(lastSent as string)).toEqual({ type: "ping" });
  });

  test("send() is dropped when socket not open", () => {
    const { client, sockets } = makeClient();
    client.connect();
    // Not yet open — CONNECTING state
    client.send({ type: "ping" });
    // Only session_start (from open) would be in sent; but open hasn't fired
    expect(sockets[0].sent).toHaveLength(0);
  });

  test("sendAudio() sends ArrayBuffer as binary frame", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    const pcm = new ArrayBuffer(256);
    client.sendAudio(pcm);
    const binaryFrames = sockets[0].sent.filter(
      (d) => d instanceof ArrayBuffer
    );
    expect(binaryFrames).toHaveLength(1);
    expect(binaryFrames[0]).toBe(pcm);
  });

  test("sendAudio() is dropped when socket not open", () => {
    const { client, sockets } = makeClient();
    client.connect();
    // Not yet OPEN
    const pcm = new ArrayBuffer(64);
    client.sendAudio(pcm);
    expect(sockets[0].sent).toHaveLength(0);
  });
});

describe("WsClient — receiving events (done-when: on() receives events)", () => {
  test('on("transcript") handler receives transcript events', () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();

    const received: TranscriptEvent[] = [];
    client.on("transcript", (evt) => received.push(evt));

    const event: TranscriptEvent = {
      type: "transcript",
      turn_id: "turn-1",
      role: "user",
      text: "Hello",
      is_final: true,
      phase: "setup",
    };
    sockets[0].triggerTextMessage(JSON.stringify(event));

    expect(received).toHaveLength(1);
    expect(received[0].text).toBe("Hello");
    expect(received[0].role).toBe("user");
    expect(received[0].is_final).toBe(true);
  });

  test('on("connected") handler receives connected events', () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();

    const received: ConnectedEvent[] = [];
    client.on("connected", (evt) => received.push(evt));

    const event: ConnectedEvent = {
      type: "connected",
      session_id: "test-session-id",
      session_status: "setup",
    };
    sockets[0].triggerTextMessage(JSON.stringify(event));

    expect(received).toHaveLength(1);
    expect(received[0].session_status).toBe("setup");
  });

  test('on("page_complete") handler receives page_complete events', () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();

    const received: PageCompleteEvent[] = [];
    client.on("page_complete", (evt) => received.push(evt));

    const event: PageCompleteEvent = {
      type: "page_complete",
      page: 1,
      illustration_failed: false,
      audio_failed: false,
      generated_at: new Date().toISOString(),
    };
    sockets[0].triggerTextMessage(JSON.stringify(event));

    expect(received).toHaveLength(1);
    expect(received[0].page).toBe(1);
  });

  test("multiple handlers for different event types work independently", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();

    const transcripts: TranscriptEvent[] = [];
    const pings: unknown[] = [];
    client.on("transcript", (evt) => transcripts.push(evt));
    client.on("pong", (evt) => pings.push(evt));

    sockets[0].triggerTextMessage(
      JSON.stringify({ type: "transcript", turn_id: "t1", role: "agent", text: "Hi", is_final: true, phase: "setup" })
    );
    sockets[0].triggerTextMessage(JSON.stringify({ type: "pong" }));

    expect(transcripts).toHaveLength(1);
    expect(pings).toHaveLength(1);
  });

  test("unknown event types are silently ignored", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    // No handler registered for "unknown_type"
    expect(() => {
      sockets[0].triggerTextMessage(JSON.stringify({ type: "unknown_type" }));
    }).not.toThrow();
  });

  test("malformed JSON text frames are silently dropped", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    expect(() => {
      sockets[0].triggerTextMessage("not valid json {{");
    }).not.toThrow();
  });
});

describe("WsClient — binary frames (done-when: sendAudio sends binary)", () => {
  test("binary inbound frames forwarded to onAudioChunk", () => {
    const chunks: ArrayBuffer[] = [];
    const { client, sockets } = makeClient({ onAudioChunk: (pcm) => chunks.push(pcm) });
    client.connect();
    sockets[0].triggerOpen();

    const audio = new ArrayBuffer(512);
    sockets[0].triggerBinaryMessage(audio);

    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toBe(audio);
  });

  test("binary inbound frames not passed to event handlers", () => {
    let handlerCalled = false;
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    // Register a handler that should NOT fire on binary frames
    client.on("transcript", () => { handlerCalled = true; });

    sockets[0].triggerBinaryMessage(new ArrayBuffer(8));
    expect(handlerCalled).toBe(false);
  });
});

describe("WsClient — auto-reconnect (done-when: reconnect with exponential backoff)", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test("unexpected close triggers reconnect after base delay", () => {
    const { client, sockets } = makeClient({ maxRetries: 3, reconnectBaseMs: 100 });
    client.connect();
    sockets[0].triggerOpen();
    sockets[0].triggerClose(1006); // abnormal close

    // First reconnect after 100 ms (2^0 * 100 ms)
    jest.advanceTimersByTime(100);
    expect(sockets).toHaveLength(2);
  });

  test("each retry doubles the delay (exponential back-off)", () => {
    const { client, sockets } = makeClient({ maxRetries: 3, reconnectBaseMs: 100 });
    client.connect();
    sockets[0].triggerOpen();

    // First disconnect
    sockets[0].triggerClose(1006);
    jest.advanceTimersByTime(100); // 1st retry fires
    expect(sockets).toHaveLength(2);

    // Second disconnect
    sockets[1].triggerClose(1006);
    jest.advanceTimersByTime(199); // Not yet enough (needs 200 ms)
    expect(sockets).toHaveLength(2); // no 3rd socket yet
    jest.advanceTimersByTime(1);    // now at 200 ms total
    expect(sockets).toHaveLength(3);
  });

  test("retryCount increments on each attempt", () => {
    const { client, sockets } = makeClient({ maxRetries: 3, reconnectBaseMs: 10 });
    client.connect();
    sockets[0].triggerOpen();
    expect(client.retryCount).toBe(0);

    sockets[0].triggerClose(1006);
    jest.advanceTimersByTime(10);
    expect(client.retryCount).toBe(1);
  });

  test("retryCount resets to 0 after successful reconnect", () => {
    const { client, sockets } = makeClient({ maxRetries: 3, reconnectBaseMs: 10 });
    client.connect();
    sockets[0].triggerOpen();
    sockets[0].triggerClose(1006);
    jest.advanceTimersByTime(10);

    sockets[1].triggerOpen(); // reconnect succeeds
    expect(client.retryCount).toBe(0);
  });

  test("reconnect re-sends session_start after reconnect open", () => {
    const { client, sockets } = makeClient({ maxRetries: 3, reconnectBaseMs: 10 });
    client.connect();
    sockets[0].triggerOpen();
    sockets[0].triggerClose(1006);
    jest.advanceTimersByTime(10);

    sockets[1].triggerOpen();
    const lastSent = sockets[1].sent[sockets[1].sent.length - 1];
    const parsed = JSON.parse(lastSent as string) as WsClientMessage;
    expect(parsed.type).toBe("session_start");
  });

  test("no reconnect after maxRetries exceeded", () => {
    const { client, sockets } = makeClient({ maxRetries: 2, reconnectBaseMs: 10 });
    client.connect();
    sockets[0].triggerOpen();

    // 1st disconnect → 1st retry
    sockets[0].triggerClose(1006);
    jest.advanceTimersByTime(10);
    expect(sockets).toHaveLength(2);

    // 2nd disconnect → 2nd retry (last allowed)
    sockets[1].triggerClose(1006);
    jest.advanceTimersByTime(20);
    expect(sockets).toHaveLength(3);

    // 3rd disconnect → no more retries
    sockets[2].triggerClose(1006);
    jest.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(3); // stays at 3
  });
});

describe("WsClient — disconnect()", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  test("disconnect() prevents auto-reconnect", () => {
    const { client, sockets } = makeClient({ maxRetries: 3, reconnectBaseMs: 10 });
    client.connect();
    sockets[0].triggerOpen();
    client.disconnect();

    jest.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(1); // no new socket created
  });

  test("disconnect() sets isConnected to false", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    expect(client.isConnected).toBe(true);
    client.disconnect();
    expect(client.isConnected).toBe(false);
  });

  test("send() is dropped after disconnect", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].triggerOpen();
    client.disconnect();
    client.send({ type: "ping" });
    // Only session_start was sent before disconnect
    expect(sockets[0].sent.filter((s) => s === JSON.stringify({ type: "ping" }))).toHaveLength(0);
  });
});
