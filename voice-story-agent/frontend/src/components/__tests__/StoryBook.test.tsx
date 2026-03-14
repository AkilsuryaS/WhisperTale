/**
 * StoryBook.test.tsx — Unit tests for StoryBook component (T-040).
 *
 * All "done when" criteria:
 *  1. HoldAnimation is visible between page_generating and page_complete
 *     → hold-animation testid present when isGenerating=true, absent when false
 *  2. StoryBook renders 0–5 StoryPage components based on useStoryState.pages
 *     → 0 pages → no story-page elements
 *     → 1 page  → 1 story-page element
 *     → 3 pages → 3 story-page elements
 *     → 5 pages → 5 story-page elements
 *  3. Closing card appears after story_complete
 *     → story-closing-card present when storyComplete=true
 *     → story-closing-card absent when storyComplete=false
 *  4. Steering indicator appears/disappears with steeringWindowOpen
 *     → steering-badge present when steeringWindowOpen=true
 *     → steering-badge absent when steeringWindowOpen=false
 *
 * Additional tests:
 *  - story-book-carousel is always rendered
 *  - Hold slide is absent when isGenerating=false
 *  - Pages are rendered in ascending order (page 1 before page 3)
 *  - Closing card text content correct
 *  - Steering badge has accessible role/label
 *  - HoldAnimation inside StoryBook has role="status"
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import { StoryBook } from "../StoryBook";
import type { PageState } from "@/hooks/useStoryState";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makePage(pageNumber: number, text = "Story text."): PageState {
  return {
    pageNumber,
    text,
    imageUrl: null,
    audioUrl: null,
    illustrationFailed: false,
    audioFailed: false,
    status: "complete",
  };
}

function makePages(count: number): Map<number, PageState> {
  const m = new Map<number, PageState>();
  for (let i = 1; i <= count; i++) {
    m.set(i, makePage(i, `Page ${i} text.`));
  }
  return m;
}

const EMPTY_PAGES = new Map<number, PageState>();

// ---------------------------------------------------------------------------
// 1. HoldAnimation visible/invisible (done-when)
// ---------------------------------------------------------------------------

describe("StoryBook — HoldAnimation (done-when: visible during generation)", () => {
  it("renders hold-animation when isGenerating=true", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={true}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();
  });

  it("does NOT render hold-animation when isGenerating=false", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();
  });

  it("hold slide appears when isGenerating transitions to true", () => {
    const { rerender } = render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();

    rerender(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={true}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();
  });

  it("hold slide disappears when isGenerating transitions to false (page_complete)", () => {
    const { rerender } = render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={true}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();

    rerender(
      <StoryBook
        pages={makePages(1)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 2. StoryPage count matches pages map (done-when)
// ---------------------------------------------------------------------------

describe("StoryBook — page rendering (done-when: 0–5 StoryPage components)", () => {
  it("renders 0 story pages when pages map is empty", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryAllByTestId("story-page")).toHaveLength(0);
  });

  it("renders 1 story page when pages map has 1 entry", () => {
    render(
      <StoryBook
        pages={makePages(1)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getAllByTestId("story-page")).toHaveLength(1);
  });

  it("renders 3 story pages when pages map has 3 entries", () => {
    render(
      <StoryBook
        pages={makePages(3)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getAllByTestId("story-page")).toHaveLength(3);
  });

  it("renders 5 story pages when pages map has 5 entries", () => {
    render(
      <StoryBook
        pages={makePages(5)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getAllByTestId("story-page")).toHaveLength(5);
  });

  it("pages are rendered in ascending order (page 1 before page 3)", () => {
    render(
      <StoryBook
        pages={makePages(3)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    const indicators = screen.getAllByTestId("page-number-indicator");
    expect(indicators[0]).toHaveTextContent("Page 1 of 5");
    expect(indicators[1]).toHaveTextContent("Page 2 of 5");
    expect(indicators[2]).toHaveTextContent("Page 3 of 5");
  });

  it("renders the correct slide containers for delivered pages", () => {
    render(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("story-book-slide-1")).toBeInTheDocument();
    expect(screen.getByTestId("story-book-slide-2")).toBeInTheDocument();
    expect(screen.queryByTestId("story-book-slide-3")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 3. Closing card after story_complete (done-when)
// ---------------------------------------------------------------------------

describe("StoryBook — closing card (done-when: appears after story_complete)", () => {
  it("renders closing card when storyComplete=true", () => {
    render(
      <StoryBook
        pages={makePages(5)}
        isGenerating={false}
        storyComplete={true}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("story-closing-card")).toBeInTheDocument();
  });

  it("closing card is absent when storyComplete=false", () => {
    render(
      <StoryBook
        pages={makePages(3)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("story-closing-card")).not.toBeInTheDocument();
  });

  it("closing card contains 'The End!' text", () => {
    render(
      <StoryBook
        pages={makePages(5)}
        isGenerating={false}
        storyComplete={true}
        steeringWindowOpen={false}
      />
    );
    const card = screen.getByTestId("story-closing-card");
    expect(card).toHaveTextContent("The End!");
  });

  it("closing card contains adventure message", () => {
    render(
      <StoryBook
        pages={makePages(5)}
        isGenerating={false}
        storyComplete={true}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("story-closing-card")).toHaveTextContent(
      /great adventure/i
    );
  });

  it("closing slide container present when storyComplete=true", () => {
    render(
      <StoryBook
        pages={makePages(5)}
        isGenerating={false}
        storyComplete={true}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("story-book-closing-slide")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 4. Steering indicator badge (done-when)
// ---------------------------------------------------------------------------

describe("StoryBook — steering badge (done-when: appears/disappears with steeringWindowOpen)", () => {
  it("renders steering badge when steeringWindowOpen=true", () => {
    render(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={true}
      />
    );
    expect(screen.getByTestId("steering-badge")).toBeInTheDocument();
  });

  it("steering badge absent when steeringWindowOpen=false", () => {
    render(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("steering-badge")).not.toBeInTheDocument();
  });

  it("steering badge appears when steeringWindowOpen transitions to true", () => {
    const { rerender } = render(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("steering-badge")).not.toBeInTheDocument();

    rerender(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={true}
      />
    );
    expect(screen.getByTestId("steering-badge")).toBeInTheDocument();
  });

  it("steering badge disappears when steeringWindowOpen transitions to false", () => {
    const { rerender } = render(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={true}
      />
    );
    expect(screen.getByTestId("steering-badge")).toBeInTheDocument();

    rerender(
      <StoryBook
        pages={makePages(2)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.queryByTestId("steering-badge")).not.toBeInTheDocument();
  });

  it("steering badge has accessible role and label", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={true}
      />
    );
    const badge = screen.getByTestId("steering-badge");
    expect(badge).toHaveAttribute("role", "status");
    expect(badge).toHaveAttribute(
      "aria-label",
      "You can speak to change the story"
    );
  });

  it("steering badge has amber styling", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={true}
      />
    );
    const badge = screen.getByTestId("steering-badge");
    expect(badge.className).toContain("amber");
  });
});

// ---------------------------------------------------------------------------
// 5. General structure
// ---------------------------------------------------------------------------

describe("StoryBook — general structure", () => {
  it("always renders story-book container", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("story-book")).toBeInTheDocument();
  });

  it("always renders the carousel container", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("story-book-carousel")).toBeInTheDocument();
  });

  it("hold-animation inside StoryBook has role status", () => {
    render(
      <StoryBook
        pages={EMPTY_PAGES}
        isGenerating={true}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    const holdEl = screen.getByTestId("hold-animation");
    expect(holdEl).toHaveAttribute("role", "status");
  });

  it("can simultaneously show pages, hold animation, and steering badge", () => {
    render(
      <StoryBook
        pages={makePages(2)}
        isGenerating={true}
        storyComplete={false}
        steeringWindowOpen={true}
      />
    );
    expect(screen.getAllByTestId("story-page")).toHaveLength(2);
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();
    expect(screen.getByTestId("steering-badge")).toBeInTheDocument();
  });

  it("uses correct totalPages default (5) in page number indicator", () => {
    render(
      <StoryBook
        pages={makePages(1)}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
      />
    );
    expect(screen.getByTestId("page-number-indicator")).toHaveTextContent(
      "Page 1 of 5"
    );
  });

  it("forwards custom totalPages to StoryPage", () => {
    const pages = new Map<number, PageState>();
    pages.set(1, makePage(1));
    render(
      <StoryBook
        pages={pages}
        isGenerating={false}
        storyComplete={false}
        steeringWindowOpen={false}
        totalPages={3}
      />
    );
    expect(screen.getByTestId("page-number-indicator")).toHaveTextContent(
      "Page 1 of 3"
    );
  });
});
