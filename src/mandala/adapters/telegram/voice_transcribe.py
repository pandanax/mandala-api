"""Telegram ``message.voice`` → Yandex SpeechKit → the existing text pipeline."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from mandala import metrics
from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.domain import InboundAttachment, InboundEvent
from mandala.observability import op_format
from mandala.services.transcription import (
    SpeechToTextProvider,
    SttConfigurationError,
    SttPermanentError,
    SttTemporaryError,
    SttTimeoutError,
    TranscriptionMetadata,
    build_stt_provider_from_env,
)

logger = logging.getLogger(__name__)

VOICE_ATTACHMENT_KIND = "voice"
_DEFAULT_MAX_DURATION_SECONDS = 30
_DEFAULT_MAX_FILE_SIZE_BYTES = 1_048_576
_DEFAULT_MAX_TRANSCRIPT_CHARS = 4096
_DEFAULT_IDEMPOTENCY_TTL_SECONDS = 3600
_DEFAULT_IDEMPOTENCY_MAX_ENTRIES = 10_000
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

_MSG_UNAVAILABLE = (
    "🎙️ Голосовые сейчас временно недоступны. Напишите, пожалуйста, текстом — я сразу отвечу."
)
_MSG_FAILED = "🎙️ Не получилось распознать голосовое. Попробуйте ещё раз или напишите текстом."
_MSG_TEMPORARY = (
    "🎙️ Сервис распознавания временно недоступен. Попробуйте чуть позже или напишите текстом."
)
_MSG_EMPTY = (
    "🎙️ Не расслышал в голосовом полезного текста. "
    "Попробуйте записать чуть чётче или напишите текстом."
)

VoiceProcessingKey = tuple[str, str, str, str]


class VoiceInputSettings(BaseModel):
    """Feature flag and pre-download limits, with an optional per-vertical flag."""

    enabled: bool = True
    max_duration_seconds: int = Field(default=_DEFAULT_MAX_DURATION_SECONDS, ge=1, le=30)
    max_file_size_bytes: int = Field(default=_DEFAULT_MAX_FILE_SIZE_BYTES, ge=1024, le=1_048_576)
    max_transcript_chars: int = Field(default=_DEFAULT_MAX_TRANSCRIPT_CHARS, ge=100, le=20_000)
    idempotency_ttl_seconds: int = Field(
        default=_DEFAULT_IDEMPOTENCY_TTL_SECONDS,
        ge=60,
        le=86_400,
    )
    idempotency_max_entries: int = Field(
        default=_DEFAULT_IDEMPOTENCY_MAX_ENTRIES,
        ge=100,
        le=100_000,
    )

    @classmethod
    def from_env(
        cls,
        vertical_id: str,
        environ: Mapping[str, str] | None = None,
    ) -> VoiceInputSettings:
        env = dict(environ if environ is not None else os.environ)
        suffix = re.sub(r"[^A-Z0-9]", "_", vertical_id.upper())
        enabled_raw = env.get(f"VOICE_ENABLED_{suffix}", env.get("VOICE_ENABLED", "true"))
        return cls(
            enabled=_bool_env(enabled_raw, default=True),
            max_duration_seconds=_int_env(
                env.get("VOICE_MAX_DURATION_SECONDS"),
                default=_DEFAULT_MAX_DURATION_SECONDS,
                minimum=1,
                maximum=30,
            ),
            max_file_size_bytes=_int_env(
                env.get("VOICE_MAX_FILE_SIZE_BYTES"),
                default=_DEFAULT_MAX_FILE_SIZE_BYTES,
                minimum=1024,
                maximum=1_048_576,
            ),
            max_transcript_chars=_int_env(
                env.get("VOICE_MAX_TRANSCRIPT_CHARS"),
                default=_DEFAULT_MAX_TRANSCRIPT_CHARS,
                minimum=100,
                maximum=20_000,
            ),
            idempotency_ttl_seconds=_int_env(
                env.get("VOICE_IDEMPOTENCY_TTL_SECONDS"),
                default=_DEFAULT_IDEMPOTENCY_TTL_SECONDS,
                minimum=60,
                maximum=86_400,
            ),
            idempotency_max_entries=_int_env(
                env.get("VOICE_IDEMPOTENCY_MAX_ENTRIES"),
                default=_DEFAULT_IDEMPOTENCY_MAX_ENTRIES,
                minimum=100,
                maximum=100_000,
            ),
        )


def _bool_env(raw: str | None, *, default: bool) -> bool:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _int_env(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


@dataclass(frozen=True, slots=True)
class VoiceResolution:
    """Result before the common text pipeline or a friendly terminal response."""

    event: InboundEvent
    soft_message: str | None = None
    skip_processing: bool = False
    processing_key: VoiceProcessingKey | None = None


class _VoiceDeduplicator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[VoiceProcessingKey, float] = {}

    def acquire(
        self,
        key: VoiceProcessingKey,
        *,
        ttl_seconds: int,
        max_entries: int,
    ) -> bool:
        now = time.monotonic()
        with self._lock:
            self._entries = {
                existing: expires for existing, expires in self._entries.items() if expires > now
            }
            if key in self._entries:
                return False
            if len(self._entries) >= max_entries:
                oldest = min(self._entries, key=self._entries.__getitem__)
                self._entries.pop(oldest, None)
            self._entries[key] = now + ttl_seconds
            return True

    def finish(self, key: VoiceProcessingKey, *, success: bool) -> None:
        if success:
            return
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_deduplicator = _VoiceDeduplicator()


def _select_voice_attachment(event: InboundEvent) -> InboundAttachment | None:
    return next(
        (
            attachment
            for attachment in event.attachments
            if attachment.kind == VOICE_ATTACHMENT_KIND and attachment.file_id
        ),
        None,
    )


def _needs_transcription(event: InboundEvent) -> bool:
    return (
        event.callback_data is None
        and not (event.text or "").strip()
        and _select_voice_attachment(event) is not None
    )


def _metadata_int(extra: dict[str, Any], key: str) -> int | None:
    value = extra.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _processing_key(event: InboundEvent) -> VoiceProcessingKey | None:
    raw = event.raw_ref or {}
    chat_id = raw.get("chat_id")
    message_id = raw.get("message_id")
    if chat_id is None or message_id is None:
        return None
    return (
        event.vertical_id,
        event.external_user_id,
        str(chat_id),
        str(message_id),
    )


def _voice_log(event: InboundEvent, stage: str, **fields: Any) -> None:
    raw = event.raw_ref or {}
    logger.info(
        "voice %s",
        op_format(
            vertical_id=event.vertical_id,
            channel=event.channel,
            stage=stage,
            update_id=raw.get("update_id"),
            message_id=raw.get("message_id"),
            **fields,
        ),
    )


def _reject(
    event: InboundEvent,
    *,
    message: str,
    reason: str,
    key: VoiceProcessingKey | None,
) -> VoiceResolution:
    _voice_log(event, "voice_rejected", outcome="rejected", reason=reason)
    metrics.record_voice(vertical_id=event.vertical_id, outcome=f"rejected_{reason}")
    return VoiceResolution(event=event, soft_message=message, processing_key=key)


def resolve_voice_to_text(
    event: InboundEvent,
    api: TelegramBotApiClient,
    *,
    stt_provider: SpeechToTextProvider | None = None,
    settings: VoiceInputSettings | None = None,
) -> VoiceResolution:
    """Resolve Telegram voice and return the same ``InboundEvent`` shape used by text."""
    if not _needs_transcription(event):
        return VoiceResolution(event=event)

    config = settings or VoiceInputSettings.from_env(event.vertical_id)
    attachment = _select_voice_attachment(event)
    assert attachment is not None
    extra = dict(attachment.model_extra or {})
    duration_seconds = _metadata_int(extra, "duration_seconds")
    file_size_bytes = _metadata_int(extra, "file_size_bytes")
    _voice_log(
        event,
        "voice_received",
        duration_seconds=duration_seconds,
        file_size_bytes=file_size_bytes,
    )
    metrics.record_voice(vertical_id=event.vertical_id, outcome="received")

    key = _processing_key(event)
    if key is not None and not _deduplicator.acquire(
        key,
        ttl_seconds=config.idempotency_ttl_seconds,
        max_entries=config.idempotency_max_entries,
    ):
        _voice_log(event, "voice_duplicate", outcome="skipped")
        metrics.record_voice(vertical_id=event.vertical_id, outcome="duplicate_skipped")
        return VoiceResolution(event=event, skip_processing=True)

    if not config.enabled:
        return _reject(event, message=_MSG_UNAVAILABLE, reason="disabled", key=key)
    if duration_seconds is not None and duration_seconds > config.max_duration_seconds:
        return _reject(
            event,
            message=(
                f"🎙️ Голосовое длиннее {config.max_duration_seconds} секунд. "
                "Запишите короче или отправьте текстом."
            ),
            reason="too_long",
            key=key,
        )
    if file_size_bytes is not None and file_size_bytes > config.max_file_size_bytes:
        return _reject(
            event,
            message="🎙️ Голосовой файл слишком большой. Запишите короче или отправьте текстом.",
            reason="too_large",
            key=key,
        )

    owns_provider = stt_provider is None
    try:
        provider = stt_provider or build_stt_provider_from_env(vertical_id=event.vertical_id)
    except SttConfigurationError:
        logger.warning("voice STT configuration invalid", exc_info=True)
        return _reject(event, message=_MSG_UNAVAILABLE, reason="configuration", key=key)
    if provider is None:
        return _reject(event, message=_MSG_UNAVAILABLE, reason="unconfigured", key=key)

    try:
        try:
            _voice_log(event, "voice_download_started")
            telegram_file = api.get_file(str(attachment.file_id))
            file_path = telegram_file.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                raise ValueError("Telegram getFile response has no file_path")
            audio = api.download_file(file_path)
            _voice_log(event, "voice_download_success", downloaded_bytes=len(audio))
        except Exception:  # noqa: BLE001 — Telegram errors become a safe user message
            logger.warning("voice download failed", exc_info=True)
            _voice_log(event, "voice_download_failed", outcome="failed")
            metrics.record_voice(vertical_id=event.vertical_id, outcome="download_failed")
            return VoiceResolution(event=event, soft_message=_MSG_FAILED, processing_key=key)

        if len(audio) > config.max_file_size_bytes:
            return _reject(
                event,
                message="🎙️ Голосовой файл слишком большой. Запишите короче или отправьте текстом.",
                reason="too_large_after_download",
                key=key,
            )

        metadata = TranscriptionMetadata(
            filename="voice.ogg",
            content_type=str(extra.get("mime_type") or "audio/ogg"),
            duration_seconds=duration_seconds,
            file_size_bytes=file_size_bytes or len(audio),
            file_unique_id=(
                str(extra["file_unique_id"]) if extra.get("file_unique_id") is not None else None
            ),
        )
        started = time.perf_counter()
        _voice_log(event, "stt_started", provider="yandex_speechkit", model="general")
        try:
            result = provider.transcribe(audio, metadata=metadata)
        except SttTimeoutError:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.warning("voice STT timeout", exc_info=True)
            _voice_log(event, "stt_timeout", outcome="timeout")
            metrics.record_stt(
                outcome="timeout",
                elapsed_ms=elapsed_ms,
                provider="yandex_speechkit",
                model="general",
            )
            return VoiceResolution(event=event, soft_message=_MSG_TEMPORARY, processing_key=key)
        except SttTemporaryError:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.warning("voice STT temporary failure", exc_info=True)
            _voice_log(event, "stt_failed", outcome="temporary_error")
            metrics.record_stt(
                outcome="temporary_error",
                elapsed_ms=elapsed_ms,
                provider="yandex_speechkit",
                model="general",
            )
            return VoiceResolution(event=event, soft_message=_MSG_TEMPORARY, processing_key=key)
        except SttPermanentError:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.warning("voice STT permanent failure", exc_info=True)
            _voice_log(event, "stt_failed", outcome="permanent_error")
            metrics.record_stt(
                outcome="permanent_error",
                elapsed_ms=elapsed_ms,
                provider="yandex_speechkit",
                model="general",
            )
            return VoiceResolution(event=event, soft_message=_MSG_FAILED, processing_key=key)
        except Exception:  # noqa: BLE001 — unknown provider failure still degrades safely
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.exception("voice STT unexpected failure")
            _voice_log(event, "stt_failed", outcome="unexpected_error")
            metrics.record_stt(
                outcome="unexpected_error",
                elapsed_ms=elapsed_ms,
                provider="yandex_speechkit",
                model="general",
            )
            return VoiceResolution(event=event, soft_message=_MSG_TEMPORARY, processing_key=key)

        metrics.record_stt(
            outcome="success",
            elapsed_ms=result.latency_ms,
            provider=result.provider,
            model=result.model,
        )
        normalized = " ".join(result.text.split())
        if not normalized or not any(character.isalnum() for character in normalized):
            _voice_log(event, "voice_empty_transcript", outcome="empty")
            metrics.record_voice(vertical_id=event.vertical_id, outcome="empty_transcript")
            return VoiceResolution(event=event, soft_message=_MSG_EMPTY, processing_key=key)
        if len(normalized) > config.max_transcript_chars:
            return _reject(
                event,
                message=(
                    "🎙️ Распознанный текст слишком длинный. Запишите короче или отправьте текстом."
                ),
                reason="transcript_too_long",
                key=key,
            )

        updated = event.model_copy(update={"text": normalized, "voice_transcribed": True})
        _voice_log(
            event,
            "voice_forwarded_to_text_pipeline",
            outcome="forwarded",
            provider=result.provider,
            model=result.model,
            transcript_length=len(normalized),
            stt_latency_ms=round(result.latency_ms, 1),
        )
        metrics.record_voice(vertical_id=event.vertical_id, outcome="forwarded")
        return VoiceResolution(event=updated, processing_key=key)
    finally:
        if owns_provider:
            provider.close()


def complete_voice_processing(resolution: VoiceResolution, *, success: bool) -> None:
    """Finish process-local deduplication and record business outcome separately from STT."""
    if resolution.processing_key is not None:
        _deduplicator.finish(resolution.processing_key, success=success)
    if resolution.event.voice_transcribed:
        outcome = "business_success" if success else "business_failed"
        metrics.record_voice(vertical_id=resolution.event.vertical_id, outcome=outcome)
        _voice_log(
            resolution.event,
            "voice_processing_success" if success else "voice_processing_failed",
            outcome="success" if success else "failed",
        )


def reset_voice_idempotency_for_tests() -> None:
    _deduplicator.clear()


__all__ = [
    "VoiceInputSettings",
    "VoiceResolution",
    "complete_voice_processing",
    "reset_voice_idempotency_for_tests",
    "resolve_voice_to_text",
]
