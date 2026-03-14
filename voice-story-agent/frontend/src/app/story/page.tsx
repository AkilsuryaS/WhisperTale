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
 * Bottom-bar behaviour
 * --------------------
 * While story pages are visible the bottom bar (CaptionBar + large VoiceButton)
 * is collapsed out of the way so the full story card is unobstructed.  Only a
 * small floating mic FAB stays visible in the bottom-right corner.
 *
 * Tapping the FAB (or the large button during setup) expands the bar.  The bar
 * auto-collapses again once isListening and isProcessing both return to false.
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
// Small mic icon — used by the floating FAB in reading mode
// ---------------------------------------------------------------------------

function SmallMicIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className="h-6 w-6"
    >
      <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4Z" />
      <path d="M19 10a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.93V19H9a1 1 0 0 0 0 2h6a1 1 0 0 0 0-2h-2v-2.07A7 7 0 0 0 19 10Z" />
    </svg>
  );
}

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

  // ── Bottom-bar expanded/collapsed state ─────────────────────────────────
  // true  → full CaptionBar + large VoiceButton visible
  // false → bar hidden; only small FAB shown (reading mode, pages present)
  const [barExpanded, setBarExpanded] = useState(true);

  // Expand the bar whenever the mic becomes active so the stop button is
  // always visible.  Without this, the auto-collapse effect below can hide
  // the bar before startMic() finishes and sets isListening=true.
  useEffect(() => {
    if (voice.isListening) {
      setBarExpanded(true);
    }
  }, [voice.isListening]);

  // Auto-collapse the bar once the user stops speaking and pages are visible.
  useEffect(() => {
    if (!voice.isListening && !isProcessing && story.pages.size > 0) {
      setBarExpanded(false);
    }
  }, [voice.isListening, isProcessing, story.pages.size]);

  // Keep the bar open while there are no pages yet (setup phase).
  useEffect(() => {
    if (story.pages.size === 0) {
      setBarExpanded(true);
    }
  }, [story.pages.size]);

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
    // Expand the bottom bar and unlock autoplay on every mic tap.
    setBarExpanded(true);
    if (audioUnlockRef.current) {
      audioUnlockRef.current.play().catch(() => {});
      audioUnlockRef.current.pause();
    }
    if (!voice.sessionId) {
      voice.startSession().catch(() => { /* error shown in UI */ });
    } else if (voice.isListening) {
      const submitted = voice.stopMic();
      // Only show "Processing..." when we actually submitted text.
      setIsProcessing(Boolean(submitted.trim()));
    } else {
      // If user wants to speak while pages are visible, request an interrupt so
      // backend opens/enters steering flow for applying story changes.
      if (voice.wsClient && story.pages.size > 0 && !story.steeringWindowOpen) {
        voice.wsClient.send({
          type: "interrupt",
          page_number: story.steeringWindowPage ?? 1,
        });
      }
      setIsProcessing(false);
      voice.startMic().catch(() => { /* error shown in UI */ });
    }
  }, [voice, story.pages.size, story.steeringWindowOpen, story.steeringWindowPage]);

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

  // Whether the full bottom bar should actually be rendered (not slid away).
  const showFullBar = barExpanded || story.pages.size === 0;

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

      {/* Listening label — shown while mic is active and pages exist */}
      {voice.isListening && story.pages.size > 0 && (
        <div className="absolute top-3 left-1/2 z-20 -translate-x-1/2 pointer-events-none">
          <span className="text-xs font-medium text-purple-600 tracking-wide uppercase animate-pulse bg-white/70 rounded-full px-3 py-1 shadow-sm">
            {isGenerating ? "✨ Creating your story…" : "🎤 Listening…"}
          </span>
        </div>
      )}

      {/* ── StoryBook carousel ─────────────────────────────────────────────
          Padding-bottom:
            - showFullBar → 80 px (pb-20) to clear the floating VoiceButton
              which sits -top-16 (64px) above the in-flow CaptionBar
            - reading mode → 0   — bar is slid out; FAB is corner-positioned */}
      <div
        className={[
          "flex flex-1 overflow-y-auto transition-all duration-300",
          showFullBar ? "pb-20" : "pb-0",
        ].join(" ")}
        data-testid="story-book-wrapper"
      >
        <StoryBook
          pages={story.pages}
          isGenerating={isGenerating}
          storyComplete={storyComplete}
          steeringWindowOpen={story.steeringWindowOpen}
          totalPages={totalPages}
          pauseNarration={voice.isListening}
        />
      </div>

      {/* ── Caption bar + VoiceButton — slides as a unit ─────────────────── */}
      <div
        className={[
          "transition-transform duration-300 flex-shrink-0 relative",
          showFullBar ? "translate-y-0" : "translate-y-full",
        ].join(" ")}
      >
        {/* Large VoiceButton — floats above the caption bar */}
        {showFullBar && (
          <div className="absolute -top-16 left-1/2 z-30 -translate-x-1/2">
            <VoiceButton
              isListening={voice.isListening}
              steeringWindowOpen={story.steeringWindowOpen}
              isGenerating={isGenerating}
              onInterrupt={handleInterrupt}
              onFeedback={handleFeedback}
            />
          </div>
        )}
        <CaptionBar
          captions={story.captions}
          partialCaption={partialCaption}
          safetyRewrite={safetyRewrite}
          safetyAccepted={safetyAccepted}
        />
      </div>

import { StoryTextInput } from "@/components/StoryTextInput";

// ... [existing imports]

// (Inside the component, replacing the floating mic FAB area with the new layout)
      {/* ── Text Input & FAB Area (Bottom-Right) ── */}
      {story.pages.size > 0 && !barExpanded && (
        <div className="fixed bottom-5 right-5 z-30 flex flex-col items-end gap-3">
          {/* Optional Text Input (shown when steering window is open, or always available) */}
          <div className="w-80 shadow-lg origin-bottom-right transition-all animate-in fade-in slide-in-from-bottom-5">
             <StoryTextInput 
               disabled={isGenerating || isProcessing}
               onSend={(text) => {
                 voice.sendTextUpdate(text);
               }} 
             />
          </div>

          <button
            type="button"
            aria-label={voice.isListening ? "Tap to stop" : "Tap to speak"}
            onClick={handleFeedback}
            className={[
              "flex h-14 w-14 items-center justify-center rounded-full shadow-xl",
              voice.isListening
                ? "bg-red-500 text-white hover:bg-red-600 active:bg-red-700 animate-pulse"
                : "bg-purple-500 text-white hover:bg-purple-600 active:bg-purple-700",
              "transition-colors duration-150",
              "focus:outline-none focus-visible:ring-4 focus-visible:ring-purple-400 focus-visible:ring-offset-2",
            ].join(" ")}
            data-testid="mic-fab"
          >
            {voice.isListening ? (
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className="h-6 w-6">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            ) : (
              <SmallMicIcon />
            )}
          </button>
        </div>
      )}

      {/* Silent audio element — unlocks browser autoplay on first mic tap */}
      {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
      <audio ref={audioUnlockRef} src="" className="hidden" aria-hidden="true" />
    </main>
  );
}
