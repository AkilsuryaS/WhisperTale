"""
Tests for T-044: Cloud Logging structured events.

Covers:
- StructuredJsonFormatter emits valid JSON with required Cloud Logging fields
- JSON records include session_id / event_type / duration_ms when passed in extra={}
- SafetyService logs do NOT contain raw_input / utterance text
- SafetyService emits event_type="safety_decision" with category but no raw text
- LatencyLogger emits event_type with duration_ms
- configure_logging() sets up a handler with the JSON formatter in json mode
- configure_logging() falls back to plain text formatter in non-Cloud-Run mode
- page_orchestrator structured log fields (page_generation_started, page_complete,
  gemini_call_latency, page_asset_failed)
- story_ws structured log fields (ws_connect, ws_disconnect, voice_command_applied,
  voice_command_received, session_status_changed)
- logging_config module exists and is importable
- StructuredJsonFormatter severity mapping for all standard levels
- Extra fields passed to logger.info() are forwarded into the JSON payload
- Exception info is captured as "exception" key (no traceback leak in normal path)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------

from app.logging_config import (  # noqa: E402
    LatencyLogger,
    StructuredJsonFormatter,
    _to_severity,
    configure_logging,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_handler_with_json_formatter() -> tuple[logging.Logger, StringIO]:
    """Return a logger + StringIO stream wired with the JSON formatter."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredJsonFormatter())
    test_logger = logging.getLogger(f"test.{id(stream)}")
    test_logger.handlers = [handler]
    test_logger.setLevel(logging.DEBUG)
    test_logger.propagate = False
    return test_logger, stream


# ===========================================================================
# StructuredJsonFormatter basics
# ===========================================================================


