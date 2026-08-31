"""Telegram voice → Yandex SpeechKit → existing text pipeline regressions."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any, cast
from unittest.mock import MagicMock

import httpx
import pytest

from mandala.adapters.telegram import polling
from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.voice_transcribe import (
    VoiceInputSettings,
    complete_voice_processing,
    reset_voice_idempotency_for_tests,
    resolve_voice_to_text,
)
from mandala.domain import InboundAttachment, InboundEvent, OutboundMessage
from mandala.services.transcription import (
    SttConfigurationError,
    SttEnvSettings,
    SttPermanentError,
    SttTemporaryError,
    SttTimeoutError,
    TranscriptionMetadata,
    TranscriptionResult,
    YandexSpeechKitProvider,
    build_stt_provider_from_env,
)


@pytest.fixture(autouse=True)
def _clean_voice_dedup() -> Any:
    reset_voice_idempotency_for_tests()
    yield
    reset_voice_idempotency_for_tests()


def _voice_event(
    *,
    vertical_id: str = "astrology",
    message_id: int = 30,
    duration_seconds: int = 3,
    file_size_bytes: int = 4096,
) -> InboundEvent:
    return InboundEvent(
        vertical_id=vertical_id,
        channel="telegram",
        external_user_id="42",
        text=None,
        attachments=[
            InboundAttachment(
                kind="voice",
                file_id="voice_fid_1",
                mime_type="audio/ogg",
                file_unique_id="unique-1",
                duration_seconds=duration_seconds,
                file_size_bytes=file_size_bytes,
            )
        ],
        locale="ru",
        raw_ref={"chat_id": 42, "message_id": message_id, "update_id": 20},
    )


def _result(text: str = "расскажи про мой натал") -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        provider="yandex_speechkit",
        model="general",
        audio_duration_ms=3000,
        latency_ms=123.4,
    )


def _voice_update(*, message_id: int = 30) -> dict[str, Any]:
    return {
        "update_id": 20,
        "message": {
            "message_id": message_id,
            "from": {"id": 42, "is_bot": False, "language_code": "ru"},
            "chat": {"id": 42, "type": "private"},
            "date": 1,
            "voice": {
                "duration": 3,
                "mime_type": "audio/ogg",
                "file_id": "voice_abc",
                "file_unique_id": "u1",
                "file_size": 4096,
            },
        },
    }


# --- Telegram mapping ---------------------------------------------------------


def test_map_voice_update_preserves_stt_and_idempotency_metadata() -> None:
    event = telegram_update_to_inbound_event(_voice_update(), vertical_id="astrology")
    assert event is not None
    assert event.text is None
    assert event.raw_ref == {"chat_id": 42, "update_id": 20, "message_id": 30}
    attachment = event.attachments[0]
    assert attachment.kind == "voice"
    assert attachment.file_id == "voice_abc"
    assert attachment.model_extra == {
        "mime_type": "audio/ogg",
        "file_unique_id": "u1",
        "duration_seconds": 3,
        "file_size_bytes": 4096,
    }


def test_regular_audio_attachment_is_not_treated_as_voice() -> None:
    update = _voice_update()
    message = update["message"]
    assert isinstance(message, dict)
    message.pop("voice")
    message["audio"] = {"file_id": "audio-id", "mime_type": "audio/mpeg"}
    event = telegram_update_to_inbound_event(update, vertical_id="astrology")
    assert event is not None
    assert event.attachments == []


# --- Configuration ------------------------------------------------------------


def test_voice_settings_default_on_and_per_vertical_override() -> None:
    astrology = VoiceInputSettings.from_env(
        "astrology",
        {"VOICE_ENABLED": "false", "VOICE_ENABLED_ASTROLOGY": "true"},
    )
    nutrition = VoiceInputSettings.from_env(
        "nutrition",
        {"VOICE_ENABLED": "true", "VOICE_ENABLED_NUTRITION": "false"},
    )
    assert astrology.enabled is True
    assert nutrition.enabled is False
    assert astrology.max_duration_seconds == 30
    assert astrology.max_file_size_bytes == 1_048_576


def test_stt_settings_are_yandex_general_and_do_not_fall_back_to_llm_credentials() -> None:
    settings = SttEnvSettings.from_env(
        {
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_API_KEY": "must-not-be-used",
            "YANDEX_SPEECHKIT_USE_METADATA_IAM": "false",
        }
    )
    assert settings.provider == "yandex_speechkit"
    assert settings.model == "general"
    assert settings.language == "ru-RU"
    assert settings.configured is False
    assert (
        build_stt_provider_from_env(
            {
                "LLM_API_KEY": "must-not-be-used",
                "YANDEX_SPEECHKIT_USE_METADATA_IAM": "false",
            }
        )
        is None
    )


def test_stt_builder_can_use_attached_vm_service_account_token() -> None:
    provider = build_stt_provider_from_env(
        {},
        metadata_token_provider=lambda: "metadata-iam-token",
    )
    assert isinstance(provider, YandexSpeechKitProvider)
    provider.close()


def test_stt_language_supports_per_vertical_override() -> None:
    environment = {
        "STT_LANGUAGE": "ru-RU",
        "STT_LANGUAGE_NUTRITION": "kk-KZ",
    }
    assert SttEnvSettings.from_env(environment, vertical_id="astrology").language == "ru-RU"
    assert SttEnvSettings.from_env(environment, vertical_id="nutrition").language == "kk-KZ"


def test_stt_provider_off_disables_even_with_credentials() -> None:
    settings = SttEnvSettings.from_env(
        {"STT_PROVIDER": "off", "YANDEX_SPEECHKIT_API_KEY": "secret"}
    )
    assert settings.enabled is False
    assert (
        build_stt_provider_from_env({"STT_PROVIDER": "off", "YANDEX_SPEECHKIT_API_KEY": "secret"})
        is None
    )


def test_non_general_model_is_rejected_before_http() -> None:
    settings = SttEnvSettings.from_env(
        {"YANDEX_SPEECHKIT_API_KEY": "secret", "STT_MODEL": "general:rc"}
    )
    with pytest.raises(SttConfigurationError):
        YandexSpeechKitProvider.from_settings(settings)


# --- Yandex SpeechKit provider ------------------------------------------------


def test_yandex_provider_posts_raw_ogg_with_general_ru_and_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/speech/v1/stt:recognize"
        assert request.url.params["topic"] == "general"
        assert request.url.params["lang"] == "ru-RU"
        assert request.url.params["format"] == "oggopus"
        assert "folderId" not in request.url.params
        assert request.headers["authorization"] == "Api-Key api-secret"
        assert request.headers["content-type"] == "application/octet-stream"
        assert request.content == b"OggS-fake-bytes"
        return httpx.Response(200, json={"result": "Привет, как дела?"})

    provider = YandexSpeechKitProvider(
        api_key="api-secret",
        folder_id="must-be-ignored-for-api-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _delay: None,
    )
    result = provider.transcribe(
        b"OggS-fake-bytes",
        metadata=TranscriptionMetadata(duration_seconds=3),
    )
    assert result.text == "Привет, как дела?"
    assert result.provider == "yandex_speechkit"
    assert result.model == "general"
    assert result.audio_duration_ms == 3000


def test_yandex_provider_supports_iam_bearer_auth() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer iam-secret"
        assert request.url.params["folderId"] == "folder-for-user-token"
        return httpx.Response(200, json={"result": "готово"})

    provider = YandexSpeechKitProvider(
        iam_token="iam-secret",
        folder_id="folder-for-user-token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert provider.transcribe(b"OggS", metadata=TranscriptionMetadata()).text == "готово"


def test_yandex_provider_retries_5xx_bounded_then_succeeds() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"message": "temporary"})
        return httpx.Response(200, json={"result": "успех"})

    provider = YandexSpeechKitProvider(
        api_key="secret",
        max_retries=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=delays.append,
    )
    assert provider.transcribe(b"OggS", metadata=TranscriptionMetadata()).text == "успех"
    assert attempts == 3
    assert delays == [0.5, 1.0]


def test_yandex_provider_does_not_retry_permanent_401() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"message": "invalid credentials"})

    provider = YandexSpeechKitProvider(
        api_key="bad",
        max_retries=2,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _delay: None,
    )
    with pytest.raises(SttPermanentError) as exc:
        provider.transcribe(b"OggS", metadata=TranscriptionMetadata())
    assert exc.value.status_code == 401
    assert attempts == 1


def test_yandex_provider_retries_timeout_then_classifies_it() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("slow", request=request)

    provider = YandexSpeechKitProvider(
        api_key="secret",
        max_retries=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleeper=lambda _delay: None,
    )
    with pytest.raises(SttTimeoutError):
        provider.transcribe(b"OggS", metadata=TranscriptionMetadata())
    assert attempts == 2


# --- Voice orchestration ------------------------------------------------------


def test_feature_flag_off_performs_no_download_or_stt() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    provider = MagicMock()
    resolution = resolve_voice_to_text(
        _voice_event(vertical_id="nutrition"),
        api,
        stt_provider=provider,
        settings=VoiceInputSettings(enabled=False),
    )
    assert resolution.soft_message is not None
    api.get_file.assert_not_called()
    provider.transcribe.assert_not_called()


def test_unconfigured_stt_performs_no_telegram_download(monkeypatch: pytest.MonkeyPatch) -> None:
    import mandala.adapters.telegram.voice_transcribe as voice_module

    api = MagicMock(spec=TelegramBotApiClient)
    monkeypatch.setattr(
        voice_module,
        "build_stt_provider_from_env",
        lambda **_kwargs: None,
    )
    resolution = resolve_voice_to_text(_voice_event(), api)
    assert "недоступны" in (resolution.soft_message or "")
    api.get_file.assert_not_called()


def test_telegram_download_failure_never_calls_stt() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.side_effect = RuntimeError("network down")
    provider = MagicMock()
    resolution = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    assert "Не получилось распознать" in (resolution.soft_message or "")
    provider.transcribe.assert_not_called()


@pytest.mark.parametrize(
    ("duration", "size", "expected"),
    [(31, 1024, "30 секунд"), (3, 1_048_577, "слишком большой")],
)
def test_metadata_limits_reject_before_download_and_stt(
    duration: int,
    size: int,
    expected: str,
) -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    provider = MagicMock()
    resolution = resolve_voice_to_text(
        _voice_event(duration_seconds=duration, file_size_bytes=size),
        api,
        stt_provider=provider,
    )
    assert expected in (resolution.soft_message or "")
    api.get_file.assert_not_called()
    provider.transcribe.assert_not_called()


def test_downloaded_size_is_checked_when_telegram_metadata_underreports() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"x" * 1025
    provider = MagicMock()
    resolution = resolve_voice_to_text(
        _voice_event(file_size_bytes=10),
        api,
        stt_provider=provider,
        settings=VoiceInputSettings(max_file_size_bytes=1024),
    )
    assert "слишком большой" in (resolution.soft_message or "")
    provider.transcribe.assert_not_called()


def test_success_downloads_in_memory_and_forwards_normalized_text_with_metadata() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS-bytes"
    provider = MagicMock()
    provider.transcribe.return_value = _result("  запиши   завтрак  ")

    resolution = resolve_voice_to_text(
        _voice_event(vertical_id="nutrition"), api, stt_provider=provider
    )

    assert resolution.soft_message is None
    assert resolution.event.text == "запиши завтрак"
    assert resolution.event.voice_transcribed is True
    api.get_file.assert_called_once_with("voice_fid_1")
    api.download_file.assert_called_once_with("voice/file.oga")
    metadata = provider.transcribe.call_args.kwargs["metadata"]
    assert metadata == TranscriptionMetadata(
        filename="voice.ogg",
        content_type="audio/ogg",
        duration_seconds=3,
        file_size_bytes=4096,
        file_unique_id="unique-1",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SttTimeoutError("timeout", retryable=True), "временно недоступен"),
        (SttTemporaryError("temporary", retryable=True), "временно недоступен"),
        (SttPermanentError("bad audio"), "Не получилось распознать"),
    ],
)
def test_stt_error_taxonomy_has_safe_user_messages(error: Exception, expected: str) -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    provider = MagicMock()
    provider.transcribe.side_effect = error
    resolution = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    assert expected in (resolution.soft_message or "")
    assert resolution.event.text is None


def test_empty_or_punctuation_only_transcript_is_rejected() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    provider = MagicMock()
    provider.transcribe.return_value = _result(" ... !!! ")
    resolution = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    assert "полезного текста" in (resolution.soft_message or "")


def test_full_transcript_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret_transcript = "секретная фраза пользователя 987654321"
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    provider = MagicMock()
    provider.transcribe.return_value = _result(secret_transcript)
    with caplog.at_level(logging.INFO):
        resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    assert secret_transcript not in caplog.text
    assert "transcript_length=" in caplog.text


def test_duplicate_message_skips_second_download_stt_and_business() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    provider = MagicMock()
    provider.transcribe.return_value = _result()
    first = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    complete_voice_processing(first, success=True)

    duplicate = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    assert duplicate.skip_processing is True
    assert api.get_file.call_count == 1
    assert provider.transcribe.call_count == 1


def test_failed_business_processing_releases_idempotency_key_for_retry() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    provider = MagicMock()
    provider.transcribe.return_value = _result()
    first = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    complete_voice_processing(first, success=False)
    retry = resolve_voice_to_text(_voice_event(), api, stt_provider=provider)
    assert retry.skip_processing is False
    assert provider.transcribe.call_count == 2


def test_owned_provider_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import mandala.adapters.telegram.voice_transcribe as voice_module

    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    provider = MagicMock()
    provider.transcribe.return_value = _result()
    monkeypatch.setattr(
        voice_module,
        "build_stt_provider_from_env",
        lambda **_kwargs: provider,
    )
    resolve_voice_to_text(_voice_event(), api)
    provider.close.assert_called_once_with()


def test_non_voice_text_passthrough() -> None:
    event = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="42",
        text="обычный текст",
        raw_ref={"chat_id": 42, "message_id": 1},
    )
    api = MagicMock(spec=TelegramBotApiClient)
    provider = MagicMock()
    resolution = resolve_voice_to_text(event, api, stt_provider=provider)
    assert resolution.event is event
    assert resolution.processing_key is None
    provider.transcribe.assert_not_called()
    api.get_file.assert_not_called()


# --- Shared pipeline equivalence ---------------------------------------------


def test_text_and_voice_transcript_use_the_same_polling_business_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock()
    provider.transcribe.return_value = _result("одинаковая команда")
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file.oga"}
    api.download_file.return_value = b"OggS"
    engine = MagicMock()
    engine.begin.return_value = nullcontext(cast(Any, object()))
    seen: list[tuple[str | None, bool]] = []
    delivered: list[str | None] = []

    monkeypatch.setattr(polling, "process_telegram_billing_update", lambda *a, **kw: False)
    monkeypatch.setattr(polling, "answer_callback_query_if_present", lambda *a, **kw: None)
    monkeypatch.setattr(
        "mandala.adapters.telegram.voice_transcribe.build_stt_provider_from_env",
        lambda **_kwargs: provider,
    )
    monkeypatch.setattr(
        polling,
        "run_with_typing_keepalive",
        lambda _api, _chat_id, fn: fn(),
    )

    def fake_handle(event: InboundEvent, _conn: object) -> list[OutboundMessage]:
        seen.append((event.text, event.voice_transcribed))
        return [OutboundMessage(text=f"RESULT:{event.text}")]

    def fake_deliver(
        _api: object,
        *,
        chat_id: int,
        messages: list[OutboundMessage],
        vertical_id: str,
    ) -> dict[str, str]:
        assert chat_id == 42
        assert vertical_id == "nutrition"
        delivered.append(messages[0].text)
        return {}

    monkeypatch.setattr(polling, "handle_inbound", fake_handle)
    monkeypatch.setattr(polling, "deliver_outbound_messages", fake_deliver)
    monkeypatch.setattr(polling, "persist_photo_file_ids", lambda *a, **kw: None)

    text_update = _voice_update(message_id=1)
    text_message = text_update["message"]
    assert isinstance(text_message, dict)
    text_message.pop("voice")
    text_message["text"] = "одинаковая команда"
    polling.process_telegram_update(
        text_update,
        vertical_id="nutrition",
        engine=engine,
        api=api,
    )
    polling.process_telegram_update(
        _voice_update(message_id=2),
        vertical_id="nutrition",
        engine=engine,
        api=api,
    )

    assert seen == [("одинаковая команда", False), ("одинаковая команда", True)]
    assert delivered == ["RESULT:одинаковая команда", "RESULT:одинаковая команда"]


# --- Telegram file API --------------------------------------------------------


def test_bot_api_get_file_and_download() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "voice/f.oga"}})
        if "/file/bot" in request.url.path and request.url.path.endswith("voice/f.oga"):
            return httpx.Response(200, content=b"AUDIO-BYTES")
        return httpx.Response(404, json={"ok": False, "description": "not found"})

    api = TelegramBotApiClient(
        "123:ABC",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert api.get_file("fid")["file_path"] == "voice/f.oga"
    assert api.download_file("voice/f.oga") == b"AUDIO-BYTES"
