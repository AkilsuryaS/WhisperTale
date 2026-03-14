/**
 * app/story/page.tsx — Main story experience page.
 *
 * Wires together all hooks and UI components built in T-034 through T-040:
 *   - useVoiceSession  → session lifecycle, microphone, WebSocket connection
 *   - useStoryState    → per-page text / image / audio state + captions
 *   - StoryBook        → horizontal carousel of StoryPage cards
 *   - CaptionBar       → scrolling transcript strip
 *   - VoiceButton      → accessible mic toggle
 *
 * Reconnect recovery
 * ------------------
 * When the WebSocket reconnects (connected event fires after the first time),
 * the page calls GET /sessions/{id} and passes the response to story.hydrate()
 * so any pages that arrived while offline are restored.
 *
 * Safety state
 * ------------
 * safetyRewrite and safetyAccepted are tracked here (not in useStoryState) so
 * they can be passed directly to CaptionBar as controlled props.
 */

"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
import { useVoiceSession } from "@/hooks/useVoiceSession";
import { useStoryState } from "@/hooks/useStoryState";
import { WsClient } from "@/lib/wsClient";
import type {
  SafetyRewriteEvent,
  SafetyAcceptedEvent,
  PageGeneratingEvent,
} from "@/lib/wsTypes";
import { StoryBook } from "@/components/StoryBook";
import { CaptionBar } from "@/components/CaptionBar";
import { VoiceButton } from "@/components/VoiceButton";

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
// Page component
// ---------------------------------------------------------------------------

export default function StoryAppPage() {
  // ── Voice session lifecycle ─────────────────────────────────────────────
  const voice = useVoiceSession();

  // ── WsClient ref — created once per session, passed to useStoryState ────
  const wsClientRef = useRef<WsClient | null>(null);
  const [wsClient, setWsClient] = useState<WsClient | null>(null);

  // ── Story state accumulated from WS events ──────────────────────────────
  const story = useStoryState(wsClient);

  // ── Generation / completion state ───────────────────────────────────────
  const [isGenerating, setIsGenerating] = useState(false);
  const [storyComplete, setStoryComplete] = useState(false);

  // ── Safety state ────────────────────────────────────────────────────────
  const [safetyRewrite, setSafetyRewrite] = useState<SafetyRewriteEvent | null>(null);
  const [safetyAccepted, setSafetyAccepted] = useState<SafetyAcceptedEvent | null>(null);

  // ── Reconnect recovery guard ─────────────────────────────────────────────
  const hasConnectedOnceRef = useRef(false);

  // ── Hydrate session after reconnect ─────────────────────────────────────
  const hydrateSession = useCallback(
    async (sessionId: string) => {
      try {
        const res = await fetch(`${API_BASE}/sessions/${sessionId}`);
        if (!res.ok) return;
        const data = (await res.json()) as {
          session_id: string;
          pages?: Array<{
            page_number: number;
            status: string;
            text?: string | null;
            illustration_failed?: boolean;
            audio_failed?: boolean;
          }> | null;
        };
        story.hydrate(data);
      } catch {
        // Reconnect hydration is best-effort; swallow errors
      }
    },
    [story]
  );

  // ── Build and wire up a WsClient when the session is established ─────────
  useEffect(() => {
    if (!voice.sessionId) {
      // Session stopped or not yet started — tear down any existing client
      if (wsClientRef.current) {
        wsClientRef.current.disconnect();
        wsClientRef.current = null;
        setWsClient(null);
      }
      hasConnectedOnceRef.current = false;
      setIsGenerating(false);
      setStoryComplete(false);
      setSafetyRewrite(null);
      setSafetyAccepted(null);
      return;
    }

    // Avoid re-creating if already have a client for this session
    if (wsClientRef.current) return;

    const client = new WsClient({
      wsBaseUrl: WS_BASE,
      sessionId: voice.sessionId,
      token: "",
    });

    // Page generation tracking
    client.on("page_generating", (_evt: PageGeneratingEvent) => {
      setIsGenerating(true);
    });
    client.on("page_complete", () => {
      setIsGenerating(false);
    });
    client.on("story_complete", () => {
      setStoryComplete(true);
      setIsGenerating(false);
    });

    // Safety events
    client.on("safety_rewrite", (evt: SafetyRewriteEvent) => {
      setSafetyRewrite(evt);
      setSafetyAccepted(null);
    });
    client.on("safety_accepted", (evt: SafetyAcceptedEvent) => {
      setSafetyAccepted(evt);
    });

    // Reconnect recovery: second+ connected event means we rejoined mid-session
    client.on("connected", () => {
      if (hasConnectedOnceRef.current && voice.sessionId) {
        hydrateSession(voice.sessionId);
      } else {
        hasConnectedOnceRef.current = true;
      }
    });

    wsClientRef.current = client;
    setWsClient(client);
    client.connect();

    return () => {
      // cleanup handled via voice.sessionId becoming null (above branch)
    };
  }, [voice.sessionId, hydrateSession]);

  // ── Interrupt / feedback handlers ────────────────────────────────────────
  const handleInterrupt = useCallback(() => {
    if (voice.sessionId && wsClientRef.current) {
      wsClientRef.current.send({
        type: "interrupt",
        page_number: story.steeringWindowPage ?? 1,
      });
    }
  }, [voice.sessionId, story.steeringWindowPage]);

  const handleFeedback = useCallback(() => {
    if (!voice.isListening) {
      voice.startSession().catch(() => { /* error shown in UI */ });
    } else {
      voice.stopSession();
    }
  }, [voice]);

  // ── Derived state ────────────────────────────────────────────────────────
  const totalPages = 5;

  // ── Render ───────────────────────────────────────────────────────────────
  return (
    <main
      data-testid="story-app-page"
      className="relative flex h-screen flex-col overflow-hidden bg-story-cream"
    >
      {/* Error banner */}
      {voice.error && (
        <div
          data-testid="session-error-banner"
          role="alert"
          className="flex items-center justify-between bg-red-100 px-4 py-2 text-sm text-red-700"
        >
          <span>{voice.error.message}</span>
          <button
            className="ml-4 font-semibold underline"
            onClick={() => voice.stopSession()}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* StoryBook carousel — takes all remaining vertical space */}
      <div className="flex flex-1 overflow-hidden" data-testid="story-book-wrapper">
        <StoryBook
          pages={story.pages}
          isGenerating={isGenerating}
          storyComplete={storyComplete}
          steeringWindowOpen={story.steeringWindowOpen}
          totalPages={totalPages}
        />
      </div>

      {/* Caption bar — fixed bottom strip */}
      <CaptionBar
        captions={story.captions}
        safetyRewrite={safetyRewrite}
        safetyAccepted={safetyAccepted}
        className="pb-28"
      />

      {/* Voice button — floating above the caption bar */}
      <div className="absolute bottom-6 left-1/2 z-30 -translate-x-1/2">
        <VoiceButton
          isListening={voice.isListening}
          steeringWindowOpen={story.steeringWindowOpen}
          isGenerating={isGenerating}
          onInterrupt={handleInterrupt}
          onFeedback={handleFeedback}
        />
      </div>
    </main>
  );
}
