/**
 * wsClient.ts — Typed bidi-streaming WebSocket client for the Voice Story Agent.
 *
 * Responsibilities
 * ----------------
 * - Connect to `${NEXT_PUBLIC_WS_BASE_URL}/ws/story/{session_id}?token={token}`
 * - Send binary PCM audio frames via `sendAudio(pcm: ArrayBuffer)`
 * - Send typed JSON control messages via `send(msg: WsClientMessage)`
 * - Route inbound text frames by `type` to registered handlers via `on()`
 * - Forward inbound binary frames to `onAudioChunk` callback
 * - Auto-reconnect on unintended disconnect: exponential back-off
 *   (base 500 ms, factor ×2, max 5 retries); re-emits `session_start` on reconnect
 * - Expose `disconnect()` for intentional close (no reconnect)
 *
 * Design constraints
 * ------------------
 * - Zero `any` in the public API; internal plumbing uses narrow casts only where
 *   the browser WebSocket API forces it (raw `MessageEvent`).
 * - The `WebSocket` constructor is injected via the `_factory` option to enable
 *   full unit-test isolation (Jest replaces the global with a mock).
 */

import type {
  WsClientMessage,
  WsServerEvent,
  WsServerEventByType,
} from "./wsTypes";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Handler function called with the typed payload for a specific event type. */
export type EventHandler<T extends WsServerEvent["type"]> = (
  payload: WsServerEventByType<T>
) => void;

/** Options accepted by the `WsClient` constructor. */
export interface WsClientOptions {
  /** Base URL (ws:// or wss://), e.g. `ws://localhost:8000`. */
  wsBaseUrl: string;
  /** Session ID (UUID). */
  sessionId: string;
  /** Bearer token for authentication. */
  token: string;
  /**
   * Callback for inbound binary frames (agent audio PCM).
   * Called with the raw `ArrayBuffer` of each binary WebSocket frame.
   */
  onAudioChunk?: (pcm: ArrayBuffer) => void;
  /**
   * Maximum number of automatic reconnect attempts (default 5).
   * Set to 0 to disable auto-reconnect.
   */
  maxRetries?: number;
  /**
   * Base delay in ms for the first reconnect attempt (default 500).
   * Each subsequent attempt doubles the delay.
   */
  reconnectBaseMs?: number;
  /**
   * Injectable WebSocket factory — used by tests to inject a mock implementation.
   * Defaults to the global `WebSocket` constructor.
   */
  _factory?: (url: string) => WebSocket;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_MAX_RETRIES = 5;
const DEFAULT_RECONNECT_BASE_MS = 500;

// ---------------------------------------------------------------------------
// WsClient
// ---------------------------------------------------------------------------

export class WsClient {
  private readonly _wsBaseUrl: string;
  private readonly _sessionId: string;
  private readonly _token: string;
  private readonly _onAudioChunk: ((pcm: ArrayBuffer) => void) | undefined;
  private readonly _maxRetries: number;
  private readonly _reconnectBaseMs: number;
  private readonly _factory: (url: string) => WebSocket;

  /** Registered event-type → handler map.  Keyed by type string. */
  private readonly _handlers: Map<string, EventHandler<WsServerEvent["type"]>>;

  private _ws: WebSocket | null = null;
  /** True after `disconnect()` is called — prevents auto-reconnect. */
  private _intentionalClose = false;
  private _retryCount = 0;
  private _reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(opts: WsClientOptions) {
    this._wsBaseUrl = opts.wsBaseUrl;
    this._sessionId = opts.sessionId;
    this._token = opts.token;
    this._onAudioChunk = opts.onAudioChunk;
    this._maxRetries = opts.maxRetries ?? DEFAULT_MAX_RETRIES;
    this._reconnectBaseMs = opts.reconnectBaseMs ?? DEFAULT_RECONNECT_BASE_MS;
    this._factory = opts._factory ?? ((url: string) => new WebSocket(url));
    this._handlers = new Map();
  }

  // ── Public interface ──────────────────────────────────────────────────────

