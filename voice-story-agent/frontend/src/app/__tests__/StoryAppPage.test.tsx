/**
 * StoryAppPage.test.tsx — Unit tests for the assembled story page (T-041).
 *
 * All "done when" criteria:
 *  1. npm run build succeeds — verified separately (build step)
 *  2. Page renders without runtime errors
 *  3. Reconnect recovery calls GET /sessions/{id} and hydrates story state
 *
 * Strategy
 * --------
 * The page internally creates a WsClient and calls useVoiceSession, so both
 * are module-mocked.  useStoryState is also mocked to give test control over
 * the full story display state.
 */

import React from "react";
import { render, screen, act, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

// ---------------------------------------------------------------------------
// Mock useVoiceSession
// ---------------------------------------------------------------------------

type EventMap = Record<string, (payload: unknown) => void>;

/** Controllable mock WsClient — shared across module boundary via closure. */
class MockWsClient {
  private _handlers: EventMap = {};
  connectCalled = false;
  disconnectCalled = false;
  isConnected = true;
  sentMessages: unknown[] = [];

  on(type: string, handler: (payload: unknown) => void): this {
    this._handlers[type] = handler;
    return this;
  }
  connect(): void { this.connectCalled = true; }
  disconnect(): void { this.disconnectCalled = true; }
  send(msg: unknown): void { this.sentMessages.push(msg); }
  sendAudio(_buf: ArrayBuffer): void { /* no-op */ }

  emit(type: string, payload: unknown): void {
    this._handlers[type]?.(payload);
  }
}

let mockWsClientInstance: MockWsClient | null = null;

jest.mock("@/lib/wsClient", () => {
  return {
    WsClient: jest.fn().mockImplementation(() => {
      mockWsClientInstance = new MockWsClient();
      return mockWsClientInstance;
    }),
  };
});

// ---------------------------------------------------------------------------
// Mock useVoiceSession
// ---------------------------------------------------------------------------

const mockVoiceSession = {
  sessionId: null as string | null,
  sessionStatus: null as string | null,
  isListening: false,
  error: null as { code: string; message: string } | null,
  startSession: jest.fn().mockResolvedValue(undefined),
  stopSession: jest.fn(),
};

jest.mock("@/hooks/useVoiceSession", () => ({
  useVoiceSession: () => mockVoiceSession,
}));

// ---------------------------------------------------------------------------
// Mock useStoryState
// ---------------------------------------------------------------------------

const mockStoryState = {
  pages: new Map(),
  captions: [] as unknown[],
  steeringWindowOpen: false,
  steeringWindowPage: null as number | null,
  hydrate: jest.fn(),
  reset: jest.fn(),
};

jest.mock("@/hooks/useStoryState", () => ({
  useStoryState: () => mockStoryState,
}));

// ---------------------------------------------------------------------------
// Import page AFTER mocks are set up
// ---------------------------------------------------------------------------

import StoryAppPage from "../story/page";

// ---------------------------------------------------------------------------
// Global fetch mock
// ---------------------------------------------------------------------------

const mockFetch = jest.fn();
global.fetch = mockFetch as unknown as typeof fetch;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function resetMocks() {
  mockWsClientInstance = null;
  mockVoiceSession.sessionId = null;
  mockVoiceSession.sessionStatus = null;
  mockVoiceSession.isListening = false;
  mockVoiceSession.error = null;
  mockVoiceSession.startSession = jest.fn().mockResolvedValue(undefined);
  mockVoiceSession.stopSession = jest.fn();
  mockStoryState.pages = new Map();
  mockStoryState.captions = [];
  mockStoryState.steeringWindowOpen = false;
  mockStoryState.steeringWindowPage = null;
  mockStoryState.hydrate = jest.fn();
  mockStoryState.reset = jest.fn();
  mockFetch.mockReset();
}

beforeEach(() => {
  resetMocks();
});

// ---------------------------------------------------------------------------
// 1. Page renders without runtime errors (done-when #2)
// ---------------------------------------------------------------------------

describe("StoryAppPage — renders without runtime errors (done-when)", () => {
  it("renders the story-app-page container", () => {
    render(<StoryAppPage />);
    expect(screen.getByTestId("story-app-page")).toBeInTheDocument();
  });

  it("renders the story-book-wrapper", () => {
    render(<StoryAppPage />);
    expect(screen.getByTestId("story-book-wrapper")).toBeInTheDocument();
  });

  it("renders VoiceButton component", () => {
    render(<StoryAppPage />);
    expect(screen.getByTestId("voice-button")).toBeInTheDocument();
  });

  it("renders CaptionBar component", () => {
    render(<StoryAppPage />);
    expect(screen.getByTestId("caption-bar")).toBeInTheDocument();
  });

  it("renders StoryBook carousel", () => {
    render(<StoryAppPage />);
    expect(screen.getByTestId("story-book")).toBeInTheDocument();
  });

  it("does not render error banner when no error", () => {
    render(<StoryAppPage />);
    expect(screen.queryByTestId("session-error-banner")).not.toBeInTheDocument();
  });

  it("renders error banner when voice.error is set", () => {
    mockVoiceSession.error = { code: "mic_permission_denied", message: "Mic denied" };
    render(<StoryAppPage />);
    expect(screen.getByTestId("session-error-banner")).toBeInTheDocument();
    expect(screen.getByTestId("session-error-banner")).toHaveTextContent("Mic denied");
  });

  it("VoiceButton reflects isListening from useVoiceSession", () => {
    mockVoiceSession.isListening = true;
    render(<StoryAppPage />);
    const btn = screen.getByTestId("voice-button");
    expect(btn).toHaveAttribute("aria-pressed", "true");
  });
});

// ---------------------------------------------------------------------------
// 2. WsClient is created and connected when sessionId is available (done-when #2 supplemental)
// ---------------------------------------------------------------------------

describe("StoryAppPage — WsClient wiring", () => {
  it("creates and connects WsClient when sessionId is set", async () => {
    mockVoiceSession.sessionId = "sess-abc";
    render(<StoryAppPage />);
    await waitFor(() => {
      expect(mockWsClientInstance).not.toBeNull();
      expect(mockWsClientInstance!.connectCalled).toBe(true);
    });
  });

  it("does not create WsClient when sessionId is null", () => {
    mockVoiceSession.sessionId = null;
    render(<StoryAppPage />);
    // WsClient mock not called if sessionId is null on first render
    // (It may have been called 0 times since session is null from start)
    expect(mockWsClientInstance).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 3. Reconnect recovery calls GET /sessions/{id} and hydrates story state (done-when #3)
// ---------------------------------------------------------------------------

describe("StoryAppPage — reconnect recovery (done-when)", () => {
  it("calls GET /sessions/{id} and hydrates on second connected event", async () => {
    mockVoiceSession.sessionId = "sess-reconnect-1";

    const hydrateData = {
      session_id: "sess-reconnect-1",
      pages: [
        { page_number: 1, status: "complete", text: "Once upon a time…" },
        { page_number: 2, status: "complete", text: "Then something happened…" },
      ],
    };

    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(hydrateData),
    } as unknown as Response);

    render(<StoryAppPage />);

    // Wait for WsClient to be created and connected
    await waitFor(() => {
      expect(mockWsClientInstance).not.toBeNull();
    });

    // First connected event — marks hasConnectedOnce, does NOT hydrate
    act(() => {
      mockWsClientInstance!.emit("connected", {
        type: "connected",
        session_id: "sess-reconnect-1",
        session_status: "setup",
      });
    });

    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockStoryState.hydrate).not.toHaveBeenCalled();

    // Second connected event — triggers reconnect hydration
    await act(async () => {
      mockWsClientInstance!.emit("connected", {
        type: "connected",
        session_id: "sess-reconnect-1",
        session_status: "generating",
      });
    });

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("/sessions/sess-reconnect-1")
      );
    });

    await waitFor(() => {
      expect(mockStoryState.hydrate).toHaveBeenCalledWith(hydrateData);
    });
  });

  it("does NOT hydrate on first connected event (initial connection)", async () => {
    mockVoiceSession.sessionId = "sess-initial-1";

    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ session_id: "sess-initial-1", pages: [] }),
    } as unknown as Response);

    render(<StoryAppPage />);

    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    // Only ONE connected event — should not fetch
    act(() => {
      mockWsClientInstance!.emit("connected", {
        type: "connected",
        session_id: "sess-initial-1",
        session_status: "setup",
      });
    });

    // Give async code time to run
    await new Promise((r) => setTimeout(r, 50));

    expect(mockFetch).not.toHaveBeenCalled();
    expect(mockStoryState.hydrate).not.toHaveBeenCalled();
  });

  it("swallows fetch errors during reconnect hydration (does not throw)", async () => {
    mockVoiceSession.sessionId = "sess-err-1";

    mockFetch.mockRejectedValue(new Error("Network error"));

    render(<StoryAppPage />);
    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    // First connected
    act(() => {
      mockWsClientInstance!.emit("connected", {
        type: "connected",
        session_id: "sess-err-1",
        session_status: "setup",
      });
    });

    // Second connected — fetch throws, should not propagate
    await expect(
      act(async () => {
        mockWsClientInstance!.emit("connected", {
          type: "connected",
          session_id: "sess-err-1",
          session_status: "generating",
        });
        await new Promise((r) => setTimeout(r, 50));
      })
    ).resolves.not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// 4. isGenerating / storyComplete derived from WS events
// ---------------------------------------------------------------------------

describe("StoryAppPage — generation state from WS events", () => {
  it("shows HoldAnimation after page_generating event", async () => {
    mockVoiceSession.sessionId = "sess-gen-1";
    render(<StoryAppPage />);

    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    act(() => {
      mockWsClientInstance!.emit("page_generating", {
        type: "page_generating",
        page: 1,
        voice_commands_applied: [],
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("hold-animation")).toBeInTheDocument();
    });
  });

  it("hides HoldAnimation after page_complete event", async () => {
    mockVoiceSession.sessionId = "sess-gen-2";
    render(<StoryAppPage />);

    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    act(() => {
      mockWsClientInstance!.emit("page_generating", {
        type: "page_generating",
        page: 1,
        voice_commands_applied: [],
      });
    });

    await waitFor(() => expect(screen.getByTestId("hold-animation")).toBeInTheDocument());

    act(() => {
      mockWsClientInstance!.emit("page_complete", {
        type: "page_complete",
        page: 1,
        illustration_failed: false,
        audio_failed: false,
        generated_at: new Date().toISOString(),
      });
    });

    await waitFor(() => {
      expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();
    });
  });

  it("shows closing card after story_complete event", async () => {
    mockVoiceSession.sessionId = "sess-complete-1";
    render(<StoryAppPage />);

    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    act(() => {
      mockWsClientInstance!.emit("story_complete", {
        type: "story_complete",
        session_id: "sess-complete-1",
        page_count: 5,
        pages_with_failures: [],
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("story-closing-card")).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// 5. Safety events wired to CaptionBar
// ---------------------------------------------------------------------------

describe("StoryAppPage — safety state wired to CaptionBar", () => {
  it("shows safety rewrite amber card after safety_rewrite event", async () => {
    mockVoiceSession.sessionId = "sess-safety-1";
    render(<StoryAppPage />);

    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    act(() => {
      mockWsClientInstance!.emit("safety_rewrite", {
        type: "safety_rewrite",
        decision_id: "dec-1",
        turn_id: "t-1",
        detected_category: "physical_harm",
        proposed_rewrite: "A friendly snowball fight instead!",
        phase: "steering",
      });
    });

    await waitFor(() => {
      expect(screen.getByTestId("safety-rewrite-card")).toBeInTheDocument();
    });
  });

  it("replaces amber card with green card after safety_accepted event", async () => {
    mockVoiceSession.sessionId = "sess-safety-2";
    render(<StoryAppPage />);

    await waitFor(() => expect(mockWsClientInstance).not.toBeNull());

    act(() => {
      mockWsClientInstance!.emit("safety_rewrite", {
        type: "safety_rewrite",
        decision_id: "dec-2",
        turn_id: "t-2",
        detected_category: "physical_harm",
        proposed_rewrite: "Something nicer!",
        phase: "steering",
      });
    });

    await waitFor(() => expect(screen.getByTestId("safety-rewrite-card")).toBeInTheDocument());

    act(() => {
      mockWsClientInstance!.emit("safety_accepted", {
        type: "safety_accepted",
        decision_id: "dec-2",
        final_premise: "Something nicer!",
        exclusion_added: null,
      });
    });

    await waitFor(() => {
      expect(screen.queryByTestId("safety-rewrite-card")).not.toBeInTheDocument();
      expect(screen.getByTestId("safety-accepted-card")).toBeInTheDocument();
    });
  });
});
