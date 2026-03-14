/**
 * useStoryState.test.tsx — Unit tests for useStoryState hook (T-036).
 *
 * All "done when" criteria:
 *  1. page_image_ready({ page: 1, image_url: "https://..." }) sets pages.get(1).imageUrl
 *  2. page_asset_failed({ asset_type: "illustration" }) sets pages.get(N).illustrationFailed = true
 *  3. hydrate(session) populates pages from existing session data
 *
 * Additional tests:
 *  - page_text_ready sets pages.get(N).text and status = "text_ready"
 *  - page_audio_ready sets pages.get(N).audioUrl
 *  - page_complete sets status = "complete" and persists illustration/audio_failed flags
 *  - page_asset_failed with asset_type="narration" sets audioFailed = true
 *  - transcript final events are added to captions
 *  - partial transcript events (is_final=false) are NOT added to captions
 *  - steering_window_open sets steeringWindowOpen=true and steeringWindowPage
 *  - steering_window_closed sets steeringWindowOpen=false
 *  - hydrate skips pages array when null/empty
 *  - hydrate merges with existing state (doesn't overwrite unrelated fields)
 *  - reset() clears all state
 *  - passing null client is safe (no subscriptions, no crash)
 *  - multiple events for the same page accumulate correctly
 */

import { renderHook, act } from "@testing-library/react";
import { useStoryState } from "../useStoryState";
import type { WsClient } from "@/lib/wsClient";
import type {
  PageTextReadyEvent,
  PageImageReadyEvent,
  PageAudioReadyEvent,
  PageAssetFailedEvent,
  PageCompleteEvent,
  TranscriptEvent,
  SteeringWindowOpenEvent,
  SteeringWindowClosedEvent,
} from "@/lib/wsTypes";

// ---------------------------------------------------------------------------
// Minimal mock WsClient
// ---------------------------------------------------------------------------

type HandlerMap = Record<string, (payload: unknown) => void>;

class MockWsClient {
  private _handlers: HandlerMap = {};

  on(type: string, handler: (payload: unknown) => void): this {
    this._handlers[type] = handler;
    return this;
  }

  emit(type: string, payload: unknown): void {
    this._handlers[type]?.(payload);
  }
}

function makeMockClient(): MockWsClient {
  return new MockWsClient();
}

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

function buildHook(client: MockWsClient | null = null) {
  return renderHook(() =>
    useStoryState(client as unknown as WsClient | null)
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useStoryState — initial state", () => {
  it("starts with empty pages map", () => {
    const { result } = buildHook();
    expect(result.current.pages.size).toBe(0);
  });

  it("starts with empty captions array", () => {
    const { result } = buildHook();
    expect(result.current.captions).toHaveLength(0);
  });

  it("starts with steeringWindowOpen=false", () => {
    const { result } = buildHook();
    expect(result.current.steeringWindowOpen).toBe(false);
  });

  it("starts with steeringWindowPage=null", () => {
    const { result } = buildHook();
    expect(result.current.steeringWindowPage).toBeNull();
  });

  it("null client is safe — no crash", () => {
    expect(() => buildHook(null)).not.toThrow();
  });
});

describe("useStoryState — done-when: page_image_ready sets pages.get(1).imageUrl", () => {
  it("page_image_ready sets imageUrl on the correct page", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    const evt: PageImageReadyEvent = {
      type: "page_image_ready",
      page: 1,
      asset_id: "asset-001",
      image_url: "https://storage.googleapis.com/bucket/img1.png",
      signed_url_expires_at: "2026-01-01T00:00:00Z",
    };

    act(() => { client.emit("page_image_ready", evt); });

    expect(result.current.pages.get(1)?.imageUrl).toBe(
      "https://storage.googleapis.com/bucket/img1.png"
    );
  });

  it("page_image_ready creates a new page entry if one didn't exist", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_image_ready", {
        type: "page_image_ready",
        page: 3,
        asset_id: "asset-003",
        image_url: "https://example.com/img3.png",
        signed_url_expires_at: "2026-01-01T00:00:00Z",
      } as PageImageReadyEvent);
    });

    const page = result.current.pages.get(3);
    expect(page).toBeDefined();
    expect(page?.imageUrl).toBe("https://example.com/img3.png");
  });

  it("page_image_ready for page 2 does not affect page 1", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_image_ready", {
        type: "page_image_ready",
        page: 2,
        asset_id: "a",
        image_url: "https://example.com/img2.png",
        signed_url_expires_at: "2026-01-01T00:00:00Z",
      } as PageImageReadyEvent);
    });

    expect(result.current.pages.get(1)).toBeUndefined();
    expect(result.current.pages.get(2)?.imageUrl).toBe("https://example.com/img2.png");
  });
});

