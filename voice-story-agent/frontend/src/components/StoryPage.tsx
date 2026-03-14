/**
 * StoryPage.tsx — Renders one page of the storybook.
 *
 * Layout
 * ------
 * - Story text (visible after page_text_ready; fades in when text changes from null → string)
 * - Illustration: <img> with signed URL; friendly "painting" SVG placeholder if illustrationFailed
 * - Audio player: hidden <audio autoPlay> element when audioUrl is set; text-only fallback if audioFailed
 * - Page number indicator: "Page N of M"
 * - Each asset (text, image, audio) has a fade-in transition as it arrives
 *
 * Props
 * -----
 * - page: PageState   — all state for this page (from useStoryState)
 * - pageNumber: number — current page number (1-based)
 * - totalPages: number — total number of pages in the story
 */

"use client";

import React, { useRef, useEffect } from "react";
import type { PageState } from "@/hooks/useStoryState";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StoryPageProps {
  /** Full page state from useStoryState. */
  page: PageState;
  /** 1-based page number for display (e.g. "Page 2 of 5"). */
  pageNumber: number;
  /** Total number of pages in the story. */
  totalPages: number;
  /** True when this page is the currently visible slide — gates audio playback. */
  isActive: boolean;
}

// ---------------------------------------------------------------------------
// Painting placeholder SVG (shown when illustration has failed)
// ---------------------------------------------------------------------------

function PaintingPlaceholder() {
  return (
    <div
      data-testid="illustration-placeholder"
      aria-label="Illustration unavailable"
      className="flex flex-col items-center justify-center gap-2 rounded-2xl bg-story-cream/60 p-6 text-amber-700"
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        aria-hidden="true"
        className="h-16 w-16 opacity-60"
      >
        {/* Frame */}
        <rect x="2" y="3" width="20" height="16" rx="2" />
        {/* Mountain / landscape hint */}
        <path d="M2 15 l5-6 4 4 3-3 8 5" />
        {/* Sun */}
        <circle cx="17" cy="8" r="2" />
      </svg>
      <p className="text-sm font-medium">Picture coming soon…</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AudioFallback — shown when narration audio failed to generate
// ---------------------------------------------------------------------------

function AudioFallback() {
  return (
    <p
      data-testid="audio-fallback"
      className="mt-2 text-xs text-gray-500 italic"
    >
      Narration unavailable — read along above!
    </p>
  );
}

// ---------------------------------------------------------------------------
// StoryPage component
// ---------------------------------------------------------------------------

export function StoryPage({ page, pageNumber, totalPages, isActive }: StoryPageProps) {
  const hasText = page.text !== null;
  const hasImage = page.imageUrl !== null;
  const hasAudio = page.audioUrl !== null;

  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (!audioRef.current) return;
    if (isActive && page.audioUrl) {
      audioRef.current.play().catch(() => {});
    } else {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
  }, [isActive, page.audioUrl]);

  return (
    <article
      data-testid="story-page"
      aria-label={`Page ${pageNumber} of ${totalPages}`}
      className="flex w-full max-w-2xl flex-col gap-4 rounded-3xl bg-white p-6 shadow-xl"
    >
      {/* ── Page number indicator ──────────────────────────────────────────── */}
      <p
        data-testid="page-number-indicator"
        className="text-center text-xs font-semibold uppercase tracking-widest text-gray-400"
      >
        Page {pageNumber} of {totalPages}
      </p>

      {/* ── Illustration area ──────────────────────────────────────────────── */}
      <div
        data-testid="illustration-area"
        className="relative overflow-hidden rounded-2xl bg-story-cream/40"
        style={{ minHeight: "12rem" }}
      >
        {hasImage && !page.illustrationFailed && (
          <img
            data-testid="page-illustration"
            src={page.imageUrl!}
            alt={`Illustration for page ${pageNumber}`}
            className={[
              "w-full rounded-2xl object-cover transition-opacity duration-700",
              hasImage ? "opacity-100" : "opacity-0",
            ].join(" ")}
          />
        )}

        {page.illustrationFailed && <PaintingPlaceholder />}

        {/* Skeleton shown while no image yet and not failed */}
        {!hasImage && !page.illustrationFailed && (
          <div
            data-testid="illustration-skeleton"
            aria-hidden="true"
            className="h-48 animate-pulse rounded-2xl bg-gray-200"
          />
        )}
      </div>

      {/* ── Story text ────────────────────────────────────────────────────── */}
      <div
        data-testid="story-text-area"
        className={[
          "min-h-[4rem] transition-opacity duration-700",
          hasText ? "opacity-100" : "opacity-0",
        ].join(" ")}
      >
        {hasText ? (
          <p
            data-testid="page-text"
            className="text-base leading-relaxed text-gray-800"
          >
            {page.text}
          </p>
        ) : (
          <div
            data-testid="text-skeleton"
            aria-hidden="true"
            className="space-y-2"
          >
            <div className="h-4 w-full animate-pulse rounded bg-gray-200" />
            <div className="h-4 w-5/6 animate-pulse rounded bg-gray-200" />
            <div className="h-4 w-4/6 animate-pulse rounded bg-gray-200" />
          </div>
        )}
      </div>

      {/* ── Audio player / fallback ────────────────────────────────────────── */}
      {hasAudio && !page.audioFailed && (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <audio
          ref={audioRef}
          data-testid="page-audio"
          src={page.audioUrl!}
          aria-label={`Narration for page ${pageNumber}`}
          className="hidden"
        />
      )}

      {page.audioFailed && <AudioFallback />}
    </article>
  );
}

export default StoryPage;
