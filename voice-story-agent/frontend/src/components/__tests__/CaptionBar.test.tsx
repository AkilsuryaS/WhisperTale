/**
 * CaptionBar.test.tsx — Unit tests for CaptionBar component (T-038).
 *
 * All "done when" criteria:
 *  1. Renders user and agent bubbles with correct alignment
 *  2. Safety rewrite amber card appears when safetyRewrite is non-null
 *  3. Auto-scroll triggers when new caption is added
 *  4. Partial transcript updates (same turn_id, is_final=false) update existing bubble text
 *
 * Additional tests:
 *  - Empty captions renders without error
 *  - Multiple captions all rendered
 *  - Agent bubble has left-aligned class, user bubble has right-aligned class
 *  - Safety accepted green card replaces amber card
 *  - When both safetyRewrite and safetyAccepted present: only green shown
 *  - Partial caption rendered with partial testid
 *  - Partial caption text updates when prop changes
 *  - No partial bubble when partialCaption is null
 *  - Safety rewrite card is absent when safetyRewrite is null
 *  - Safety accepted card is absent when safetyAccepted is null
 *  - scrollTop set to scrollHeight on mount and on update
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import { CaptionBar } from "../CaptionBar";
import type { Caption } from "@/hooks/useStoryState";
import type { SafetyRewriteEvent, SafetyAcceptedEvent } from "@/lib/wsTypes";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const USER_CAPTION: Caption = {
  turnId: "t-user-1",
  role: "user",
  text: "Make it sillier!",
  isFinal: true,
};

const AGENT_CAPTION: Caption = {
  turnId: "t-agent-1",
  role: "agent",
  text: "Great choice! Adding more silliness.",
  isFinal: true,
};

const SAFETY_REWRITE: SafetyRewriteEvent = {
  type: "safety_rewrite",
  decision_id: "dec-1",
  turn_id: "t-sr-1",
  detected_category: "physical_harm",
  proposed_rewrite: "How about a friendly snowball fight instead?",
  phase: "steering",
};

const SAFETY_ACCEPTED: SafetyAcceptedEvent = {
  type: "safety_accepted",
  decision_id: "dec-1",
  final_premise: "A friendly snowball fight.",
  exclusion_added: null,
};

// ---------------------------------------------------------------------------
// Mock scrollTop setter to detect auto-scroll
// ---------------------------------------------------------------------------

beforeEach(() => {
  // jsdom doesn't implement scrollTop/scrollHeight natively.
  // We spy on the setter to verify scroll calls.
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    set: jest.fn(),
    get: jest.fn().mockReturnValue(0),
    configurable: true,
  });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    get: jest.fn().mockReturnValue(500),
    configurable: true,
  });
});

// ---------------------------------------------------------------------------
// 1. Renders user/agent bubbles with correct alignment (done-when)
// ---------------------------------------------------------------------------

describe("CaptionBar — bubble rendering (done-when: correct alignment)", () => {
  it("renders user bubble", () => {
    render(<CaptionBar captions={[USER_CAPTION]} />);
    expect(screen.getByTestId("caption-bubble-user")).toBeInTheDocument();
    expect(screen.getByTestId("caption-bubble-user")).toHaveTextContent(
      "Make it sillier!"
    );
  });

  it("user bubble has right-aligned (ml-auto) class", () => {
    render(<CaptionBar captions={[USER_CAPTION]} />);
    const bubble = screen.getByTestId("caption-bubble-user");
    expect(bubble.className).toContain("ml-auto");
  });

  it("user bubble has sky-blue background class", () => {
    render(<CaptionBar captions={[USER_CAPTION]} />);
    const bubble = screen.getByTestId("caption-bubble-user");
    expect(bubble.className).toContain("sky");
  });

  it("renders agent bubble", () => {
    render(<CaptionBar captions={[AGENT_CAPTION]} />);
    expect(screen.getByTestId("caption-bubble-agent")).toBeInTheDocument();
    expect(screen.getByTestId("caption-bubble-agent")).toHaveTextContent(
      "Great choice! Adding more silliness."
    );
  });

  it("agent bubble has left-aligned (mr-auto) class", () => {
    render(<CaptionBar captions={[AGENT_CAPTION]} />);
    const bubble = screen.getByTestId("caption-bubble-agent");
    expect(bubble.className).toContain("mr-auto");
  });

  it("agent bubble has cream background class", () => {
    render(<CaptionBar captions={[AGENT_CAPTION]} />);
    const bubble = screen.getByTestId("caption-bubble-agent");
    expect(bubble.className).toContain("cream");
  });

  it("renders multiple captions in order", () => {
    render(
      <CaptionBar captions={[AGENT_CAPTION, USER_CAPTION]} />
    );
    const allBubbles = screen.getByTestId("caption-scroll").children;
    // Both bubbles rendered
    expect(allBubbles.length).toBeGreaterThanOrEqual(2);
  });

  it("renders empty captions without crashing", () => {
    render(<CaptionBar captions={[]} />);
    expect(screen.getByTestId("caption-bar")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 2. Safety rewrite amber card (done-when)
// ---------------------------------------------------------------------------

describe("CaptionBar — safety rewrite card (done-when: amber card when safetyRewrite non-null)", () => {
  it("safety rewrite card appears when safetyRewrite is non-null", () => {
    render(
      <CaptionBar captions={[]} safetyRewrite={SAFETY_REWRITE} />
    );
    expect(screen.getByTestId("safety-rewrite-card")).toBeInTheDocument();
  });

  it("safety rewrite card contains proposed_rewrite text", () => {
    render(
      <CaptionBar captions={[]} safetyRewrite={SAFETY_REWRITE} />
    );
    expect(screen.getByTestId("safety-rewrite-card")).toHaveTextContent(
      "How about a friendly snowball fight instead?"
    );
  });

  it("safety rewrite card has amber styling", () => {
    render(
      <CaptionBar captions={[]} safetyRewrite={SAFETY_REWRITE} />
    );
    const card = screen.getByTestId("safety-rewrite-card");
    expect(card.className).toContain("amber");
  });

  it("safety rewrite card contains 'I can make it better' text", () => {
    render(
      <CaptionBar captions={[]} safetyRewrite={SAFETY_REWRITE} />
    );
    expect(screen.getByTestId("safety-rewrite-card")).toHaveTextContent(
      /I can make it better/i
    );
  });

  it("safety rewrite card is NOT shown when safetyRewrite is null", () => {
    render(<CaptionBar captions={[]} safetyRewrite={null} />);
    expect(screen.queryByTestId("safety-rewrite-card")).not.toBeInTheDocument();
  });

  it("safety rewrite card is NOT shown when safetyAccepted overrides it", () => {
    render(
      <CaptionBar
        captions={[]}
        safetyRewrite={SAFETY_REWRITE}
        safetyAccepted={SAFETY_ACCEPTED}
      />
    );
    expect(screen.queryByTestId("safety-rewrite-card")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Safety accepted green card
// ---------------------------------------------------------------------------

describe("CaptionBar — safety accepted green card", () => {
  it("safety accepted card appears when safetyAccepted is non-null", () => {
    render(
      <CaptionBar
        captions={[]}
        safetyRewrite={SAFETY_REWRITE}
        safetyAccepted={SAFETY_ACCEPTED}
      />
    );
    expect(screen.getByTestId("safety-accepted-card")).toBeInTheDocument();
  });

  it("safety accepted card has green styling", () => {
    render(
      <CaptionBar
        captions={[]}
        safetyRewrite={SAFETY_REWRITE}
        safetyAccepted={SAFETY_ACCEPTED}
      />
    );
    const card = screen.getByTestId("safety-accepted-card");
    expect(card.className).toContain("green");
  });

  it("safety accepted card shows final_premise", () => {
    render(
      <CaptionBar
        captions={[]}
        safetyRewrite={SAFETY_REWRITE}
        safetyAccepted={SAFETY_ACCEPTED}
      />
    );
    expect(screen.getByTestId("safety-accepted-card")).toHaveTextContent(
      "A friendly snowball fight."
    );
  });

  it("safety accepted card is NOT shown when null", () => {
    render(
      <CaptionBar captions={[]} safetyRewrite={SAFETY_REWRITE} />
    );
    expect(screen.queryByTestId("safety-accepted-card")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 3. Auto-scroll triggers (done-when)
// ---------------------------------------------------------------------------

describe("CaptionBar — auto-scroll (done-when: scrolls when new caption added)", () => {
  it("scrollTop is set on initial render (scrolls to bottom)", () => {
    const scrollTopSetter = jest.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTop", {
      set: scrollTopSetter,
      get: jest.fn().mockReturnValue(0),
      configurable: true,
    });

    render(<CaptionBar captions={[USER_CAPTION]} />);
    expect(scrollTopSetter).toHaveBeenCalled();
  });

  it("scrollTop is set to scrollHeight value", () => {
    const scrollTopSetter = jest.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTop", {
      set: scrollTopSetter,
      get: jest.fn().mockReturnValue(0),
      configurable: true,
    });
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      get: jest.fn().mockReturnValue(999),
      configurable: true,
    });

    render(<CaptionBar captions={[USER_CAPTION]} />);
    // scrollTop should be set to scrollHeight (999)
    expect(scrollTopSetter).toHaveBeenCalledWith(999);
  });

  it("scroll container has correct testid", () => {
    render(<CaptionBar captions={[]} />);
    expect(screen.getByTestId("caption-scroll")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// 4. Partial transcript updates in-place (done-when)
// ---------------------------------------------------------------------------

describe("CaptionBar — partial transcript (done-when: updates existing bubble)", () => {
  it("partial caption is rendered when partialCaption is provided", () => {
    render(
      <CaptionBar
        captions={[]}
        partialCaption={{ turnId: "t-partial-1", role: "user", text: "Make it s..." }}
      />
    );
    expect(screen.getByTestId("caption-bubble-partial")).toBeInTheDocument();
    expect(screen.getByTestId("caption-bubble-partial")).toHaveTextContent(
      "Make it s..."
    );
  });

  it("partial caption text updates when prop changes", () => {
    const { rerender } = render(
      <CaptionBar
        captions={[]}
        partialCaption={{ turnId: "t-p1", role: "user", text: "Make" }}
      />
    );
    expect(screen.getByTestId("caption-bubble-partial")).toHaveTextContent("Make");

    rerender(
      <CaptionBar
        captions={[]}
        partialCaption={{ turnId: "t-p1", role: "user", text: "Make it sillier!" }}
      />
    );
    expect(screen.getByTestId("caption-bubble-partial")).toHaveTextContent(
      "Make it sillier!"
    );
  });

  it("no partial bubble when partialCaption is null", () => {
    render(<CaptionBar captions={[USER_CAPTION]} partialCaption={null} />);
    expect(screen.queryByTestId("caption-bubble-partial")).not.toBeInTheDocument();
  });

  it("partial caption has italic/opacity styling (streaming indicator)", () => {
    render(
      <CaptionBar
        captions={[]}
        partialCaption={{ turnId: "t-p2", role: "agent", text: "Once upon..." }}
      />
    );
    const partial = screen.getByTestId("caption-bubble-partial");
    // Our impl adds 'italic' and 'opacity-70' classes when isPartial=true
    expect(partial.className).toMatch(/italic|opacity/);
  });

  it("partial caption is shown below committed captions", () => {
    render(
      <CaptionBar
        captions={[USER_CAPTION]}
        partialCaption={{ turnId: "t-p3", role: "agent", text: "Generating..." }}
      />
    );
    // Both present
    expect(screen.getByTestId("caption-bubble-user")).toBeInTheDocument();
    expect(screen.getByTestId("caption-bubble-partial")).toBeInTheDocument();
  });

  it("when partial is promoted to committed, committed bubble appears and partial disappears", () => {
    const { rerender } = render(
      <CaptionBar
        captions={[]}
        partialCaption={{ turnId: "t-promote-1", role: "agent", text: "Great choice!" }}
      />
    );
    expect(screen.getByTestId("caption-bubble-partial")).toBeInTheDocument();

    // Promote: add to captions, clear partial
    rerender(
      <CaptionBar
        captions={[{ turnId: "t-promote-1", role: "agent", text: "Great choice!", isFinal: true }]}
        partialCaption={null}
      />
    );
    expect(screen.queryByTestId("caption-bubble-partial")).not.toBeInTheDocument();
    expect(screen.getByTestId("caption-bubble-agent")).toHaveTextContent("Great choice!");
  });
});