describe("useStoryState — done-when: page_asset_failed sets illustrationFailed=true", () => {
  it("page_asset_failed with illustration sets illustrationFailed=true", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_asset_failed", {
        type: "page_asset_failed",
        page: 2,
        asset_type: "illustration",
        asset_id: null,
        reason: "Imagen quota exceeded",
      } as PageAssetFailedEvent);
    });

    expect(result.current.pages.get(2)?.illustrationFailed).toBe(true);
    expect(result.current.pages.get(2)?.audioFailed).toBe(false);
  });

  it("page_asset_failed with narration sets audioFailed=true", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_asset_failed", {
        type: "page_asset_failed",
        page: 4,
        asset_type: "narration",
        asset_id: null,
        reason: "TTS error",
      } as PageAssetFailedEvent);
    });

    expect(result.current.pages.get(4)?.audioFailed).toBe(true);
    expect(result.current.pages.get(4)?.illustrationFailed).toBe(false);
  });
});

describe("useStoryState — done-when: hydrate(session) populates pages", () => {
  it("hydrate populates pages from session data", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      result.current.hydrate({
        session_id: "sess-001",
        pages: [
          { page_number: 1, status: "complete", text: "Once upon a time...", illustration_failed: false, audio_failed: false },
          { page_number: 2, status: "text_ready", text: "Then the bunny hopped." },
        ],
      });
    });

    expect(result.current.pages.get(1)?.text).toBe("Once upon a time...");
    expect(result.current.pages.get(1)?.status).toBe("complete");
    expect(result.current.pages.get(2)?.text).toBe("Then the bunny hopped.");
    expect(result.current.pages.get(2)?.status).toBe("text_ready");
  });

  it("hydrate with null pages is a no-op", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      result.current.hydrate({ session_id: "sess-001", pages: null });
    });

    expect(result.current.pages.size).toBe(0);
  });

  it("hydrate with empty pages array is a no-op", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      result.current.hydrate({ session_id: "sess-001", pages: [] });
    });

    expect(result.current.pages.size).toBe(0);
  });

  it("hydrate merges with existing image/audio state set by WS events", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    // First receive image URL via WS
    act(() => {
      client.emit("page_image_ready", {
        type: "page_image_ready",
        page: 1,
        asset_id: "a1",
        image_url: "https://example.com/img.png",
        signed_url_expires_at: "2026-01-01T00:00:00Z",
      } as PageImageReadyEvent);
    });

    // Then hydrate — should not overwrite the imageUrl
    act(() => {
      result.current.hydrate({
        session_id: "sess-001",
        pages: [{ page_number: 1, status: "complete", text: "Story text." }],
      });
    });

    const page = result.current.pages.get(1);
    expect(page?.imageUrl).toBe("https://example.com/img.png"); // preserved
    expect(page?.text).toBe("Story text.");                       // from hydrate
    expect(page?.status).toBe("complete");                        // from hydrate
  });
});

describe("useStoryState — page_text_ready event", () => {
  it("page_text_ready sets text and status=text_ready", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_text_ready", {
        type: "page_text_ready",
        page: 1,
        text: "Once upon a time in a sunny meadow...",
      } as PageTextReadyEvent);
    });

    const page = result.current.pages.get(1);
    expect(page?.text).toBe("Once upon a time in a sunny meadow...");
    expect(page?.status).toBe("text_ready");
  });
});

describe("useStoryState — page_audio_ready event", () => {
  it("page_audio_ready sets audioUrl", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_audio_ready", {
        type: "page_audio_ready",
        page: 1,
        asset_id: "aud-001",
        audio_url: "https://storage.googleapis.com/bucket/audio1.mp3",
        signed_url_expires_at: "2026-01-01T00:00:00Z",
      } as PageAudioReadyEvent);
    });

    expect(result.current.pages.get(1)?.audioUrl).toBe(
      "https://storage.googleapis.com/bucket/audio1.mp3"
    );
  });
});

describe("useStoryState — page_complete event", () => {
  it("page_complete sets status=complete", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_complete", {
        type: "page_complete",
        page: 1,
        illustration_failed: false,
        audio_failed: false,
        generated_at: "2026-03-13T12:00:00Z",
      } as PageCompleteEvent);
    });

    expect(result.current.pages.get(1)?.status).toBe("complete");
  });

  it("page_complete persists illustration_failed flag", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_complete", {
        type: "page_complete",
        page: 2,
        illustration_failed: true,
        audio_failed: false,
        generated_at: "2026-03-13T12:00:00Z",
      } as PageCompleteEvent);
    });

    expect(result.current.pages.get(2)?.illustrationFailed).toBe(true);
    expect(result.current.pages.get(2)?.audioFailed).toBe(false);
  });
});