class TestStructuredJsonFormatter:
    def test_output_is_valid_json(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("hello world")
        payload = json.loads(stream.getvalue().strip())
        assert isinstance(payload, dict)

    def test_severity_info(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("test message")
        payload = json.loads(stream.getvalue().strip())
        assert payload["severity"] == "INFO"

    def test_severity_warning(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.warning("warn msg")
        payload = json.loads(stream.getvalue().strip())
        assert payload["severity"] == "WARNING"

    def test_severity_error(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.error("err msg")
        payload = json.loads(stream.getvalue().strip())
        assert payload["severity"] == "ERROR"

    def test_severity_debug(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.debug("debug msg")
        payload = json.loads(stream.getvalue().strip())
        assert payload["severity"] == "DEBUG"

    def test_severity_critical(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.critical("critical msg")
        payload = json.loads(stream.getvalue().strip())
        assert payload["severity"] == "CRITICAL"

    def test_message_field_present(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("the actual message")
        payload = json.loads(stream.getvalue().strip())
        assert "message" in payload
        assert payload["message"] == "the actual message"

    def test_timestamp_field_present(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("ts test")
        payload = json.loads(stream.getvalue().strip())
        assert "timestamp" in payload
        # Should be a non-empty string
        assert isinstance(payload["timestamp"], str)
        assert len(payload["timestamp"]) > 10

    def test_logger_field_present(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("logger name test")
        payload = json.loads(stream.getvalue().strip())
        assert "logger" in payload

    def test_extra_session_id_forwarded(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("session test", extra={"session_id": "abc-123"})
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("session_id") == "abc-123"

    def test_extra_event_type_forwarded(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info(
            "event test",
            extra={"event_type": "ws_connect", "session_id": "s1"},
        )
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("event_type") == "ws_connect"

    def test_extra_duration_ms_forwarded(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("latency test", extra={"duration_ms": 123})
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("duration_ms") == 123

    def test_multiple_extra_fields(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info(
            "multi",
            extra={
                "session_id": "s2",
                "event_type": "page_complete",
                "page_number": 3,
                "illustration_failed": False,
            },
        )
        payload = json.loads(stream.getvalue().strip())
        assert payload["page_number"] == 3
        assert payload["illustration_failed"] is False

    def test_raw_input_not_present_by_default(self) -> None:
        """Ensure no accidental raw_input leaks from base record attributes."""
        logger, stream = _make_stream_handler_with_json_formatter()
        logger.info("safety test", extra={"session_id": "s3", "safe": False})
        payload = json.loads(stream.getvalue().strip())
        assert "raw_input" not in payload
        assert "utterance" not in payload


# ===========================================================================
# _to_severity
# ===========================================================================


class TestToSeverity:
    def test_info(self) -> None:
        assert _to_severity(logging.INFO) == "INFO"

    def test_warning(self) -> None:
        assert _to_severity(logging.WARNING) == "WARNING"

    def test_error(self) -> None:
        assert _to_severity(logging.ERROR) == "ERROR"

    def test_debug(self) -> None:
        assert _to_severity(logging.DEBUG) == "DEBUG"

    def test_critical(self) -> None:
        assert _to_severity(logging.CRITICAL) == "CRITICAL"

    def test_unknown_level_returns_default(self) -> None:
        assert _to_severity(99) == "DEFAULT"


# ===========================================================================
# configure_logging
# ===========================================================================


class TestConfigureLogging:
    def test_force_json_adds_handler(self) -> None:
        """configure_logging(force_json=True) should install a StreamHandler."""
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            configure_logging(force_json=True)
            assert len(root.handlers) >= 1
            assert any(
                isinstance(h, logging.StreamHandler) for h in root.handlers
            )
        finally:
            root.handlers = original_handlers

    def test_force_json_formatter_is_json(self) -> None:
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            configure_logging(force_json=True)
            handler = root.handlers[0]
            assert isinstance(handler.formatter, StructuredJsonFormatter)
        finally:
            root.handlers = original_handlers

    def test_force_not_json_uses_plain_formatter(self) -> None:
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            configure_logging(force_json=False)
            handler = root.handlers[0]
            assert not isinstance(handler.formatter, StructuredJsonFormatter)
        finally:
            root.handlers = original_handlers

    def test_k_service_env_triggers_json(self) -> None:
        """When K_SERVICE is set (Cloud Run), JSON formatter should be used."""
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            with patch.dict(os.environ, {"K_SERVICE": "my-service"}):
                configure_logging()
            handler = root.handlers[0]
            assert isinstance(handler.formatter, StructuredJsonFormatter)
        finally:
            root.handlers = original_handlers

    def test_log_format_env_triggers_json(self) -> None:
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            with patch.dict(os.environ, {"LOG_FORMAT": "json"}):
                configure_logging()
            handler = root.handlers[0]
            assert isinstance(handler.formatter, StructuredJsonFormatter)
        finally:
            root.handlers = original_handlers


# ===========================================================================
# LatencyLogger
# ===========================================================================


class TestLatencyLogger:
    def test_emits_event_type(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        with LatencyLogger(logger, "gemini_call", session_id="s1"):
            pass
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("event_type") == "gemini_call"

    def test_emits_duration_ms(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        with LatencyLogger(logger, "gemini_call"):
            pass
        payload = json.loads(stream.getvalue().strip())
        assert "duration_ms" in payload
        assert isinstance(payload["duration_ms"], int)
        assert payload["duration_ms"] >= 0

    def test_extra_kwargs_forwarded(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        with LatencyLogger(logger, "gemini_call", session_id="s1", model="flash"):
            pass
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("session_id") == "s1"
        assert payload.get("model") == "flash"

    def test_success_true_on_no_exception(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        with LatencyLogger(logger, "op"):
            pass
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("success") is True

    def test_success_false_on_exception(self) -> None:
        logger, stream = _make_stream_handler_with_json_formatter()
        try:
            with LatencyLogger(logger, "op"):
                raise ValueError("oops")
        except ValueError:
            pass
        payload = json.loads(stream.getvalue().strip())
        assert payload.get("success") is False


# ===========================================================================
# SafetyService structured logging — raw_input must NOT be logged
# ===========================================================================


class TestSafetyServiceStructuredLogs:
    """
    Verify that SafetyService.evaluate() emits event_type="safety_decision"
    with the category but NOT the raw utterance text.
    """

    @pytest.mark.asyncio
    async def test_safe_result_logs_safety_decision(self) -> None:
        from app.services.safety_service import SafetyService

        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        mock_client.aio.models = MagicMock()

        mock_response = MagicMock()
        mock_response.text = '{"safe": true, "category": null, "rewrite": null}'
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        svc = SafetyService(client=mock_client)

        captured_extras: list[dict] = []

        def _capture_log(msg, *args, **kwargs):
            extra = kwargs.get("extra", {})
            captured_extras.append(extra)

        with patch.object(
            logging.getLogger("app.services.safety_service"), "info", side_effect=_capture_log
        ):
            result = await svc.evaluate("once upon a time", session_id="s99")

        assert result.safe is True
        events = [e.get("event_type") for e in captured_extras]
        assert "safety_decision" in events

        # Critical: NO raw utterance text in any logged extra
        for extra in captured_extras:
            for v in extra.values():
                assert "once upon a time" not in str(v), (
                    f"raw utterance leaked into log extra: {extra}"
                )

    @pytest.mark.asyncio
    async def test_unsafe_result_logs_category_not_raw_input(self) -> None:
        from app.services.safety_service import SafetyService

        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        mock_client.aio.models = MagicMock()

        mock_response = MagicMock()
        mock_response.text = (
            '{"safe": false, "category": "physical_harm", "rewrite": "play nicely"}'
        )
        mock_client.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        svc = SafetyService(client=mock_client)

        captured_extras: list[dict] = []

        def _capture_warn(msg, *args, **kwargs):
            extra = kwargs.get("extra", {})
            captured_extras.append(extra)

        with patch.object(
            logging.getLogger("app.services.safety_service"),
            "warning",
            side_effect=_capture_warn,
        ):
            result = await svc.evaluate(
                "the dragon punches the princess", session_id="s100"
            )

        assert result.safe is False

        events = {e.get("event_type") for e in captured_extras}
        assert "safety_decision" in events

        # Find the safety_decision extra
        safety_extra = next(
            e for e in captured_extras if e.get("event_type") == "safety_decision"
        )
        assert safety_extra.get("session_id") == "s100"
        assert safety_extra.get("category") == "physical_harm"
        assert "safe" in safety_extra
        assert safety_extra.get("safe") is False

        # raw_input must NOT appear
        for extra in captured_extras:
            for v in extra.values():
                assert "punches" not in str(v), (
                    f"raw utterance text leaked into log extra: {extra}"
                )

    @pytest.mark.asyncio
    async def test_classifier_error_logs_event_type_not_utterance(self) -> None:
        from app.services.safety_service import SafetyService

        mock_client = MagicMock()
        mock_client.aio = MagicMock()
        mock_client.aio.models = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API unreachable")
        )

        svc = SafetyService(client=mock_client)

        captured_extras: list[dict] = []

        def _capture_error(msg, *args, **kwargs):
            extra = kwargs.get("extra", {})
            captured_extras.append(extra)

        with patch.object(
            logging.getLogger("app.services.safety_service"),
            "error",
            side_effect=_capture_error,
        ):
            result = await svc.evaluate("kill everyone", session_id="s101")

        assert result.safe is False

        events = [e.get("event_type") for e in captured_extras]
        assert "safety_classifier_error" in events

        # utterance must NEVER appear in any logged extra
        for extra in captured_extras:
            for v in extra.values():
                assert "kill" not in str(v).lower(), (
                    f"forbidden text in log extra: {extra}"
                )


# ===========================================================================
# logging_config module structural tests
# ===========================================================================


class TestLoggingConfigModule:
    def test_module_importable(self) -> None:
        import app.logging_config  # noqa: F401

    def test_configure_logging_callable(self) -> None:
        import app.logging_config as lc
        assert callable(lc.configure_logging)

    def test_structured_json_formatter_class_exists(self) -> None:
        import app.logging_config as lc
        assert hasattr(lc, "StructuredJsonFormatter")

    def test_latency_logger_class_exists(self) -> None:
        import app.logging_config as lc
        assert hasattr(lc, "LatencyLogger")

    def test_to_severity_callable(self) -> None:
        import app.logging_config as lc
        assert callable(lc._to_severity)


# ===========================================================================
# page_orchestrator event_type instrumentation — static analysis
# ===========================================================================


class TestPageOrchestratorInstrumentation:
    """
    Verify that the page_orchestrator source contains the required
    structured-log event_type strings.
    """

    PAGE_ORCH_PATH = (
        BACKEND_ROOT / "app" / "websocket" / "page_orchestrator.py"
    )

    def _src(self) -> str:
        return self.PAGE_ORCH_PATH.read_text()

    def test_page_generation_started_event(self) -> None:
        assert "page_generation_started" in self._src()

    def test_page_generation_complete_event(self) -> None:
        assert "page_generation_complete" in self._src()

    def test_gemini_call_latency_event(self) -> None:
        assert "gemini_call_latency" in self._src()

    def test_page_asset_ready_event(self) -> None:
        assert "page_asset_ready" in self._src()

    def test_page_asset_failed_event(self) -> None:
        assert "page_asset_failed" in self._src()

    def test_duration_ms_logged(self) -> None:
        assert "duration_ms" in self._src()

    def test_imports_time_module(self) -> None:
        assert "import time" in self._src()

    def test_session_id_in_log_extras(self) -> None:
        assert '"session_id"' in self._src()

    def test_page_number_in_log_extras(self) -> None:
        assert '"page_number"' in self._src()


# ===========================================================================
# story_ws event_type instrumentation — static analysis
# ===========================================================================


class TestStoryWsInstrumentation:
    """
    Verify that story_ws.py contains the required structured-log event_type
    strings for WS lifecycle and voice command events.
    """

    STORY_WS_PATH = BACKEND_ROOT / "app" / "websocket" / "story_ws.py"

    def _src(self) -> str:
        return self.STORY_WS_PATH.read_text()

    def test_ws_connect_event(self) -> None:
        assert "ws_connect" in self._src()

    def test_ws_disconnect_event(self) -> None:
        assert "ws_disconnect" in self._src()

    def test_ws_rejected_event(self) -> None:
        assert "ws_rejected" in self._src()

    def test_voice_command_received_event(self) -> None:
        assert "voice_command_received" in self._src()

    def test_voice_command_applied_event(self) -> None:
        assert "voice_command_applied" in self._src()

    def test_session_status_changed_event(self) -> None:
        assert "session_status_changed" in self._src()

    def test_session_id_in_ws_connect_block(self) -> None:
        """session_id must be forwarded in the ws_connect extra."""
        src = self._src()
        ws_connect_idx = src.find('"ws_connect"')
        assert ws_connect_idx != -1
        nearby = src[max(0, ws_connect_idx - 200): ws_connect_idx + 200]
        assert "session_id" in nearby

    def test_session_id_in_ws_disconnect_block(self) -> None:
        src = self._src()
        ws_disconnect_idx = src.find('"ws_disconnect"')
        assert ws_disconnect_idx != -1
        nearby = src[max(0, ws_disconnect_idx - 200): ws_disconnect_idx + 200]
        assert "session_id" in nearby


# ===========================================================================
# safety_service structured log — static analysis
# ===========================================================================


class TestSafetyServiceInstrumentation:
    SAFETY_SVC_PATH = BACKEND_ROOT / "app" / "services" / "safety_service.py"

    def _src(self) -> str:
        return self.SAFETY_SVC_PATH.read_text()

    def test_safety_decision_event_type(self) -> None:
        assert "safety_decision" in self._src()

    def test_safety_classifier_error_event_type(self) -> None:
        assert "safety_classifier_error" in self._src()

    def test_category_logged_not_raw_input(self) -> None:
        src = self._src()
        # "category" key must appear in a structured-log extra block
        assert '"category"' in src

    def test_raw_input_not_in_extra_dicts(self) -> None:
        """
        The string 'raw_input' must not appear inside any extra={} dict passed
        to the logger — it may only appear as an attribute on SafetyDecision.
        """
        src = self._src()
        # Find all occurrences of "extra={" blocks and check none include raw_input
        import re
        extra_blocks = re.findall(r"extra=\{[^}]+\}", src)
        for block in extra_blocks:
            assert "raw_input" not in block, (
                f"raw_input found in logger extra block: {block}"
            )
            assert "utterance" not in block, (
                f"utterance found in logger extra block: {block}"
            )

    def test_main_uses_configure_logging(self) -> None:
        main_src = (BACKEND_ROOT / "app" / "main.py").read_text()
        assert "configure_logging" in main_src
        assert "from app.logging_config import configure_logging" in main_src

    def test_logging_config_file_exists(self) -> None:
        assert (BACKEND_ROOT / "app" / "logging_config.py").exists()
