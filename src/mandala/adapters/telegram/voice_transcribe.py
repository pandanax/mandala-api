"""Голос Telegram → текст: скачивание аудио + STT, затем обычный пайплайн (голос→текст).

Оркестрация между :class:`~mandala.adapters.telegram.bot_api.TelegramBotApiClient`
(``getFile`` + скачивание) и провайдер-агностичным STT
(:mod:`mandala.services.transcription`). Вызывается из polling и webhook **после** маппинга
апдейта и **до** ``handle_inbound``: если это голосовое/аудио без текста — подменяем ``text``
транскриптом и идём в общий пайплайн; при любой ошибке STT — мягкое дружелюбное сообщение,
приложение не падает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.domain import InboundAttachment, InboundEvent
from mandala.services.transcription import (
    OpenAICompatibleSttClient,
    TranscriptionError,
    build_stt_client_from_env,
)

logger = logging.getLogger(__name__)

# Виды вложений, которые распознаём как речь (см. inbound_map).
VOICE_ATTACHMENT_KINDS = ("voice", "audio")

# Мягкие дружелюбные сообщения при сбоях STT (без технических деталей для пользователя).
_MSG_UNAVAILABLE = (
    "🎙️ Пока не могу разобрать голосовые. Напишите, пожалуйста, текстом — я сразу отвечу."
)
_MSG_FAILED = "🎙️ Не получилось распознать голосовое. Попробуйте ещё раз или напишите текстом."
_MSG_EMPTY = (
    "🎙️ Не расслышал в голосовом ни слова 🙃 Попробуйте записать чуть чётче или напишите текстом."
)

# Сопоставление mime → (имя файла, content-type) для multipart-запроса STT.
_MIME_EXT = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
}


@dataclass(frozen=True)
class VoiceResolution:
    """Результат попытки превратить голос в текст.

    - ``event`` — событие для дальнейшей обработки (обновлённое с текстом, либо исходное,
      если это не голос);
    - ``soft_message`` — если задан, ``event`` обрабатывать НЕ нужно: доставьте это
      дружелюбное сообщение пользователю.
    """

    event: InboundEvent
    soft_message: str | None = None


def _select_voice_attachment(event: InboundEvent) -> InboundAttachment | None:
    for att in event.attachments:
        if att.kind in VOICE_ATTACHMENT_KINDS and att.file_id:
            return att
    return None


def _needs_transcription(event: InboundEvent) -> bool:
    """Голос/аудио без уже присутствующего текста (например без подписи-caption)."""
    if event.callback_data is not None:
        return False
    if event.text and event.text.strip():
        return False
    return _select_voice_attachment(event) is not None


def _filename_and_type(mime: str | None) -> tuple[str, str]:
    m = (mime or "").strip().lower()
    ext = _MIME_EXT.get(m, "ogg")
    ctype = m if m else "audio/ogg"
    return f"audio.{ext}", ctype


def resolve_voice_to_text(
    event: InboundEvent,
    api: TelegramBotApiClient,
    *,
    stt_client: OpenAICompatibleSttClient | None = None,
) -> VoiceResolution:
    """Если событие — голосовое/аудио без текста, транскрибировать и вернуть новое событие.

    Не поднимает исключений наружу: любой сбой конвертируется в ``soft_message``.
    Для не-голосовых событий возвращает исходное ``event`` без изменений.
    """
    if not _needs_transcription(event):
        return VoiceResolution(event=event)

    att = _select_voice_attachment(event)
    assert att is not None  # гарантировано _needs_transcription
    file_id = str(att.file_id)
    extra = att.model_extra or {}
    mime = extra.get("mime_type")

    client = stt_client or build_stt_client_from_env()
    if client is None:
        logger.warning("STT не сконфигурирован (нет STT_*/LLM_* URL+ключа) — голос не распознан")
        return VoiceResolution(event=event, soft_message=_MSG_UNAVAILABLE)

    owns_client = stt_client is None
    try:
        try:
            meta = api.get_file(file_id)
            file_path = meta.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                logger.warning("telegram getFile без file_path для голосового сообщения")
                return VoiceResolution(event=event, soft_message=_MSG_FAILED)
            audio = api.download_file(file_path)
        except Exception:
            logger.warning("не удалось скачать аудио из Telegram", exc_info=True)
            return VoiceResolution(event=event, soft_message=_MSG_FAILED)

        filename, content_type = _filename_and_type(mime if isinstance(mime, str) else None)
        try:
            transcript = client.transcribe(audio, filename=filename, content_type=content_type)
        except TranscriptionError:
            logger.warning("STT: ошибка транскрипции голосового", exc_info=True)
            return VoiceResolution(event=event, soft_message=_MSG_FAILED)
    finally:
        if owns_client and client is not None:
            client.close()

    text = (transcript or "").strip()
    if not text:
        return VoiceResolution(event=event, soft_message=_MSG_EMPTY)

    updated = event.model_copy(update={"text": text, "voice_transcribed": True})
    logger.info("голос распознан (STT), длина текста=%d символов", len(text))
    return VoiceResolution(event=updated)
