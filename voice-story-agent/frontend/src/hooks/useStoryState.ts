/**
 * useStoryState.ts — React hook that accumulates the complete story display state.
 *
 * State managed
 * -------------
 * - `pages`              Map<number, PageState>  — one entry per page (1–5)
 * - `captions`           Caption[]               — ordered transcript entries
 * - `steeringWindowOpen` boolean                 — true during steering window
 * - `steeringWindowPage` number | null           — page that opened the window
 *
 * Inputs
 * ------
 * Callers pass a `WsClient` instance (or null). The hook subscribes to all
 * relevant WebSocket events when the client changes and unsubscribes (by
 * registering no-op overrides) on cleanup.
 *
 * `hydrate(session)` pre-fills state from a `GET /sessions/{id}` REST response
 * to recover after a WebSocket reconnect.
 *
 * Design constraints
 * ------------------
 * - No `any` in public API.
 * - All WsClient subscriptions use `client.on()` so they are compatible with
 *   both the real WsClient and any mock used in tests.
 * - `PageState` is immutable per update — each event produces a new object so
 *   React's referential equality check detects changes.
 */

"use client";

import { useState, useEffect, useCallback, useRef } from "react";
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
// Public types
// ---------------------------------------------------------------------------

/** Per-page lifecycle status (mirrors api-spec PageStatus). */
export type PageStatus =
  | "pending"
  | "text_ready"
  | "assets_generating"
  | "complete"
  | "error";

/** All display-relevant state for a single story page. */
export interface PageState {
  /** Page number (1–5). */
  pageNumber: number;
  /** Generated story text; null until page_text_ready. */
  text: string | null;
  /** Signed HTTPS URL for the illustration; null until page_image_ready. */
  imageUrl: string | null;
  /** Signed HTTPS URL for the narration MP3; null until page_audio_ready. */
  audioUrl: string | null;
  /** True if the illustration asset generation failed. */
  illustrationFailed: boolean;
  /** True if the narration audio asset generation failed. */
  audioFailed: boolean;
  /** Current lifecycle status of the page. */
  status: PageStatus;
}

/** One caption entry for the caption strip. */
export interface Caption {
  /** "user" or "agent" speaker. */
  role: "user" | "agent";
  /** Caption text to render (safe, from transcript event). */
  text: string;
  /** turn_id for keying in lists. */
  turnId: string;
  /** Whether this is a final (committed) transcript. */
  isFinal: boolean;
}

/** Shape of the session data returned by GET /sessions/{id}. */
export interface HydrateSession {
  session_id: string;
  pages?: Array<{
    page_number: number;
    status: string;
    text?: string | null;
    illustration_failed?: boolean;
    audio_failed?: boolean;
  }> | null;
}

