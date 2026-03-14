/**
 * StoryBook.tsx — Page carousel for the Voice Story Agent storybook.
 *
 * Layout
 * ------
 * Horizontal scroll-snapping carousel. Each "slide" is either:
 *  - A `StoryPage` for a delivered page (pages map entry)
 *  - A `HoldAnimation` placeholder while the next page is being generated
 *  - A closing card ("The End! …") after `story_complete`
 *
 * Steering indicator
 * ------------------
 * When `steeringWindowOpen = true`, a subtle badge floats in the top-right
 * corner to remind children that they can speak a command.
 *
 * Props
 * -----
 * - pages            Map<number, PageState>  from useStoryState
 * - isGenerating     boolean                 true while page_generating is active
 * - storyComplete    boolean                 true after story_complete event
 * - steeringWindowOpen boolean              true while steering window is open
 * - totalPages       number                  default 5 (used for "Page N of M")
 */

"use client";

import React, { useState, useEffect, useRef } from "react";
import type { PageState } from "@/hooks/useStoryState";
import { StoryPage } from "./StoryPage";
import { HoldAnimation } from "./HoldAnimation";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StoryBookProps {
  /** All delivered pages from useStoryState, keyed by 1-based page number. */
  pages: Map<number, PageState>;
  /** True while a new page is being generated (page_generating fired, page_complete not yet). */
  isGenerating: boolean;
  /** True after the story_complete WebSocket event. */
  storyComplete: boolean;
  /** True while the steering window is open. */
  steeringWindowOpen: boolean;
  /** Total pages in the story (for "Page N of M" labels). Default: 5. */
  totalPages?: number;
}

// ---------------------------------------------------------------------------
// ClosingCard — rendered after story_complete
// ---------------------------------------------------------------------------

function ClosingCard() {
  return (
    <div
      data-testid="story-closing-card"
      className={[
        "flex w-full max-w-2xl flex-col items-center justify-center gap-4",
        "rounded-3xl bg-gradient-to-br from-story-lavender to-story-cream",
        "p-10 shadow-xl",
      ].join(" ")}
    >
      <span className="text-5xl" aria-hidden="true">
        🌟
      </span>
      <h2 className="text-2xl font-extrabold text-purple-700">The End!</h2>
      <p className="text-center text-base text-gray-600">
        What a great adventure. Thanks for listening!
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SteeringBadge — "you can speak" indicator
// ---------------------------------------------------------------------------

function SteeringBadge() {
  return (
    <div
      data-testid="steering-badge"
      role="status"
      aria-label="You can speak to change the story"
      className={[
        "absolute right-4 top-4 z-10",
        "flex items-center gap-1.5 rounded-full",
        "bg-amber-400 px-3 py-1.5 shadow-md",
        "text-xs font-semibold text-amber-900",
      ].join(" ")}
    >
      <span className="h-2 w-2 animate-ping rounded-full bg-amber-700 opacity-75" />
      Speak to change the story
    </div>
  );
}

// ---------------------------------------------------------------------------
// StoryBook component
// ---------------------------------------------------------------------------

export function StoryBook({
  pages,
  isGenerating,
  storyComplete,
  steeringWindowOpen,
  totalPages = 5,
}: StoryBookProps) {
  // Build sorted list of page entries (1→5 order).
  const sortedPages = Array.from(pages.entries()).sort(([a], [b]) => a - b);

  // Track which page is visible in the carousel for audio gating.
  const [activePage, setActivePage] = useState<number>(1);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const slideRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const page = Number(
              (entry.target as HTMLElement).getAttribute("data-page")
            );
            if (page) setActivePage(page);
          }
        }
      },
      { threshold: 0.6 }
    );

    // Observe all currently-registered slides.
    for (const el of slideRefs.current.values()) {
      observerRef.current.observe(el);
    }

    return () => {
      observerRef.current?.disconnect();
    };
  }, []);

  // When new pages arrive, start observing their slide elements.
  useEffect(() => {
    if (!observerRef.current) return;
    for (const [pageNum, el] of slideRefs.current.entries()) {
      if (el) observerRef.current.observe(el);
    }
  }, [sortedPages.length]);

  const registerSlide = (pageNum: number, el: HTMLDivElement | null) => {
    if (el) {
      slideRefs.current.set(pageNum, el);
      observerRef.current?.observe(el);
    } else {
      slideRefs.current.delete(pageNum);
    }
  };

  return (
    <div
      data-testid="story-book"
      className="relative flex w-full flex-1 flex-col items-center"
    >
      {/* Steering window badge */}
      {steeringWindowOpen && <SteeringBadge />}

      {/* Horizontal scroll carousel */}
      <div
        data-testid="story-book-carousel"
        className={[
          "flex w-full snap-x snap-mandatory overflow-x-auto",
          "scrollbar-none gap-4 px-4 py-6",
        ].join(" ")}
        style={{ scrollBehavior: "smooth" }}
      >
        {/* Rendered story pages */}
        {sortedPages.map(([pageNumber, pageState]) => (
          <div
            key={pageNumber}
            ref={(el) => registerSlide(pageNumber, el)}
            data-page={pageNumber}
            data-testid={`story-book-slide-${pageNumber}`}
            className="flex w-full flex-shrink-0 snap-center items-center justify-center"
          >
            <StoryPage
              page={pageState}
              pageNumber={pageNumber}
              totalPages={totalPages}
              isActive={pageNumber === activePage}
            />
          </div>
        ))}

        {/* HoldAnimation placeholder — shown while generating next page */}
        {isGenerating && (
          <div
            data-testid="story-book-hold-slide"
            className="flex w-full flex-shrink-0 snap-center items-center justify-center"
          >
            <HoldAnimation isGenerating={isGenerating} />
          </div>
        )}

        {/* Closing card after story_complete */}
        {storyComplete && (
          <div
            data-testid="story-book-closing-slide"
            className="flex w-full flex-shrink-0 snap-center items-center justify-center"
          >
            <ClosingCard />
          </div>
        )}
      </div>
    </div>
  );
}

export default StoryBook;