  /**
   * Register a handler for a specific server event type.
   *
   * Only one handler per type is supported (last registration wins).
   *
   * @example
   * client.on("transcript", (evt) => console.log(evt.text));
   */
  on<T extends WsServerEvent["type"]>(
    type: T,
    handler: EventHandler<T>
  ): this {
    // The internal map stores handlers as the widest handler type.
    // The double-cast through `unknown` is safe: when we retrieve by the same
    // `type` key, the dispatched payload is always `WsServerEventByType<T>`.
    this._handlers.set(
      type,
      handler as unknown as EventHandler<WsServerEvent["type"]>
    );
    return this;
  }

  /**
   * Open the WebSocket connection.
   * Calling `connect()` on an already-open socket is a no-op.
   */
  connect(): void {
    if (
      this._ws !== null &&
      (this._ws.readyState === WebSocket.OPEN ||
        this._ws.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this._intentionalClose = false;
    this._openSocket();
  }

  /**
   * Intentionally close the WebSocket.
   * Auto-reconnect will NOT be attempted after calling this method.
   */
  disconnect(): void {
    this._intentionalClose = true;
    this._clearReconnectTimer();
    if (this._ws !== null) {
      this._ws.close(1000, "client disconnect");
      this._ws = null;
    }
  }

  /**
   * Send a typed JSON control message to the server.
   *
   * Silently dropped if the socket is not in OPEN state.
   */
  send(msg: WsClientMessage): void {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(msg));
    }
  }

  /**
   * Send a raw PCM audio chunk as a binary WebSocket frame.
   *
   * Silently dropped if the socket is not in OPEN state.
   *
   * @param pcm  Raw PCM ArrayBuffer (16-bit signed little-endian, 16 kHz mono).
   */
  sendAudio(pcm: ArrayBuffer): void {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(pcm);
    }
  }

  /** True if the underlying WebSocket is currently open. */
  get isConnected(): boolean {
    return this._ws?.readyState === WebSocket.OPEN;
  }

  /** Number of reconnect attempts made since the last successful connection. */
  get retryCount(): number {
    return this._retryCount;
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  private _buildUrl(): string {
    return `${this._wsBaseUrl}/ws/story/${this._sessionId}?token=${encodeURIComponent(this._token)}`;
  }

  private _openSocket(): void {
    const url = this._buildUrl();
    const ws = this._factory(url);
    // Receive binary frames as ArrayBuffer rather than Blob.
    ws.binaryType = "arraybuffer";
    this._ws = ws;

    ws.addEventListener("open", this._handleOpen);
    ws.addEventListener("message", this._handleMessage);
    ws.addEventListener("close", this._handleClose);
    ws.addEventListener("error", this._handleError);
  }

  private _handleOpen = (): void => {
    // Successful (re)connection — reset retry counter.
    this._retryCount = 0;
    // On reconnect, re-emit session_start so the backend reactivates the slot.
    this.send({ type: "session_start", session_id: this._sessionId });
  };

  private _handleMessage = (event: MessageEvent<ArrayBuffer | string>): void => {
    if (event.data instanceof ArrayBuffer) {
      // Binary frame — agent audio PCM.
      this._onAudioChunk?.(event.data);
      return;
    }

    // Text frame — JSON control/event message.
    let parsed: WsServerEvent;
    try {
      parsed = JSON.parse(event.data) as WsServerEvent;
    } catch {
      // Malformed JSON — ignore silently.
      return;
    }

    if (typeof parsed.type !== "string") {
      return;
    }

    const handler = this._handlers.get(parsed.type);
    if (handler) {
      handler(parsed);
    }
  };

  private _handleClose = (event: CloseEvent): void => {
    this._ws = null;

    if (this._intentionalClose) {
      return;
    }

    // Attempt auto-reconnect with exponential back-off.
    if (this._retryCount < this._maxRetries) {
      const delayMs =
        this._reconnectBaseMs * Math.pow(2, this._retryCount);
      this._retryCount += 1;
      this._reconnectTimer = setTimeout(() => {
        if (!this._intentionalClose) {
          this._openSocket();
        }
      }, delayMs);
    }
  };

  private _handleError = (_event: Event): void => {
    // `error` is always followed by `close` on a WebSocket, so reconnect
    // logic is handled entirely in `_handleClose`.
  };

  private _clearReconnectTimer(): void {
    if (this._reconnectTimer !== null) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }
}
