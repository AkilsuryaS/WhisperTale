/**
 * CaptionBar.tsx — Scrolling caption strip for the Voice Story Agent.
 *
 * Layout
 * ------
 * Fixed strip at the bottom of the viewport.  Each caption is a bubble:
 *   - user   → right-aligned, sky-blue background
 *   - agent  → left-aligned,  warm-cream background
 *
 * Partial transcripts
 * -------------------
 * Pass `partialCaption` (a live, not-yet-final transcript) to show a
 * "typing" bubble that is updated in-place as words arrive.  Once the
 * transcript is committed (isFinal = true) the caller should move it into
 * the `captions` array and clear `partialCaption`.
 *
 * Safety cards
 * ------------
 * - `safetyRewrite` non-null  → amber card "I can make it better! …"
 * - `safetyAccepted` non-null → replaces the amber card with a green
 *   confirmation "✓ Got it! …"
 *
 * Auto-scroll
 * -----------
 * The scroll container has a `ref`; a `useEffect` scrolls it to the bottom
 * whenever `captions`, `partialCaption`, `safetyRewrite`, or `safetyAccepted`
 * changes.
 */

"use client";

import React, { useRef, useEffect } from "react";
import type { Caption } from "@/hooks/useStoryState";
import type { SafetyRewriteEvent, SafetyAcceptedEvent } from "@/lib/wsTypes";

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** A live, not-yet-final caption being streamed word-by-word. */
export interface PartialCaption {
  turnId: string;
  role: "user" | "agent";
  text: string;
}

export interface CaptionBarProps {
  /** Ordered list of committed (final) captions. */
  captions: Caption[];
  /** Live streaming partial transcript, or null when silent. */
  partialCaption?: PartialCaption | null;
  /** Non-null when a safety rewrite is pending user acknowledgement. */
  safetyRewrite?: SafetyRewriteEvent | null;
  /** Non-null when the user has accepted a safety rewrite. */
  safetyAccepted?: SafetyAcceptedEvent | null;
  /** Optional extra CSS classes for the outer container. */
  className?: string;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface BubbleProps {
  role: "user" | "agent";
  text: string;
  isPartial?: boolean;
  testId?: string;
}

function Bubble({ role, text, isPartial = false, testId }: BubbleProps) {
  const isUser = role === "user";
  return (
    <div
      data-testid={testId ?? (isUser ? "caption-bubble-user" : "caption-bubble-agent")}
      className={[
        "max-w-[75%] rounded-2xl px-4 py-2 text-sm leading-snug shadow-sm",
        isUser
          ? "ml-auto bg-story-sky text-blue-900 text-right"
          : "mr-auto bg-story-cream text-gray-800 text-left",
        isPartial ? "opacity-70 italic" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CaptionBar
// ---------------------------------------------------------------------------

export function CaptionBar({
  captions,
  partialCaption = null,
  safetyRewrite = null,
  safetyAccepted = null,
  className = "",
}: CaptionBarProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever content changes.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [captions, partialCaption, safetyRewrite, safetyAccepted]);

  return (
    <aside
      aria-label="Story captions"
      data-testid="caption-bar"
      className={[
        "fixed bottom-0 left-0 right-0 z-20",
        "flex flex-col",
        "bg-white/80 backdrop-blur-sm shadow-[0_-2px_12px_rgba(0,0,0,0.08)]",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {/* Scrollable caption list */}
      <div
        ref={scrollRef}
        data-testid="caption-scroll"
        className="flex max-h-48 flex-col gap-2 overflow-y-auto px-4 py-3"
      >
        {/* Committed captions */}
        {captions.map((cap) => (
          <Bubble
            key={cap.turnId}
            role={cap.role}
            text={cap.text}
            testId={cap.role === "user" ? "caption-bubble-user" : "caption-bubble-agent"}
          />
        ))}

        {/* Partial (streaming) caption — shown below committed ones */}
        {partialCaption && (
          <Bubble
            key={`partial-${partialCaption.turnId}`}
            role={partialCaption.role}
            text={partialCaption.text}
            isPartial
            testId="caption-bubble-partial"
          />
        )}

        {/* Safety cards — shown after all captions */}
        {safetyRewrite && !safetyAccepted && (
          <div
            data-testid="safety-rewrite-card"
            role="alert"
            className={[
              "rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3",
              "text-sm text-amber-900 shadow-sm",
            ].join(" ")}
          >
            <p className="font-semibold">I can make it better! ✨</p>
            <p className="mt-1">{safetyRewrite.proposed_rewrite}</p>
          </div>
        )}

        {safetyAccepted && (
          <div
            data-testid="safety-accepted-card"
            role="status"
            className={[
              "rounded-2xl border border-green-300 bg-green-50 px-4 py-3",
              "text-sm text-green-900 shadow-sm",
            ].join(" ")}
          >
            <p className="font-semibold">✓ Got it!</p>
            <p className="mt-1">{safetyAccepted.final_premise}</p>
          </div>
        )}
      </div>
    </aside>
  );
}

export default CaptionBar;