describe("useStoryState — transcript events → captions", () => {
  it("final transcript is added to captions", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("transcript", {
        type: "transcript",
        turn_id: "turn-1",
        role: "user",
        text: "Make it sillier!",
        is_final: true,
        phase: "steering",
      } as TranscriptEvent);
    });

    expect(result.current.captions).toHaveLength(1);
    expect(result.current.captions[0].text).toBe("Make it sillier!");
    expect(result.current.captions[0].role).toBe("user");
    expect(result.current.captions[0].turnId).toBe("turn-1");
  });

  it("partial transcript (is_final=false) is NOT added to captions", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("transcript", {
        type: "transcript",
        turn_id: "turn-2",
        role: "user",
        text: "Make it s...",
        is_final: false,
        phase: "steering",
      } as TranscriptEvent);
    });

    expect(result.current.captions).toHaveLength(0);
  });

  it("multiple final transcripts accumulate in order", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("transcript", {
        type: "transcript",
        turn_id: "t1",
        role: "agent",
        text: "Great!",
        is_final: true,
        phase: "setup",
      } as TranscriptEvent);
      client.emit("transcript", {
        type: "transcript",
        turn_id: "t2",
        role: "user",
        text: "Tell me more.",
        is_final: true,
        phase: "setup",
      } as TranscriptEvent);
    });

    expect(result.current.captions).toHaveLength(2);
    expect(result.current.captions[0].role).toBe("agent");
    expect(result.current.captions[1].role).toBe("user");
  });
});

describe("useStoryState — steering window events", () => {
  it("steering_window_open sets steeringWindowOpen=true and steeringWindowPage", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("steering_window_open", {
        type: "steering_window_open",
        page_just_completed: 2,
        timeout_ms: 10000,
      } as SteeringWindowOpenEvent);
    });

    expect(result.current.steeringWindowOpen).toBe(true);
    expect(result.current.steeringWindowPage).toBe(2);
  });

  it("steering_window_closed sets steeringWindowOpen=false", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("steering_window_open", {
        type: "steering_window_open",
        page_just_completed: 3,
        timeout_ms: 10000,
      } as SteeringWindowOpenEvent);
    });

    act(() => {
      client.emit("steering_window_closed", {
        type: "steering_window_closed",
        reason: "timeout",
      } as SteeringWindowClosedEvent);
    });

    expect(result.current.steeringWindowOpen).toBe(false);
  });
});

describe("useStoryState — multiple events on same page accumulate", () => {
  it("text + image + audio all accumulate on page 1", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_text_ready", {
        type: "page_text_ready",
        page: 1,
        text: "Story page 1 text.",
      } as PageTextReadyEvent);
      client.emit("page_image_ready", {
        type: "page_image_ready",
        page: 1,
        asset_id: "img-1",
        image_url: "https://img.example.com/1.png",
        signed_url_expires_at: "2026-01-01T00:00:00Z",
      } as PageImageReadyEvent);
      client.emit("page_audio_ready", {
        type: "page_audio_ready",
        page: 1,
        asset_id: "aud-1",
        audio_url: "https://audio.example.com/1.mp3",
        signed_url_expires_at: "2026-01-01T00:00:00Z",
      } as PageAudioReadyEvent);
    });

    const page = result.current.pages.get(1);
    expect(page?.text).toBe("Story page 1 text.");
    expect(page?.imageUrl).toBe("https://img.example.com/1.png");
    expect(page?.audioUrl).toBe("https://audio.example.com/1.mp3");
  });
});

describe("useStoryState — reset()", () => {
  it("reset() clears all pages", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("page_text_ready", {
        type: "page_text_ready",
        page: 1,
        text: "Some text.",
      } as PageTextReadyEvent);
    });

    expect(result.current.pages.size).toBe(1);

    act(() => { result.current.reset(); });

    expect(result.current.pages.size).toBe(0);
  });

  it("reset() clears captions", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("transcript", {
        type: "transcript",
        turn_id: "t1",
        role: "user",
        text: "Hi",
        is_final: true,
        phase: "setup",
      } as TranscriptEvent);
    });

    act(() => { result.current.reset(); });

    expect(result.current.captions).toHaveLength(0);
  });

  it("reset() closes the steering window", () => {
    const client = makeMockClient();
    const { result } = buildHook(client);

    act(() => {
      client.emit("steering_window_open", {
        type: "steering_window_open",
        page_just_completed: 1,
        timeout_ms: 10000,
      } as SteeringWindowOpenEvent);
    });

    act(() => { result.current.reset(); });

    expect(result.current.steeringWindowOpen).toBe(false);
    expect(result.current.steeringWindowPage).toBeNull();
  });
});
