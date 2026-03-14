"""
logging_config.py — Cloud Logging-compatible structured JSON formatter.

Cloud Logging parses log records as structured JSON when they are written to
stdout / stderr from a Cloud Run container.  The required fields for correct
parsing are documented at:
  https://cloud.google.com/logging/docs/structured-logging

Key fields emitted per record
──────────────────────────────
  severity   — Cloud Logging severity (maps from Python log level)
  message    — human-readable log message
  timestamp  — RFC 3339 UTC timestamp
  session_id — propagated from the ``extra`` dict so every log line can be
               correlated to a single story session
  event_type — logical event name (e.g. "ws_connect", "safety_triggered")
               propagated from the ``extra`` dict

Any additional keys passed to ``extra={...}`` in a ``logger.info(…)`` call are
included verbatim in the structured record.

Usage
──────
1.  Call ``configure_logging()`` once at startup (e.g. in ``app/main.py``).
2.  Obtain a bound logger:
        logger = logging.getLogger(__name__)
3.  Emit structured events:
        logger.info(
            "page generation complete",
            extra={
                "session_id": session_id,
                "event_type": "page_complete",
                "page_number": page_number,
                "duration_ms": elapsed_ms,
            },
        )

Raw-input safety rule
──────────────────────
NEVER include ``raw_input`` or ``utterance`` in a structured log record.
Log only the safety *category* and session ID.  This is enforced by the
SafetyService instrumentation in safety_service.py.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Severity mapping: Python level → Cloud Logging severity string
# ---------------------------------------------------------------------------

_LEVEL_TO_SEVERITY: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def _to_severity(level: int) -> str:
    """Map a Python log level integer to a Cloud Logging severity string."""
    return _LEVEL_TO_SEVERITY.get(level, "DEFAULT")


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------

# Keys that are always present on every LogRecord and should NOT be forwarded
# into the structured payload as extra context (they are either already
# captured under a canonical name or are internal Python logging internals).
_SKIP_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class StructuredJsonFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects compatible with
    Google Cloud Logging's structured-logging ingestion.

    Each record produces a JSON object with at minimum:
        {
          "severity": "INFO",
          "message": "...",
          "timestamp": "2026-03-14T10:00:00.000000Z",
          "logger": "app.services.safety_service"
        }

    Any keys passed in ``extra={...}`` (e.g. ``session_id``, ``event_type``,
    ``duration_ms``) are merged into the top-level JSON object.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # Build the base record first so exc_text etc. are populated.
        super().format(record)

        payload: dict[str, Any] = {
            "severity": _to_severity(record.levelno),
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "logger": record.name,
        }

        # Merge any extra fields the caller passed, skipping internals.
        for key, value in record.__dict__.items():
            if key not in _SKIP_ATTRS and not key.startswith("_"):
                payload[key] = value

        # Append exception traceback as a string if present.
        if record.exc_info and record.exc_text:
            payload["exception"] = record.exc_text

        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# configure_logging()
# ---------------------------------------------------------------------------

def configure_logging(
    level: int = logging.INFO,
    *,
    force_json: bool | None = None,
) -> None:
    """
    Configure the root logger to emit structured JSON records to stdout.

    Args:
        level:       Root log level (default INFO).
        force_json:  If True, always use the JSON formatter.
                     If False, use the plain text formatter.
                     If None (default), use JSON when the LOG_FORMAT env var
                     is "json" OR when running inside Cloud Run
                     (K_SERVICE env var is set by Cloud Run automatically).
    """
    if force_json is None:
        force_json = (
            os.environ.get("LOG_FORMAT", "").lower() == "json"
            or bool(os.environ.get("K_SERVICE"))  # Cloud Run sets K_SERVICE
        )

    root_logger = logging.getLogger()

    # Remove any handlers that basicConfig may have already added.
    root_logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if force_json:
        handler.setFormatter(StructuredJsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(levelname)-8s  %(name)s  %(message)s")
        )

    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Suppress noisy third-party loggers that are not useful in production.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Latency context manager
# ---------------------------------------------------------------------------

class LatencyLogger:
    """
    Context manager that measures elapsed wall-clock time and emits a
    structured log record on exit.

    Usage:
        with LatencyLogger(logger, "gemini_call", session_id=sid, model=model):
            response = await client.generate(...)

    Emits:
        {
          "event_type": "gemini_call",
          "session_id": "<sid>",
          "model": "<model>",
          "duration_ms": 123,
          "severity": "INFO",
          ...
        }
    """

    def __init__(
        self,
        logger: logging.Logger,
        event_type: str,
        level: int = logging.INFO,
        **extra: Any,
    ) -> None:
        self._logger = logger
        self._event_type = event_type
        self._level = level
        self._extra = extra
        self._start: float = 0.0

    def __enter__(self) -> "LatencyLogger":
        import time
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        import time
        elapsed_ms = round((time.perf_counter() - self._start) * 1000)
        self._logger.log(
            self._level,
            "%s completed in %d ms",
            self._event_type,
            elapsed_ms,
            extra={
                "event_type": self._event_type,
                "duration_ms": elapsed_ms,
                "success": exc_type is None,
                **self._extra,
            },
        )
