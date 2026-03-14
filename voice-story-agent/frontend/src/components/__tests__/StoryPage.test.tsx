/**
 * StoryPage.test.tsx — Unit tests for StoryPage component (T-039).
 *
 * All "done when" criteria:
 *  1. Renders correctly with text only (no image, no audio)
 *  2. Illustration placeholder appears when illustrationFailed = true
 *  3. Audio element has autoPlay and src = audioUrl when audio is ready
 *  4. Text fades in when page.text changes from null to a string
 *
 * Additional tests:
 *  - Page number indicator displays correct "Page N of M" text
 *  - Image renders with correct src when imageUrl is set
 *  - Image skeleton shown when no image yet and not failed
 *  - Text skeleton shown when text is null
 *  - Audio fallback text shown when audioFailed = true
 *  - No audio element when audioFailed = true
 *  - No audio element when audioUrl is null
 *  - No illustration placeholder when illustrationFailed = false
 *  - Illustration placeholder hides img when illustrationFailed = true
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { StoryPage } from "../StoryPage";
import type { PageState } from "@/hooks/useStoryState";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makePageState(overrides: Partial<PageState> = {}): PageState {
  return {
    pageNumber: 1,
    text: null,
    imageUrl: null,
    audioUrl: null,
    illustrationFailed: false,
    audioFailed: false,
    status: "pending",
    ...overrides,
  };
}

const TEXT_ONLY_PAGE = makePageState({
  text: "Once upon a time, in a cozy burrow beneath a big oak tree, there lived a small rabbit named Pip.",
});

const FULL_PAGE = makePageState({
  text: "Pip loved adventures more than anything.",
  imageUrl: "https://storage.example.com/page1.jpg",
  audioUrl: "https://storage.example.com/page1.mp3",
  status: "complete",
});

const FAILED_ILLUSTRATION_PAGE = makePageState({
  text: "But one day something strange happened.",
  illustrationFailed: true,
  status: "complete",
});

const FAILED_AUDIO_PAGE = makePageState({
  text: "Pip bravely stepped outside.",
  imageUrl: "https://storage.example.com/page2.jpg",
  audioFailed: true,
  status: "complete",
});

// ---------------------------------------------------------------------------
// 1. Renders correctly with text only — no image, no audio (done-when)
// ---------------------------------------------------------------------------

describe("StoryPage — text only (done-when: renders with text, no image, no audio)", () => {
  it("renders story-page element", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("story-page")).toBeInTheDocument();
  });

  it("renders page text when text is present", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("page-text")).toHaveTextContent(
      "Once upon a time"
    );
  });

  it("does NOT render audio element when audioUrl is null", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.queryByTestId("page-audio")).not.toBeInTheDocument();
  });

  it("does NOT render illustration img when imageUrl is null", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.queryByTestId("page-illustration")).not.toBeInTheDocument();
  });

  it("renders illustration skeleton when image not yet available", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("illustration-skeleton")).toBeInTheDocument();
  });

  it("does NOT render illustration placeholder (failure state) on normal page", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(
      screen.queryByTestId("illustration-placeholder")
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 2. Illustration placeholder when illustrationFailed = true (done-when)
// ---------------------------------------------------------------------------

describe("StoryPage — illustration failure (done-when: placeholder when illustrationFailed=true)", () => {
  it("renders illustration placeholder when illustrationFailed is true", () => {
    render(
      <StoryPage page={FAILED_ILLUSTRATION_PAGE} pageNumber={2} totalPages={5} />
    );
    expect(screen.getByTestId("illustration-placeholder")).toBeInTheDocument();
  });

  it("placeholder has accessible label", () => {
    render(
      <StoryPage page={FAILED_ILLUSTRATION_PAGE} pageNumber={2} totalPages={5} />
    );
    const placeholder = screen.getByTestId("illustration-placeholder");
    expect(placeholder).toHaveAttribute("aria-label", "Illustration unavailable");
  });

  it("illustration img NOT rendered when illustrationFailed is true", () => {
    const pageWithBothImageAndFailed = makePageState({
      text: "A page with failure.",
      imageUrl: "https://example.com/img.jpg",
      illustrationFailed: true,
    });
    render(
      <StoryPage page={pageWithBothImageAndFailed} pageNumber={3} totalPages={5} />
    );
    expect(screen.queryByTestId("page-illustration")).not.toBeInTheDocument();
    expect(screen.getByTestId("illustration-placeholder")).toBeInTheDocument();
  });

  it("illustration placeholder NOT shown when illustrationFailed is false", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    expect(
      screen.queryByTestId("illustration-placeholder")
    ).not.toBeInTheDocument();
  });

  it("skeleton NOT shown when illustrationFailed is true", () => {
    render(
      <StoryPage page={FAILED_ILLUSTRATION_PAGE} pageNumber={2} totalPages={5} />
    );
    expect(screen.queryByTestId("illustration-skeleton")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 3. Audio element has autoPlay and src = audioUrl (done-when)
// ---------------------------------------------------------------------------

describe("StoryPage — audio player (done-when: autoPlay, src=audioUrl when ready)", () => {
  it("renders audio element when audioUrl is set and audioFailed is false", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("page-audio")).toBeInTheDocument();
  });

  it("audio element has autoPlay attribute", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    const audio = screen.getByTestId("page-audio");
    expect(audio).toHaveAttribute("autoplay");
  });

  it("audio element src equals audioUrl", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    const audio = screen.getByTestId("page-audio");
    expect(audio).toHaveAttribute("src", "https://storage.example.com/page1.mp3");
  });

  it("does NOT render audio element when audioFailed is true", () => {
    render(
      <StoryPage page={FAILED_AUDIO_PAGE} pageNumber={2} totalPages={5} />
    );
    expect(screen.queryByTestId("page-audio")).not.toBeInTheDocument();
  });

  it("renders audio fallback text when audioFailed is true", () => {
    render(
      <StoryPage page={FAILED_AUDIO_PAGE} pageNumber={2} totalPages={5} />
    );
    expect(screen.getByTestId("audio-fallback")).toBeInTheDocument();
  });

  it("audio fallback NOT shown when audio is available", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.queryByTestId("audio-fallback")).not.toBeInTheDocument();
  });

  it("audio fallback NOT shown when audioUrl is null and audioFailed is false", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.queryByTestId("audio-fallback")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 4. Text fades in when page.text changes null → string (done-when)
// ---------------------------------------------------------------------------

describe("StoryPage — text fade-in (done-when: text fades in when text changes from null to string)", () => {
  it("text area has opacity-0 class when text is null", () => {
    const pending = makePageState({ text: null });
    render(<StoryPage page={pending} pageNumber={1} totalPages={5} />);
    const textArea = screen.getByTestId("story-text-area");
    expect(textArea.className).toContain("opacity-0");
  });

  it("text area has opacity-100 class when text is present", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    const textArea = screen.getByTestId("story-text-area");
    expect(textArea.className).toContain("opacity-100");
  });

  it("after text arrives (rerender), text is visible", () => {
    const pending = makePageState({ text: null });
    const { rerender } = render(
      <StoryPage page={pending} pageNumber={1} totalPages={5} />
    );
    expect(screen.queryByTestId("page-text")).not.toBeInTheDocument();

    const withText = makePageState({
      text: "Pip stepped bravely into the forest.",
    });
    rerender(<StoryPage page={withText} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("page-text")).toHaveTextContent(
      "Pip stepped bravely into the forest."
    );
  });

  it("text skeleton shown when text is null", () => {
    const pending = makePageState({ text: null });
    render(<StoryPage page={pending} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("text-skeleton")).toBeInTheDocument();
  });

  it("text skeleton hidden when text is present", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.queryByTestId("text-skeleton")).not.toBeInTheDocument();
  });

  it("text area has transition class for smooth fade", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    const textArea = screen.getByTestId("story-text-area");
    expect(textArea.className).toContain("transition");
  });
});

// ---------------------------------------------------------------------------
// 5. Page number indicator
// ---------------------------------------------------------------------------

describe("StoryPage — page number indicator", () => {
  it("shows 'Page 1 of 5'", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("page-number-indicator")).toHaveTextContent(
      "Page 1 of 5"
    );
  });

  it("shows 'Page 3 of 5'", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={3} totalPages={5} />);
    expect(screen.getByTestId("page-number-indicator")).toHaveTextContent(
      "Page 3 of 5"
    );
  });

  it("story-page has accessible aria-label", () => {
    render(<StoryPage page={TEXT_ONLY_PAGE} pageNumber={2} totalPages={5} />);
    expect(screen.getByTestId("story-page")).toHaveAttribute(
      "aria-label",
      "Page 2 of 5"
    );
  });
});

// ---------------------------------------------------------------------------
// 6. Illustration renders with correct src
// ---------------------------------------------------------------------------

describe("StoryPage — illustration image", () => {
  it("renders img element when imageUrl is set", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("page-illustration")).toBeInTheDocument();
  });

  it("img src equals imageUrl", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.getByTestId("page-illustration")).toHaveAttribute(
      "src",
      "https://storage.example.com/page1.jpg"
    );
  });

  it("img has descriptive alt text", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    const img = screen.getByTestId("page-illustration");
    expect(img).toHaveAttribute("alt", "Illustration for page 1");
  });

  it("skeleton NOT shown when imageUrl is set", () => {
    render(<StoryPage page={FULL_PAGE} pageNumber={1} totalPages={5} />);
    expect(screen.queryByTestId("illustration-skeleton")).not.toBeInTheDocument();
  });
});