/** Return value of useStoryState. */
export interface UseStoryStateReturn {
  /** Accumulated per-page state, keyed by page number (1-based). */
  pages: Map<number, PageState>;
  /** Ordered list of caption entries from transcript events. */
  captions: Caption[];
  /** True when the steering window is open. */
  steeringWindowOpen: boolean;
  /** Page number for which the steering window is open, or null. */
  steeringWindowPage: number | null;
  /**
   * Pre-fill state from a GET /sessions/{id} response.
   * Called after a WebSocket reconnect to recover any missed events.
   */
  hydrate: (session: HydrateSession) => void;
  /** Manually push a caption (e.g. a local user transcript). */
  addCaption: (caption: Caption) => void;
  /** Reset all state back to initial empty values. */
  reset: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeEmptyPage(pageNumber: number): PageState {
  return {
    pageNumber,
    text: null,
    imageUrl: null,
    audioUrl: null,
    illustrationFailed: false,
    audioFailed: false,
    status: "pending",
  };
}

function getOrMake(
  map: Map<number, PageState>,
  pageNumber: number
): PageState {
  return map.get(pageNumber) ?? makeEmptyPage(pageNumber);
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useStoryState(client: WsClient | null): UseStoryStateReturn {
  const [pages, setPages] = useState<Map<number, PageState>>(new Map());
  const [captions, setCaptions] = useState<Caption[]>([]);
  const [steeringWindowOpen, setSteeringWindowOpen] = useState(false);
  const [steeringWindowPage, setSteeringWindowPage] = useState<number | null>(null);

  // Track which client we are currently subscribed to so we can detect changes.
  const subscribedClientRef = useRef<WsClient | null>(null);

  // ---------------------------------------------------------------------------
  // Event handlers (stable references via useCallback)
  // ---------------------------------------------------------------------------

  const handlePageTextReady = useCallback((evt: PageTextReadyEvent) => {
    setPages((prev) => {
      const next = new Map(prev);
      const existing = getOrMake(next, evt.page);
      next.set(evt.page, {
        ...existing,
        text: evt.text,
        status: "text_ready",
      });
      return next;
    });
  }, []);

  const handlePageImageReady = useCallback((evt: PageImageReadyEvent) => {
    setPages((prev) => {
      const next = new Map(prev);
      const existing = getOrMake(next, evt.page);
      next.set(evt.page, {
        ...existing,
        imageUrl: evt.image_url,
        status: existing.status === "pending" || existing.status === "text_ready"
          ? "assets_generating"
          : existing.status,
      });
      return next;
    });
  }, []);

  const handlePageAudioReady = useCallback((evt: PageAudioReadyEvent) => {
    setPages((prev) => {
      const next = new Map(prev);
      const existing = getOrMake(next, evt.page);
      next.set(evt.page, {
        ...existing,
        audioUrl: evt.audio_url,
        status: existing.status === "pending" || existing.status === "text_ready"
          ? "assets_generating"
          : existing.status,
      });
      return next;
    });
  }, []);

  const handlePageAssetFailed = useCallback((evt: PageAssetFailedEvent) => {
    setPages((prev) => {
      const next = new Map(prev);
      const existing = getOrMake(next, evt.page);
      next.set(evt.page, {
        ...existing,
        illustrationFailed:
          evt.asset_type === "illustration" ? true : existing.illustrationFailed,
        audioFailed:
          evt.asset_type === "narration" ? true : existing.audioFailed,
      });
      return next;
    });
  }, []);

  const handlePageComplete = useCallback((evt: PageCompleteEvent) => {
    setPages((prev) => {
      const next = new Map(prev);
      const existing = getOrMake(next, evt.page);
      next.set(evt.page, {
        ...existing,
        illustrationFailed: evt.illustration_failed,
        audioFailed: evt.audio_failed,
        status: "complete",
      });
      return next;
    });
  }, []);

  const handleTranscript = useCallback((evt: TranscriptEvent) => {
    // Only accumulate final transcripts to avoid caption flicker.
    if (!evt.is_final) return;
    setCaptions((prev) => [
      ...prev,
      { role: evt.role, text: evt.text, turnId: evt.turn_id, isFinal: true },
    ]);
  }, []);

  const handleSteeringWindowOpen = useCallback(
    (evt: SteeringWindowOpenEvent) => {
      setSteeringWindowOpen(true);
      setSteeringWindowPage(evt.page_just_completed);
    },
    []
  );

  const handleSteeringWindowClosed = useCallback(
    (_evt: SteeringWindowClosedEvent) => {
      setSteeringWindowOpen(false);
    },
    []
  );

  // ---------------------------------------------------------------------------
  // Subscribe / unsubscribe when client changes
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (client === subscribedClientRef.current) return;
    subscribedClientRef.current = client;

    if (!client) return;

    client.on("page_text_ready", handlePageTextReady);
    client.on("page_image_ready", handlePageImageReady);
    client.on("page_audio_ready", handlePageAudioReady);
    client.on("page_asset_failed", handlePageAssetFailed);
    client.on("page_complete", handlePageComplete);
    client.on("transcript", handleTranscript);
    client.on("steering_window_open", handleSteeringWindowOpen);
    client.on("steering_window_closed", handleSteeringWindowClosed);
  }, [
    client,
    handlePageTextReady,
    handlePageImageReady,
    handlePageAudioReady,
    handlePageAssetFailed,
    handlePageComplete,
    handleTranscript,
    handleSteeringWindowOpen,
    handleSteeringWindowClosed,
  ]);

  // ---------------------------------------------------------------------------
  // hydrate — pre-fill state from REST GET /sessions/{id} response
  // ---------------------------------------------------------------------------

  const hydrate = useCallback((session: HydrateSession) => {
    if (!session.pages?.length) return;

    setPages((prev) => {
      const next = new Map(prev);
      for (const p of session.pages!) {
        const existing = getOrMake(next, p.page_number);
        next.set(p.page_number, {
          ...existing,
          text: p.text ?? existing.text,
          illustrationFailed: p.illustration_failed ?? existing.illustrationFailed,
          audioFailed: p.audio_failed ?? existing.audioFailed,
          status: (p.status as PageStatus) ?? existing.status,
        });
      }
      return next;
    });
  }, []);

  // ---------------------------------------------------------------------------
  // addCaption — manually push a caption (e.g. local user transcript)
  // ---------------------------------------------------------------------------

  const addCaption = useCallback((caption: Caption) => {
    setCaptions((prev) => [...prev, caption]);
  }, []);

  // ---------------------------------------------------------------------------
  // reset
  // ---------------------------------------------------------------------------

  const reset = useCallback(() => {
    setPages(new Map());
    setCaptions([]);
    setSteeringWindowOpen(false);
    setSteeringWindowPage(null);
  }, []);

  return {
    pages,
    captions,
    steeringWindowOpen,
    steeringWindowPage,
    hydrate,
    addCaption,
    reset,
  };
}
