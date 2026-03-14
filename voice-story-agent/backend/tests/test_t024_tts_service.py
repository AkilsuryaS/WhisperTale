"""
Tests for T-024: TTSService.

Strategy
--------
The Cloud TTS async client is injected via the TTSService constructor so no
real GCP calls are made. The google.cloud.texttospeech module is mocked via
patch.dict(sys.modules) for tests that need to verify TTS request objects.
_call_tts is patched for higher-level retry / error tests.

Covers:
  synthesize — success:
    T24-01  returns non-empty bytes on first attempt
    T24-02  _call_tts is called with the script and voice_config
    T24-03  Cloud TTS client synthesize_speech is called with correct params
    T24-04  audio encoding is MP3
    T24-05  speaking_rate from VoiceConfig is passed to AudioConfig
    T24-06  voice_name is passed to VoiceSelectionParams
    T24-07  language_code is passed to VoiceSelectionParams
    T24-08  script is passed as SynthesisInput.text

  retry logic:
    T24-09  first attempt exception → retries a second time
    T24-10  success on second attempt (after first failure) → returns bytes
    T24-11  both attempts fail → raises TTSError
    T24-12  TTSError.cause is the last exception on total failure
    T24-13  asyncio.sleep(_RETRY_BACKOFF_SECONDS) called between attempts
    T24-14  no sleep on first-attempt success

  TTSError:
    T24-15  TTSError message mentions number of attempts

  VoiceConfig dataclass:
    T24-16  VoiceConfig stores voice_name, language_code, speaking_rate
    T24-17  VoiceConfig default speaking_rate is 0.85

  default_voice_config():
    T24-18  returns VoiceConfig with speaking_rate=0.85
    T24-19  uses settings.TTS_VOICE_NAME for voice_name
    T24-20  uses settings.TTS_LANGUAGE_CODE for language_code
    T24-21  returns a VoiceConfig instance

  lazy client creation:
    T24-22  _get_client is idempotent
    T24-23  _get_client returns the injected client directly

  logging:
    T24-24  successful synthesis is logged
    T24-25  failed attempt is logged with error type
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import TTSError
from app.services.tts_service import (
    TTSService,
    VoiceConfig,
    _DEFAULT_SPEAKING_RATE,
    _RETRY_BACKOFF_SECONDS,
    default_voice_config,
)

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 200

SCRIPT = "Pip the small blue rabbit stepped into the sunlit Enchanted Meadow."
VOICE_CFG = VoiceConfig(
    voice_name="en-US-Neural2-F",
    language_code="en-US",
    speaking_rate=0.85,
)


def _make_mock_response(audio_bytes: bytes = FAKE_MP3) -> MagicMock:
    resp = MagicMock()
    resp.audio_content = audio_bytes
    return resp


def _make_mock_tts_client(
    audio_bytes: bytes = FAKE_MP3,
    side_effect: Exception | None = None,
) -> MagicMock:
    client = MagicMock()
    if side_effect is not None:
        client.synthesize_speech = AsyncMock(side_effect=side_effect)
    else:
        client.synthesize_speech = AsyncMock(return_value=_make_mock_response(audio_bytes))
    return client


def _make_svc(client: MagicMock | None = None) -> TTSService:
    return TTSService(tts_client=client or _make_mock_tts_client())


def _mock_tts_module() -> MagicMock:
    """Build a mock google.cloud.texttospeech module with AudioEncoding.MP3."""
    mock_tts = MagicMock()
    mock_tts.AudioEncoding.MP3 = "MP3"
    mock_tts.SynthesisInput.side_effect = lambda **kw: MagicMock(**kw)
    mock_tts.VoiceSelectionParams.side_effect = lambda **kw: MagicMock(**kw)
    mock_tts.AudioConfig.side_effect = lambda **kw: MagicMock(**kw)
    mock_tts.TextToSpeechAsyncClient.return_value = _make_mock_tts_client()
    return mock_tts


def _patched_modules(mock_tts: MagicMock) -> dict:
    return {
        "google.cloud.texttospeech": mock_tts,
        "google.cloud": MagicMock(texttospeech=mock_tts),
    }


# ---------------------------------------------------------------------------
# T24-01 — T24-08: synthesize success
# ---------------------------------------------------------------------------


class TestSynthesizeSuccess:
    @pytest.mark.anyio
    async def test_returns_non_empty_bytes(self) -> None:
        """T24-01: returns non-empty bytes on a successful synthesis."""
        svc = _make_svc()
        with patch.object(svc, "_call_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            result = await svc.synthesize(SCRIPT, VOICE_CFG)
        assert isinstance(result, bytes)
        assert len(result) > 0

    @pytest.mark.anyio
    async def test_call_tts_invoked_with_script_and_config(self) -> None:
        """T24-02: _call_tts is called with the provided script and VoiceConfig."""
        svc = _make_svc()
        with patch.object(svc, "_call_tts", new_callable=AsyncMock, return_value=FAKE_MP3) as mock:
            await svc.synthesize(SCRIPT, VOICE_CFG)
        mock.assert_called_once_with(SCRIPT, VOICE_CFG)

    @pytest.mark.anyio
    async def test_synthesize_speech_called_once(self) -> None:
        """T24-03: Cloud TTS synthesize_speech is called exactly once on success."""
        mock_tts = _mock_tts_module()
        client = _make_mock_tts_client()
        svc = _make_svc(client)
        with patch.dict(sys.modules, _patched_modules(mock_tts)):
            await svc.synthesize(SCRIPT, VOICE_CFG)
        client.synthesize_speech.assert_called_once()

    @pytest.mark.anyio
    async def test_mp3_audio_encoding_used(self) -> None:
        """T24-04: AudioConfig uses MP3 audio encoding."""
        mock_tts = _mock_tts_module()
        captured: dict = {}

        def capture_audio_cfg(**kw: object) -> MagicMock:
            m = MagicMock()
            m.audio_encoding = kw.get("audio_encoding")
            m.speaking_rate = kw.get("speaking_rate")
            captured["audio_cfg"] = m
            return m

        mock_tts.AudioConfig.side_effect = capture_audio_cfg
        client = _make_mock_tts_client()
        svc = _make_svc(client)

        with patch.dict(sys.modules, _patched_modules(mock_tts)):
            await svc.synthesize(SCRIPT, VOICE_CFG)

        assert captured["audio_cfg"].audio_encoding == mock_tts.AudioEncoding.MP3

    @pytest.mark.anyio
    async def test_speaking_rate_passed_to_audio_config(self) -> None:
        """T24-05: speaking_rate from VoiceConfig is set in AudioConfig."""
        mock_tts = _mock_tts_module()
        captured: dict = {}

        def capture_audio_cfg(**kw: object) -> MagicMock:
            m = MagicMock()
            m.speaking_rate = kw.get("speaking_rate")
            captured["audio_cfg"] = m
            return m

        mock_tts.AudioConfig.side_effect = capture_audio_cfg
        client = _make_mock_tts_client()
        svc = _make_svc(client)
        cfg = VoiceConfig(voice_name="en-US-Neural2-C", language_code="en-US", speaking_rate=0.75)

        with patch.dict(sys.modules, _patched_modules(mock_tts)):
            await svc.synthesize(SCRIPT, cfg)

        assert captured["audio_cfg"].speaking_rate == 0.75

    @pytest.mark.anyio
    async def test_voice_name_passed_to_voice_params(self) -> None:
        """T24-06: voice_name from VoiceConfig is set in VoiceSelectionParams."""
        mock_tts = _mock_tts_module()
        captured: dict = {}

        def capture_voice(**kw: object) -> MagicMock:
            m = MagicMock()
            m.name = kw.get("name")
            m.language_code = kw.get("language_code")
            captured["voice"] = m
            return m

        mock_tts.VoiceSelectionParams.side_effect = capture_voice
        client = _make_mock_tts_client()
        svc = _make_svc(client)

        with patch.dict(sys.modules, _patched_modules(mock_tts)):
            await svc.synthesize(SCRIPT, VOICE_CFG)

        assert captured["voice"].name == VOICE_CFG.voice_name

    @pytest.mark.anyio
    async def test_language_code_passed_to_voice_params(self) -> None:
        """T24-07: language_code from VoiceConfig is set in VoiceSelectionParams."""
        mock_tts = _mock_tts_module()
        captured: dict = {}

        def capture_voice(**kw: object) -> MagicMock:
            m = MagicMock()
            m.name = kw.get("name")
            m.language_code = kw.get("language_code")
            captured["voice"] = m
            return m

        mock_tts.VoiceSelectionParams.side_effect = capture_voice
        client = _make_mock_tts_client()
        svc = _make_svc(client)

        with patch.dict(sys.modules, _patched_modules(mock_tts)):
            await svc.synthesize(SCRIPT, VOICE_CFG)

        assert captured["voice"].language_code == VOICE_CFG.language_code

    @pytest.mark.anyio
    async def test_script_set_as_synthesis_input_text(self) -> None:
        """T24-08: the script string is passed as SynthesisInput.text."""
        mock_tts = _mock_tts_module()
        captured: dict = {}

        def capture_input(**kw: object) -> MagicMock:
            m = MagicMock()
            m.text = kw.get("text")
            captured["input"] = m
            return m

        mock_tts.SynthesisInput.side_effect = capture_input
        client = _make_mock_tts_client()
        svc = _make_svc(client)

        with patch.dict(sys.modules, _patched_modules(mock_tts)):
            await svc.synthesize(SCRIPT, VOICE_CFG)

        assert captured["input"].text == SCRIPT


# ---------------------------------------------------------------------------
# T24-09 — T24-14: retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    @pytest.mark.anyio
    async def test_first_failure_triggers_retry(self) -> None:
        """T24-09: first attempt failure triggers a second _call_tts call."""
        call_count = 0

        async def fake_call(script: str, cfg: VoiceConfig) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return FAKE_MP3

        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=fake_call):
            with patch("app.services.tts_service.asyncio.sleep", new_callable=AsyncMock):
                await svc.synthesize(SCRIPT, VOICE_CFG)
        assert call_count == 2

    @pytest.mark.anyio
    async def test_success_on_second_attempt_returns_bytes(self) -> None:
        """T24-10: second attempt succeeds → returns valid bytes."""
        attempt = 0

        async def fake_call(script: str, cfg: VoiceConfig) -> bytes:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("first failure")
            return FAKE_MP3

        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=fake_call):
            with patch("app.services.tts_service.asyncio.sleep", new_callable=AsyncMock):
                result = await svc.synthesize(SCRIPT, VOICE_CFG)
        assert result == FAKE_MP3

    @pytest.mark.anyio
    async def test_both_attempts_fail_raises_tts_error(self) -> None:
        """T24-11: both attempts fail → TTSError is raised."""
        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=RuntimeError("API down")):
            with patch("app.services.tts_service.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(TTSError):
                    await svc.synthesize(SCRIPT, VOICE_CFG)

    @pytest.mark.anyio
    async def test_tts_error_cause_is_last_exception(self) -> None:
        """T24-12: TTSError.cause is the last exception raised."""
        second_exc = RuntimeError("second failure")

        async def side_effect(script: str, cfg: VoiceConfig) -> bytes:
            raise second_exc

        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=side_effect):
            with patch("app.services.tts_service.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(TTSError) as exc_info:
                    await svc.synthesize(SCRIPT, VOICE_CFG)
        assert exc_info.value.cause is second_exc

    @pytest.mark.anyio
    async def test_sleep_called_between_attempts(self) -> None:
        """T24-13: asyncio.sleep is called with _RETRY_BACKOFF_SECONDS between retries."""
        attempt = 0

        async def fake_call(script: str, cfg: VoiceConfig) -> bytes:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("fail")
            return FAKE_MP3

        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=fake_call):
            with patch(
                "app.services.tts_service.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep:
                await svc.synthesize(SCRIPT, VOICE_CFG)
        mock_sleep.assert_called_once_with(_RETRY_BACKOFF_SECONDS)

    @pytest.mark.anyio
    async def test_no_sleep_on_first_attempt_success(self) -> None:
        """T24-14: asyncio.sleep is NOT called when first attempt succeeds."""
        svc = _make_svc()
        with patch.object(svc, "_call_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            with patch(
                "app.services.tts_service.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep:
                await svc.synthesize(SCRIPT, VOICE_CFG)
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# T24-15: TTSError message
# ---------------------------------------------------------------------------


class TestTTSError:
    @pytest.mark.anyio
    async def test_tts_error_message_mentions_attempts(self) -> None:
        """T24-15: TTSError message mentions the number of attempts."""
        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=RuntimeError("fail")):
            with patch("app.services.tts_service.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(TTSError) as exc_info:
                    await svc.synthesize(SCRIPT, VOICE_CFG)
        assert "2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T24-16 — T24-17: VoiceConfig dataclass
# ---------------------------------------------------------------------------


class TestVoiceConfig:
    def test_stores_all_fields(self) -> None:
        """T24-16: VoiceConfig stores voice_name, language_code, speaking_rate."""
        cfg = VoiceConfig(
            voice_name="en-US-Neural2-C",
            language_code="en-US",
            speaking_rate=0.9,
        )
        assert cfg.voice_name == "en-US-Neural2-C"
        assert cfg.language_code == "en-US"
        assert cfg.speaking_rate == 0.9

    def test_default_speaking_rate_is_0_85(self) -> None:
        """T24-17: VoiceConfig default speaking_rate is 0.85."""
        cfg = VoiceConfig(voice_name="en-US-Neural2-F", language_code="en-US")
        assert cfg.speaking_rate == 0.85


# ---------------------------------------------------------------------------
# T24-18 — T24-21: default_voice_config()
# ---------------------------------------------------------------------------


class TestDefaultVoiceConfig:
    def test_speaking_rate_is_0_85(self) -> None:
        """T24-18: default_voice_config returns VoiceConfig with speaking_rate=0.85."""
        cfg = default_voice_config()
        assert cfg.speaking_rate == _DEFAULT_SPEAKING_RATE

    def test_uses_settings_tts_voice_name(self) -> None:
        """T24-19: voice_name comes from settings.TTS_VOICE_NAME."""
        with patch("app.services.tts_service.settings") as mock_settings:
            mock_settings.TTS_VOICE_NAME = "en-US-Neural2-J"
            mock_settings.TTS_LANGUAGE_CODE = "en-US"
            cfg = default_voice_config()
        assert cfg.voice_name == "en-US-Neural2-J"

    def test_uses_settings_tts_language_code(self) -> None:
        """T24-20: language_code comes from settings.TTS_LANGUAGE_CODE."""
        with patch("app.services.tts_service.settings") as mock_settings:
            mock_settings.TTS_VOICE_NAME = "en-GB-Neural2-A"
            mock_settings.TTS_LANGUAGE_CODE = "en-GB"
            cfg = default_voice_config()
        assert cfg.language_code == "en-GB"

    def test_returns_voice_config_instance(self) -> None:
        """T24-21: default_voice_config() returns a VoiceConfig instance."""
        cfg = default_voice_config()
        assert isinstance(cfg, VoiceConfig)


# ---------------------------------------------------------------------------
# T24-22 — T24-23: lazy client creation
# ---------------------------------------------------------------------------


class TestLazyClientCreation:
    def test_get_client_is_idempotent(self) -> None:
        """T24-22: _get_client returns the same object on repeated calls."""
        injected = _make_mock_tts_client()
        svc = TTSService(tts_client=injected)
        c1 = svc._get_client()
        c2 = svc._get_client()
        assert c1 is c2

    def test_get_client_returns_injected_client(self) -> None:
        """T24-23: _get_client returns the injected client without creating a new one."""
        injected = _make_mock_tts_client()
        svc = TTSService(tts_client=injected)
        assert svc._get_client() is injected


# ---------------------------------------------------------------------------
# T24-24 — T24-25: logging
# ---------------------------------------------------------------------------


class TestLogging:
    @pytest.mark.anyio
    async def test_successful_synthesis_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """T24-24: successful synthesis is logged with char and byte info."""
        import logging

        svc = _make_svc()
        with patch.object(svc, "_call_tts", new_callable=AsyncMock, return_value=FAKE_MP3):
            with caplog.at_level(logging.INFO, logger="app.services.tts_service"):
                await svc.synthesize(SCRIPT, VOICE_CFG)

        assert any(
            "synthesised" in record.message or str(len(SCRIPT)) in record.message
            for record in caplog.records
        )

    @pytest.mark.anyio
    async def test_failed_attempt_logged_with_error_type(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """T24-25: failed attempt is logged with the error type name."""
        import logging

        svc = _make_svc()
        with patch.object(svc, "_call_tts", side_effect=RuntimeError("network error")):
            with patch("app.services.tts_service.asyncio.sleep", new_callable=AsyncMock):
                with caplog.at_level(logging.WARNING, logger="app.services.tts_service"):
                    with pytest.raises(TTSError):
                        await svc.synthesize(SCRIPT, VOICE_CFG)

        assert any("RuntimeError" in record.message for record in caplog.records)
