/**
 * EditPanel.tsx — Inline edit input for a story page.
 *
 * Renders a pencil icon button that expands into a text field + mic button.
 * Uses the browser SpeechRecognition API for voice input.
 * Calls onEditRequest with the instruction when submitted.
 */

"use client";

import React, { useState, useRef, useCallback } from "react";

export interface EditPanelProps {
  /** Called when the user submits an edit instruction. */
  onEditRequest: (instruction: string) => void;
  /** True while an edit is in progress — disables the panel. */
  isEditing: boolean;
}

// SpeechRecognition type shim for browsers that use the webkit prefix
type SpeechRecognitionInstance = InstanceType<
  typeof window extends { SpeechRecognition: infer T }
    ? T extends new (...args: unknown[]) => unknown
      ? T
      : never
    : never
>;

function getSpeechRecognition():
  | (new () => SpeechRecognitionInstance)
  | null {
  if (typeof window === "undefined") return null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition ?? null;
}

export function EditPanel({ onEditRequest, isEditing }: EditPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [isListening, setIsListening] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recognitionRef = useRef<any>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => {
      if (!prev) {
        setTimeout(() => inputRef.current?.focus(), 100);
      }
      return !prev;
    });
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = instruction.trim();
    if (!trimmed || isEditing) return;
    onEditRequest(trimmed);
    setInstruction("");
    setExpanded(false);
  }, [instruction, isEditing, onEditRequest]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit]
  );

  const handleMic = useCallback(() => {
    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    const SR = getSpeechRecognition();
    if (!SR) {
      console.warn("[EditPanel] SpeechRecognition not supported");
      return;
    }

    const recognition = new SR();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event: { results: { transcript: string }[][] }) => {
      const transcript = event.results[0]?.[0]?.transcript;
      if (transcript) {
        setInstruction((prev) => (prev ? `${prev} ${transcript}` : transcript));
      }
      setIsListening(false);
    };

    recognition.onerror = () => {
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsListening(true);
  }, [isListening]);

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={handleToggle}
        disabled={isEditing}
        aria-label="Edit this page"
        data-testid="edit-panel-toggle"
        className={[
          "flex h-9 w-9 items-center justify-center rounded-full shadow-md",
          "bg-purple-500 text-white hover:bg-purple-600 active:bg-purple-700",
          "transition-colors duration-150",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-purple-400",
          isEditing ? "opacity-50 cursor-not-allowed" : "",
        ].join(" ")}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="h-4 w-4"
        >
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
        </svg>
      </button>
    );
  }

  return (
    <div
      data-testid="edit-panel-expanded"
      className="flex w-full items-center gap-2 rounded-2xl bg-gray-50 p-2 shadow-inner"
    >
      <input
        ref={inputRef}
        type="text"
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Describe your change…"
        disabled={isEditing}
        data-testid="edit-panel-input"
        className={[
          "flex-1 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm",
          "placeholder-gray-400 outline-none",
          "focus:border-purple-400 focus:ring-2 focus:ring-purple-200",
          isEditing ? "opacity-50" : "",
        ].join(" ")}
      />

      {/* Mic button */}
      <button
        type="button"
        onClick={handleMic}
        disabled={isEditing}
        aria-label={isListening ? "Stop recording" : "Speak your change"}
        data-testid="edit-panel-mic"
        className={[
          "flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full",
          isListening
            ? "bg-red-500 text-white animate-pulse"
            : "bg-gray-200 text-gray-600 hover:bg-gray-300",
          "transition-colors duration-150",
          isEditing ? "opacity-50 cursor-not-allowed" : "",
        ].join(" ")}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="currentColor"
          aria-hidden="true"
          className="h-4 w-4"
        >
          <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4Z" />
          <path d="M19 10a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.93V19H9a1 1 0 0 0 0 2h6a1 1 0 0 0 0-2h-2v-2.07A7 7 0 0 0 19 10Z" />
        </svg>
      </button>

      {/* Submit button */}
      <button
        type="button"
        onClick={handleSubmit}
        disabled={isEditing || !instruction.trim()}
        aria-label="Submit edit"
        data-testid="edit-panel-submit"
        className={[
          "flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full",
          "bg-purple-500 text-white hover:bg-purple-600 active:bg-purple-700",
          "transition-colors duration-150",
          isEditing || !instruction.trim() ? "opacity-50 cursor-not-allowed" : "",
        ].join(" ")}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="h-4 w-4"
        >
          <line x1="22" y1="2" x2="11" y2="13" />
          <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
      </button>

      {/* Close button */}
      <button
        type="button"
        onClick={handleToggle}
        aria-label="Cancel edit"
        data-testid="edit-panel-close"
        className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-gray-200 text-gray-500 hover:bg-gray-300 transition-colors duration-150"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="h-4 w-4"
        >
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}

export default EditPanel;
