/**
 * VoiceButton.tsx — Accessible mic toggle button for Voice Story Agent.
 *
 * Visual states
 * -------------
 * 1. Idle           — solid purple circle with mic icon; click starts speaking
 * 2. Listening      — purple circle + purple pulsing ring (microphone active)
 * 3. Steering open  — purple circle + amber pulsing ring ("you can speak" cue)
 * 4. Disabled       — greyed-out circle (page generation in progress, no window)
 *
 * Priority: steeringWindowOpen > isListening > isGenerating (for ring colour).
 * The button is NEVER fully disabled during the steering window so children
 * can always interrupt the story.
 *
 * ARIA
 * ----
 * - role="button" (implicit on <button>)
 * - aria-label — changes per state
 * - aria-pressed — true while isListening or during steering window
 * - aria-disabled — true only during generation (not during steering window)
 *
 * Click behaviour
 * ---------------
 * - During isGenerating (and no steering window): calls onInterrupt
 * - Otherwise: calls onFeedback
 */

"use client";

import React from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface VoiceButtonProps {
  /** True while the microphone is recording and streaming audio. */
  isListening: boolean;
  /** True while the steering window is open (amber ring). */
  steeringWindowOpen: boolean;
  /** True while a page is being generated — button shows interrupt affordance. */
  isGenerating: boolean;
  /** Called when the user taps the button during page generation (interrupt). */
  onInterrupt?: () => void;
  /** Called when the user taps the button in idle / steering states. */
  onFeedback?: () => void;
  /** Optional additional CSS class names. */
  className?: string;
}

// ---------------------------------------------------------------------------
// Mic SVG icon
// ---------------------------------------------------------------------------

function MicIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className={className}
    >
      <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4Z" />
      <path d="M19 10a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.93V19H9a1 1 0 0 0 0 2h6a1 1 0 0 0 0-2h-2v-2.07A7 7 0 0 0 19 10Z" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VoiceButton({
  isListening,
  steeringWindowOpen,
  isGenerating,
  onInterrupt,
  onFeedback,
  className = "",
}: VoiceButtonProps) {
  // ── Derived state ──────────────────────────────────────────────────────────

  /**
   * The button is only truly disabled (aria-disabled + visual greyed-out)
   * when generation is in progress AND the steering window is not open.
   */
  const isDisabled = isGenerating && !steeringWindowOpen;

  // ── ARIA label ─────────────────────────────────────────────────────────────

  let ariaLabel: string;
  if (steeringWindowOpen) {
    ariaLabel = "Speak now to change the story";
  } else if (isListening) {
    ariaLabel = "Listening — tap to stop";
  } else if (isDisabled) {
    ariaLabel = "Microphone disabled while generating";
  } else {
    ariaLabel = "Tap to speak";
  }

  // ── Click handler ──────────────────────────────────────────────────────────

  function handleClick() {
    if (isDisabled) return;
    if (isGenerating && !steeringWindowOpen) {
      onInterrupt?.();
    } else {
      onFeedback?.();
    }
  }

  // ── Ring colour / animation ────────────────────────────────────────────────

  /**
   * Ring element shown around the button while active.
   * - Amber  when steeringWindowOpen
   * - Purple when isListening (and steering window not open)
   * - Hidden otherwise
   */
  const showRing = steeringWindowOpen || isListening;
  const ringColour = steeringWindowOpen
    ? "border-amber-400"
    : "border-purple-400";

  // ── Button colours ─────────────────────────────────────────────────────────

  const buttonColour = isDisabled
    ? "bg-gray-300 text-gray-400 cursor-not-allowed"
    : steeringWindowOpen
    ? "bg-amber-500 text-white hover:bg-amber-600 active:bg-amber-700"
    : "bg-purple-500 text-white hover:bg-purple-600 active:bg-purple-700";

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div
      className={`relative inline-flex items-center justify-center ${className}`}
      data-testid="voice-button-wrapper"
    >
      {/* Pulsing ring — rendered behind the button */}
      {showRing && (
        <span
          data-testid="voice-button-ring"
          aria-hidden="true"
          className={[
            "absolute inset-0 rounded-full border-4",
            ringColour,
            "animate-gentle-pulse",
          ].join(" ")}
        />
      )}

      <button
        type="button"
        role="button"
        aria-label={ariaLabel}
        aria-pressed={isListening || steeringWindowOpen}
        aria-disabled={isDisabled}
        disabled={isDisabled}
        onClick={handleClick}
        data-testid="voice-button"
        className={[
          "relative z-10 flex h-20 w-20 items-center justify-center",
          "rounded-full shadow-lg transition-colors duration-150 focus:outline-none",
          "focus-visible:ring-4 focus-visible:ring-offset-2 focus-visible:ring-purple-400",
          buttonColour,
        ].join(" ")}
      >
        <MicIcon className="h-9 w-9" />
      </button>
    </div>
  );
}

export default VoiceButton;
