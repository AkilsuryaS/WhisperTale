/**
 * HoldAnimation.test.tsx — Unit tests for HoldAnimation component (T-040).
 *
 * All "done when" criteria:
 *  1. HoldAnimation is visible between page_generating and page_complete
 *     → component renders (non-null) when isGenerating=true
 *     → component renders null when isGenerating=false
 *
 * Additional tests:
 *  - Has accessible role="status" with aria-label
 *  - Renders three bouncing dots when visible
 *  - Each dot has animate-bounce class
 *  - Does not render at all when isGenerating = false (null subtree)
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { HoldAnimation } from "../HoldAnimation";

// ---------------------------------------------------------------------------
// 1. Visible when isGenerating = true (done-when)
// ---------------------------------------------------------------------------

describe("HoldAnimation — visibility (done-when: visible between page_generating and page_complete)", () => {
  it("renders hold-animation element when isGenerating is true", () => {
    render(<HoldAnimation isGenerating={true} />);
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();
  });

  it("does NOT render when isGenerating is false", () => {
    render(<HoldAnimation isGenerating={false} />);
    expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();
  });

  it("appears when isGenerating switches from false to true (simulate page_generating)", () => {
    const { rerender } = render(<HoldAnimation isGenerating={false} />);
    expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();

    rerender(<HoldAnimation isGenerating={true} />);
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();
  });

  it("disappears when isGenerating switches from true to false (simulate page_complete)", () => {
    const { rerender } = render(<HoldAnimation isGenerating={true} />);
    expect(screen.getByTestId("hold-animation")).toBeInTheDocument();

    rerender(<HoldAnimation isGenerating={false} />);
    expect(screen.queryByTestId("hold-animation")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 2. Accessibility
// ---------------------------------------------------------------------------

describe("HoldAnimation — accessibility", () => {
  it("has role='status'", () => {
    render(<HoldAnimation isGenerating={true} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("has descriptive aria-label", () => {
    render(<HoldAnimation isGenerating={true} />);
    expect(screen.getByRole("status")).toHaveAttribute(
      "aria-label",
      "Generating your story page…"
    );
  });
});

// ---------------------------------------------------------------------------
// 3. Dots rendered with animation
// ---------------------------------------------------------------------------

describe("HoldAnimation — bouncing dots", () => {
  it("renders dots container", () => {
    render(<HoldAnimation isGenerating={true} />);
    expect(screen.getByTestId("hold-animation-dots")).toBeInTheDocument();
  });

  it("renders exactly three dots", () => {
    render(<HoldAnimation isGenerating={true} />);
    const dot0 = screen.getByTestId("hold-animation-dot-0");
    const dot1 = screen.getByTestId("hold-animation-dot-1");
    const dot2 = screen.getByTestId("hold-animation-dot-2");
    expect(dot0).toBeInTheDocument();
    expect(dot1).toBeInTheDocument();
    expect(dot2).toBeInTheDocument();
  });

  it("each dot has animate-bounce class", () => {
    render(<HoldAnimation isGenerating={true} />);
    [0, 1, 2].forEach((i) => {
      expect(screen.getByTestId(`hold-animation-dot-${i}`).className).toContain(
        "animate-bounce"
      );
    });
  });

  it("dots have staggered animation-delay", () => {
    render(<HoldAnimation isGenerating={true} />);
    const dot0 = screen.getByTestId("hold-animation-dot-0");
    const dot1 = screen.getByTestId("hold-animation-dot-1");
    const dot2 = screen.getByTestId("hold-animation-dot-2");
    // Delays must be different (staggered).
    const delay0 = (dot0 as HTMLElement).style.animationDelay;
    const delay1 = (dot1 as HTMLElement).style.animationDelay;
    const delay2 = (dot2 as HTMLElement).style.animationDelay;
    expect(new Set([delay0, delay1, delay2]).size).toBe(3);
  });

  it("dots are circular (rounded-full class)", () => {
    render(<HoldAnimation isGenerating={true} />);
    [0, 1, 2].forEach((i) => {
      expect(
        screen.getByTestId(`hold-animation-dot-${i}`).className
      ).toContain("rounded-full");
    });
  });
});
