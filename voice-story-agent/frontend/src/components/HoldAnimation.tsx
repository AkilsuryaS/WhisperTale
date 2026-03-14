/**
 * HoldAnimation.tsx — Gentle looping animation shown while a story page is generating.
 *
 * Behaviour
 * ---------
 * - Shown when `isGenerating = true` (i.e. after `page_generating` event fires)
 * - Hidden when `isGenerating = false` (i.e. after `page_complete` fires)
 * - MUST remain visible at all times during generation — renders a non-null
 *   DOM subtree so children always have visual feedback
 *
 * Animation
 * ---------
 * Three soft bouncing dots using Tailwind's built-in `animate-bounce` with
 * staggered `animation-delay` values so they cascade in a wave pattern.
 * An accessible label is provided via `aria-label` for screen readers.
 */

"use client";

import React from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface HoldAnimationProps {
  /** True while a page is being generated; component is visible when true. */
  isGenerating: boolean;
  /** Optional additional CSS classes for the outer container. */
  className?: string;
}

// ---------------------------------------------------------------------------
// HoldAnimation component
// ---------------------------------------------------------------------------

export function HoldAnimation({ isGenerating, className = "" }: HoldAnimationProps) {
  if (!isGenerating) return null;

  return (
    <div
      data-testid="hold-animation"
      role="status"
      aria-label="Generating your story page…"
      className={[
        "flex flex-col items-center justify-center gap-4 py-12",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {/* Bouncing dots */}
      <div
        data-testid="hold-animation-dots"
        className="flex items-end gap-2"
        aria-hidden="true"
      >
        <span
          data-testid="hold-animation-dot-0"
          className="block h-4 w-4 animate-bounce rounded-full bg-purple-400"
          style={{ animationDelay: "0ms" }}
        />
        <span
          data-testid="hold-animation-dot-1"
          className="block h-4 w-4 animate-bounce rounded-full bg-purple-400"
          style={{ animationDelay: "150ms" }}
        />
        <span
          data-testid="hold-animation-dot-2"
          className="block h-4 w-4 animate-bounce rounded-full bg-purple-400"
          style={{ animationDelay: "300ms" }}
        />
      </div>

      {/* Friendly caption */}
      <p className="text-sm font-medium text-purple-400">
        Creating your story…
      </p>
    </div>
  );
}

export default HoldAnimation;
