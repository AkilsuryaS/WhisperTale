"""
SafetyService — child-content classifier + rewriter backed by Gemini Flash.

Public interface (T-016):
    async def evaluate(utterance: str, *, session_id: str = "") -> SafetyResult

Design
------
- A single Gemini 2.5 Flash call with structured JSON output classifies the
  utterance and, when unsafe, generates a child-safe alternative premise.
- The genai.Client is injectable via the constructor so unit tests never make
  real network calls.
- Fail-safe behaviour: ANY Gemini API exception OR malformed JSON response
  results in SafetyResult(safe=False, category=None, rewrite=SAFE_FALLBACK_REWRITE).
  The original utterance is NEVER logged or passed to any generation call when
  the classifier fails (Child Safety First constitution principle).

Output JSON schema expected from Gemini:
    {
        "safe": <bool>,
        "category": <"physical_harm"|"character_death"|"gore"|"destruction"
                      |"sexual_content"|"fear_escalation"|null>,
        "rewrite": <string ≤ 80 words | null>
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.config import settings
from app.models.safety import SAFE_FALLBACK_REWRITE, SafetyCategory, SafetyResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety system prompt
# ---------------------------------------------------------------------------

_SAFETY_SYSTEM_PROMPT = """\
You are a child-content safety classifier for an interactive storytelling app \
aimed at children aged 4–10.

TASK
Evaluate whether the story premise or utterance below is appropriate for young \
children. Respond ONLY with a single valid JSON object — no prose, no markdown, \
no code fences.

PERMITTED CONTENT (safe = true)
- Emotional realism: sadness, loneliness, fear, conflict, mild peril, \
  disappointment
- Characters facing challenges and overcoming them
- Moral dilemmas appropriate for children
- Mild adventure and age-appropriate tension

FORBIDDEN CONTENT (safe = false) — pick the single best category:
  physical_harm     — hitting, punching, kicking, injuring, hurting characters
  character_death   — killing, death, murder, dying of named characters
  gore              — blood, wounds, graphic injuries, dismemberment
  destruction       — burning buildings, destroying towns, mass devastation
  sexual_content    — any sexual theme or content
  fear_escalation   — deliberately building dread, horror, or sustained \
                      psychological terror beyond mild peril

OUTPUT SCHEMA (respond ONLY with valid JSON):
{
  "safe": <boolean>,
  "category": <one of the six category strings above, or null if safe=true>,
  "rewrite": <string: warm, age-appropriate, actionable alternative premise \
               of ≤ 80 words that does NOT quote or paraphrase the forbidden \
               elements — required when safe=false, else null>
}

REWRITE RULES
- Must NOT contain the original forbidden verbs, nouns, or themes.
- Must be a complete sentence that functions as a usable story premise.
- Must be warm and encouraging for a young child.
- Must be ≤ 80 words.
"""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class SafetyService:
    """
    Classifies story utterances for child safety via a single Gemini Flash call.

    Usage:
        svc = SafetyService()
        result = await svc.evaluate("the dragon burns down the village")
        if not result.safe:
            # use result.rewrite as the child-safe alternative
    """

    def __init__(self, client: genai.Client | None = None) -> None:
        # Injecting a client lets unit tests avoid real network calls.
        self._client = client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> genai.Client:
        if self._client is None:
            project_id = settings.require_gcp("SafetyService")
            self._client = genai.Client(
                vertexai=True,
                project=project_id,
                location=settings.GCP_REGION,
            )
        return self._client

    async def _call_gemini(self, utterance: str) -> dict[str, Any]:
        """
        Call Gemini Flash with the safety system prompt and return the parsed
        JSON dict.  Raises on any API error or JSON parse failure — the caller
        (evaluate) is responsible for catching and applying fail-safe logic.
        """
        client = self._get_client()
        response = await client.aio.models.generate_content(
            model=settings.GEMINI_FLASH_MODEL,
            contents=utterance,
            config=genai_types.GenerateContentConfig(
                system_instruction=_SAFETY_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        return json.loads(response.text)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def evaluate(
        self, utterance: str, *, session_id: str = ""
    ) -> SafetyResult:
        """
        Classify *utterance* for child safety.

        Returns:
            SafetyResult(safe=True, category=None, rewrite=None)
                when the utterance is age-appropriate.
            SafetyResult(safe=False, category=<SafetyCategory>, rewrite=<str>)
                when the utterance is unsafe; rewrite is the child-safe
                alternative premise returned by Gemini.

        On ANY Gemini API exception or malformed response:
            Returns SafetyResult(safe=False, category=None,
                                  rewrite=SAFE_FALLBACK_REWRITE).
            Logs the error type and session_id — NOT the original utterance.
        """
        # ── Step 1: Call Gemini ───────────────────────────────────────────
        try:
            data = await self._call_gemini(utterance)
        except Exception as exc:
            logger.error(
                "SafetyService: classifier error — returning fail-safe result",
                extra={
                    "event_type": "safety_classifier_error",
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                },
            )
            return SafetyResult(
                safe=False, category=None, rewrite=SAFE_FALLBACK_REWRITE
            )

        # ── Step 2: Parse and validate the response dict ──────────────────
        try:
            safe = bool(data.get("safe", False))
            category_str: str | None = data.get("category")
            rewrite: str | None = data.get("rewrite")

            if safe:
                logger.info(
                    "SafetyService: content classified safe",
                    extra={
                        "event_type": "safety_decision",
                        "session_id": session_id,
                        "safe": True,
                    },
                )
                return SafetyResult(safe=True, category=None, rewrite=None)

            # Map category string to enum; fall back gracefully on unknown values.
            category: SafetyCategory | None = None
            if category_str:
                try:
                    category = SafetyCategory(category_str)
                except ValueError:
                    logger.warning(
                        "SafetyService: unknown safety category %r",
                        category_str,
                        extra={
                            "event_type": "safety_unknown_category",
                            "session_id": session_id,
                            "category": category_str,
                        },
                    )

            # Log the triggered category — NOT the raw utterance (Child Safety First)
            logger.warning(
                "SafetyService: unsafe content detected — rewriting",
                extra={
                    "event_type": "safety_decision",
                    "session_id": session_id,
                    "safe": False,
                    "category": category.value if category else None,
                },
            )
            return SafetyResult(
                safe=False,
                category=category,
                rewrite=rewrite if rewrite else SAFE_FALLBACK_REWRITE,
            )

        except Exception as exc:
            logger.error(
                "SafetyService: response parsing error — returning fail-safe result",
                extra={
                    "event_type": "safety_parse_error",
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                },
            )
            return SafetyResult(
                safe=False, category=None, rewrite=SAFE_FALLBACK_REWRITE
            )
