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

import React, { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useVoiceSession } from "@/hooks/useVoiceSession";
import { useStoryState } from "@/hooks/useStoryState";
import type {
  SafetyRewriteEvent,
  SafetyAcceptedEvent,
  PageGeneratingEvent,
  TranscriptEvent,
} from "@/lib/wsTypes";
import { StoryBook } from "@/components/StoryBook";
import { CaptionBar } from "@/components/CaptionBar";
import type { PartialCaption } from "@/components/CaptionBar";
import { VoiceButton } from "@/components/VoiceButton";

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function StoryAppPage() {
  // ── Voice session lifecycle ─────────────────────────────────────────────
  const voice = useVoiceSession();

  // Unlock browser autoplay policy on the first user gesture (mic tap).
  // A silent play()+pause() on this element marks the page as having had
  // a media-related user interaction, allowing subsequent .play() calls on
  // story narration audio elements to succeed without a NotAllowedError.
  const audioUnlockRef = useRef<HTMLAudioElement>(null);

  // ── Story state driven by the single WsClient owned by useVoiceSession ──
  const story = useStoryState(voice.wsClient);

  // ── Generation / completion state ───────────────────────────────────────
  const [isGenerating, setIsGenerating] = useState(false);
  const [storyComplete, setStoryComplete] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  // ── Safety state ────────────────────────────────────────────────────────
  const [safetyRewrite, setSafetyRewrite] = useState<SafetyRewriteEvent | null>(null);
  const [safetyAccepted, setSafetyAccepted] = useState<SafetyAcceptedEvent | null>(null);

  // ── Subscribe to page generation / safety / reconnect events ────────────
  useEffect(() => {
    const client = voice.wsClient;
    if (!client) {
      setIsGenerating(false);
      setStoryComplete(false);
      setSafetyRewrite(null);
      setSafetyAccepted(null);
      return;
    }

    client.on("page_generating", (_evt: PageGeneratingEvent) => {
      setIsGenerating(true);
      setIsProcessing(false);
    });
    client.on("page_complete", () => {
      setIsGenerating(false);
    });
    client.on("story_complete", () => {
      setStoryComplete(true);
      setIsGenerating(false);
      setIsProcessing(false);
    });
    client.on("transcript", (_evt: TranscriptEvent) => {
      setIsProcessing(false);
    });
    client.on("safety_rewrite", (evt: SafetyRewriteEvent) => {
      setSafetyRewrite(evt);
      setSafetyAccepted(null);
    });
    client.on("safety_accepted", (evt: SafetyAcceptedEvent) => {
      setSafetyAccepted(evt);
    });
  }, [voice.wsClient]);

  // ── Interrupt / feedback handlers ────────────────────────────────────────
  const handleInterrupt = useCallback(() => {
    // Unlock autoplay on the first gesture.
    if (audioUnlockRef.current) {
      audioUnlockRef.current.play().catch(() => {});
      audioUnlockRef.current.pause();
    }
    if (voice.sessionId && voice.wsClient) {
      voice.wsClient.send({
        type: "interrupt",
        page_number: story.steeringWindowPage ?? 1,
      });
    }
  }, [voice.sessionId, voice.wsClient, story.steeringWindowPage]);

  const handleFeedback = useCallback(() => {
    // Unlock autoplay on the first gesture.
    if (audioUnlockRef.current) {
      audioUnlockRef.current.play().catch(() => {});
      audioUnlockRef.current.pause();
    }
    if (!voice.sessionId) {
      voice.startSession().catch(() => { /* error shown in UI */ });
    } else if (voice.isListening) {
      voice.stopMic();
      setIsProcessing(true);
    } else {
      setIsProcessing(false);
      voice.startMic().catch(() => { /* error shown in UI */ });
    }
  }, [voice]);

  // ── Derived state ────────────────────────────────────────────────────────
  const totalPages = 5;

  const partialCaption: PartialCaption | null = useMemo(() => {
    if (!voice.isListening || !voice.liveTranscript) return null;
    return {
      turnId: `live-${voice.sessionId}`,
      role: "user" as const,
      text: voice.liveTranscript,
    };
  }, [voice.isListening, voice.liveTranscript, voice.sessionId]);

  // ── Status message shown in the center of the screen ───────────────────
  const statusMessage = (() => {
    if (voice.error) return null;
    if (!voice.sessionId) {
      return { emoji: "🎙️", text: "Tap the microphone to begin your story", sub: "" };
    }
    if (!voice.isReady && !voice.isListening) {
      return { emoji: "⏳", text: "Connecting…", sub: "Setting up your story session" };
    }
    if (voice.isReconnecting) {
      return { emoji: "🔄", text: `Reconnecting… (attempt ${voice.reconnectAttempt})`, sub: "Hang tight, getting back to your story" };
    }
    if (voice.isListening && story.pages.size === 0 && !isGenerating) {
      return { emoji: "🎤", text: "Listening…", sub: "Speak clearly — tap stop when done", showDots: true };
    }
    if (isProcessing && story.pages.size === 0 && !isGenerating) {
      return { emoji: "⏳", text: "Processing your input…", sub: "The AI is thinking about your story", showDots: true };
    }
    if (isGenerating && story.pages.size === 0) {
      return { emoji: "✨", text: "Creating your story…", sub: "Generating your personalised tale", showDots: true };
    }
    if (!voice.isListening && !isProcessing && story.pages.size === 0 && !isGenerating) {
      return { emoji: "🎙️", text: "Ready! Tap mic and tell your story", sub: "Who's the hero? Where does it happen?" };
    }
    if (storyComplete) {
      return { emoji: "📖", text: "Your story is ready!", sub: "Tap the mic to hear it again" };
    }
    return null;
  })();

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

      {/* Status overlay — shown when no story pages yet */}
      {statusMessage && story.pages.size === 0 && (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-4 px-8 text-center pointer-events-none">
          <span className="text-6xl">{statusMessage.emoji}</span>
          <p className="text-2xl font-semibold text-purple-800">{statusMessage.text}</p>
          {statusMessage.sub && (
            <p className="text-base text-purple-500 max-w-sm">{statusMessage.sub}</p>
          )}
          {/* Animated dots when listening, processing, or generating */}
          {(voice.isListening || isProcessing || isGenerating) && (
            <div className="flex gap-2 mt-2">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="inline-block w-3 h-3 rounded-full bg-purple-400 animate-bounce"
                  style={{ animationDelay: `${i * 0.15}s` }}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Listening pulse ring around mic when active */}
      {voice.isListening && (
        <div className="absolute bottom-4 left-1/2 z-20 -translate-x-1/2 flex flex-col items-center gap-2 pointer-events-none">
          <span className="text-xs font-medium text-purple-600 tracking-wide uppercase animate-pulse">
            {isGenerating ? "✨ Creating your story…" : "🎤 Listening…"}
          </span>
        </div>
      )}

      {/* StoryBook carousel — takes all remaining vertical space */}
      <div className="flex flex-1 overflow-y-auto pb-72" data-testid="story-book-wrapper">
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
        partialCaption={partialCaption}
        safetyRewrite={safetyRewrite}
        safetyAccepted={safetyAccepted}
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

      {/* Silent audio element used to unlock browser autoplay policy on first mic tap */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio ref={audioUnlockRef} src="" className="hidden" aria-hidden="true" />
    </main>
  );
}
