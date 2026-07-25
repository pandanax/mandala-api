"""Тесты голос→текст: маппинг voice-апдейта, STT-клиент, мягкая деградация."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.voice_transcribe import resolve_voice_to_text
from mandala.domain import InboundAttachment, InboundEvent
from mandala.services.transcription import (
    OpenAICompatibleSttClient,
    SttEnvSettings,
    TranscriptionError,
)


def _voice_event(kind: str = "voice", mime: str = "audio/ogg") -> InboundEvent:
    return InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="42",
        text=None,
        attachments=[InboundAttachment(kind=kind, file_id="voice_fid_1", mime_type=mime)],
        raw_ref={"chat_id": 42},
    )


# --- Маппинг апдейта ---------------------------------------------------------


def test_map_voice_update() -> None:
    upd = {
        "update_id": 20,
        "message": {
            "message_id": 30,
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
    ev = telegram_update_to_inbound_event(upd, vertical_id="astrology")
    assert ev is not None
    assert ev.text is None
    assert len(ev.attachments) == 1
    att = ev.attachments[0]
    assert att.kind == "voice"
    assert att.file_id == "voice_abc"
    assert (att.model_extra or {}).get("mime_type") == "audio/ogg"


def test_map_audio_update() -> None:
    upd = {
        "update_id": 21,
        "message": {
            "message_id": 31,
            "from": {"id": 7, "is_bot": False},
            "chat": {"id": 7, "type": "private"},
            "date": 1,
            "audio": {"mime_type": "audio/mpeg", "file_id": "audio_xyz", "duration": 10},
        },
    }
    ev = telegram_update_to_inbound_event(upd, vertical_id="therapy")
    assert ev is not None
    assert ev.attachments[0].kind == "audio"
    assert ev.attachments[0].file_id == "audio_xyz"


# --- STT env config ----------------------------------------------------------


def test_stt_env_falls_back_to_llm_and_defaults_ru() -> None:
    s = SttEnvSettings.from_env({"LLM_BASE_URL": "https://api.example/v1", "LLM_API_KEY": "sk-x"})
    assert s.enabled is True
    assert s.base_url == "https://api.example/v1"
    assert s.api_key == "sk-x"
    assert s.model == "whisper-1"
    assert s.language == "ru"


def test_stt_env_disabled_without_creds() -> None:
    s = SttEnvSettings.from_env({})
    assert s.enabled is False


def test_stt_env_explicit_language_empty_means_auto() -> None:
    s = SttEnvSettings.from_env({"STT_BASE_URL": "u", "STT_API_KEY": "k", "STT_LANGUAGE": ""})
    assert s.language == ""


def test_stt_env_provider_off_disables() -> None:
    s = SttEnvSettings.from_env({"STT_PROVIDER": "off", "STT_BASE_URL": "u", "STT_API_KEY": "k"})
    assert s.enabled is False


# --- STT HTTP client ---------------------------------------------------------


def test_stt_client_posts_language_ru_and_parses_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/audio/transcriptions")
        assert "Bearer sk-test" in request.headers.get("authorization", "")
        body = request.content
        assert b"whisper-1" in body
        assert b'name="language"' in body
        assert b"ru" in body
        return httpx.Response(200, text=json.dumps({"text": "Привет, как дела?"}))

    transport = httpx.MockTransport(handler)
    cli = OpenAICompatibleSttClient(
        base_url="https://api.example/v1",
        api_key="sk-test",
        client=httpx.Client(transport=transport),
    )
    out = cli.transcribe(b"OggS-fake-bytes", filename="audio.ogg")
    assert out == "Привет, как дела?"


def test_stt_client_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    cli = OpenAICompatibleSttClient(
        base_url="https://api.example/v1",
        api_key="sk-test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        cli.transcribe(b"bytes")
    except TranscriptionError as e:
        assert e.status_code == 500
    else:  # pragma: no cover
        raise AssertionError("expected TranscriptionError")


# --- Оркестрация resolve_voice_to_text --------------------------------------


def test_resolve_voice_success() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file_1.oga"}
    api.download_file.return_value = b"OggS-bytes"
    stt = MagicMock()
    stt.transcribe.return_value = "расскажи про мой натал"

    res = resolve_voice_to_text(_voice_event(), api, stt_client=stt)

    assert res.soft_message is None
    assert res.event.text == "расскажи про мой натал"
    assert res.event.voice_transcribed is True
    api.get_file.assert_called_once_with("voice_fid_1")
    api.download_file.assert_called_once_with("voice/file_1.oga")


def test_resolve_voice_unavailable_when_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import mandala.adapters.telegram.voice_transcribe as vt

    monkeypatch.setattr(vt, "build_stt_client_from_env", lambda: None)
    api = MagicMock(spec=TelegramBotApiClient)
    res = resolve_voice_to_text(_voice_event(), api, stt_client=None)
    assert res.soft_message is not None
    assert res.event.text is None
    api.get_file.assert_not_called()


def test_resolve_voice_soft_message_on_download_error() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.side_effect = RuntimeError("net down")
    stt = MagicMock()

    res = resolve_voice_to_text(_voice_event(), api, stt_client=stt)

    assert res.soft_message is not None
    assert res.event.text is None
    stt.transcribe.assert_not_called()


def test_resolve_voice_soft_message_on_stt_error() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file_1.oga"}
    api.download_file.return_value = b"bytes"
    stt = MagicMock()
    stt.transcribe.side_effect = TranscriptionError("nope")

    res = resolve_voice_to_text(_voice_event(), api, stt_client=stt)

    assert res.soft_message is not None
    assert res.event.text is None


def test_resolve_voice_soft_message_on_empty_transcript() -> None:
    api = MagicMock(spec=TelegramBotApiClient)
    api.get_file.return_value = {"file_path": "voice/file_1.oga"}
    api.download_file.return_value = b"bytes"
    stt = MagicMock()
    stt.transcribe.return_value = "   "

    res = resolve_voice_to_text(_voice_event(), api, stt_client=stt)

    assert res.soft_message is not None
    assert res.event.text is None


def test_resolve_non_voice_passthrough() -> None:
    ev = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="42",
        text="обычный текст",
        raw_ref={"chat_id": 42},
    )
    api = MagicMock(spec=TelegramBotApiClient)
    stt = MagicMock()
    res = resolve_voice_to_text(ev, api, stt_client=stt)
    assert res.soft_message is None
    assert res.event is ev
    stt.transcribe.assert_not_called()
    api.get_file.assert_not_called()


# --- bot_api get_file / download_file ---------------------------------------


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
    meta = api.get_file("fid")
    assert meta["file_path"] == "voice/f.oga"
    data = api.download_file("voice/f.oga")
    assert data == b"AUDIO-BYTES"
