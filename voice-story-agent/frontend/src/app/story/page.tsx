/**
 * /story — main story page (stub).
 *
 * Full implementation added in:
 *   T-034  VoiceButton component
 *   T-035  CaptionBar component
 *   T-036  StoryPage component
 *   T-037  HoldAnimation component
 *   T-038  StoryBook carousel
 *   T-039  useVoiceSession hook
 *   T-040  useStoryState hook
 *   T-041  StoryPage integration
 */

const API_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export default function StoryPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-story-cream px-4">
      {/* ── Brand header ─────────────────────────────────────── */}
      <div className="mb-10 text-center">
        <h1 className="text-5xl font-extrabold tracking-tight text-purple-600 drop-shadow-sm">
          Voice Story Agent
        </h1>
        <p className="mt-3 text-lg text-gray-500">
          A real-time voice storytelling adventure for children ✨
        </p>
      </div>

      {/* ── Mic button placeholder ───────────────────────────── */}
      <button
        disabled
        className="flex h-24 w-24 items-center justify-center rounded-full bg-purple-500 text-white shadow-lg opacity-40 cursor-not-allowed"
        aria-label="Microphone (not yet wired up)"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="currentColor"
          className="h-10 w-10"
        >
          <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4Z" />
          <path d="M19 10a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.93V19H9a1 1 0 0 0 0 2h6a1 1 0 0 0 0-2h-2v-2.07A7 7 0 0 0 19 10Z" />
        </svg>
      </button>

      <p className="mt-6 text-sm text-gray-400">
        Tap the mic to start a story — coming soon
      </p>

      {/* ── Dev info banner (visible only in development) ────── */}
      {process.env.NODE_ENV === "development" && (
        <div className="mt-12 rounded-xl border border-purple-200 bg-purple-50 px-6 py-4 text-sm text-purple-700 max-w-md text-center">
          <p className="font-semibold">Dev mode</p>
          <p className="mt-1 text-purple-500">
            Backend:{" "}
            <a
              href={`${API_URL}/health`}
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              {API_URL}/health
            </a>
          </p>
          <p className="mt-1 text-purple-500 text-xs">
            Components wired up in T-034 → T-041
          </p>
        </div>
      )}
    </main>
  );
}
