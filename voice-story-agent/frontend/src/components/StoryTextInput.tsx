import React, { useState } from "react";

interface StoryTextInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function StoryTextInput({ onSend, disabled }: StoryTextInputProps) {
  const [text, setText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim() && !disabled) {
      onSend(text.trim());
      setText("");
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className={`flex w-full max-w-lg items-center gap-2 rounded-full bg-white p-2 shadow-sm border border-purple-100 transition-opacity ${
        disabled ? "opacity-50 pointer-events-none" : "opacity-100"
      }`}
    >
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={disabled}
        placeholder="Type a change (e.g. 'make the sky purple')..."
        className="flex-1 bg-transparent px-4 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none"
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        className="flex h-10 w-10 items-center justify-center rounded-full bg-purple-500 text-white transition-colors hover:bg-purple-600 focus:outline-none focus:ring-2 focus:ring-purple-400 focus:ring-offset-2 disabled:bg-gray-300"
        aria-label="Send text update"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={2}
          stroke="currentColor"
          className="h-5 w-5 translate-x-[1px]"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M6 12L3.269 3.125A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5"
          />
        </svg>
      </button>
    </form>
  );
}
