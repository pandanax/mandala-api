"""Speech-to-text (голос → текст) через OpenAI-совместимый ``/audio/transcriptions``.

Провайдер конфигурируется из окружения (``STT_*`` с запасным использованием ``LLM_*``,
по аналогии с :mod:`mandala.llm.image_env`). Русский язык поддержан явно: по умолчанию
``STT_LANGUAGE=ru`` передаётся в запрос (Whisper-совместимо), можно очистить для
автоопределения. Секреты не хардкодятся — только env.

Слой провайдера, канало-агностичный: скачивание аудио из Telegram и оркестрация —
в :mod:`mandala.adapters.telegram.voice_transcribe`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping

import httpx
from pydantic import BaseModel, Field

_PROVIDER_ENV = "STT_PROVIDER"
_BASE_ENV = "STT_BASE_URL"
_KEY_ENV = "STT_API_KEY"
_MODEL_ENV = "STT_MODEL"
_LANG_ENV = "STT_LANGUAGE"
_FALLBACK_BASE = "LLM_BASE_URL"
_FALLBACK_KEY = "LLM_API_KEY"

# Русский язык обязателен: дефолтом просим провайдера транскрибировать на ru
# (для Whisper-совместимых API это заметно повышает точность на русской речи).
_DEFAULT_MODEL = "whisper-1"
_DEFAULT_LANGUAGE = "ru"

_STT_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)


class TranscriptionError(RuntimeError):
    """Сбой STT: HTTP, формат ответа или явная ошибка API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        provider_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_detail = provider_detail


class SttEnvSettings(BaseModel):
    """Читает ``STT_*`` с запасным использованием ``LLM_*`` для URL и ключа.

    ``enabled`` = False, если ни ``STT_BASE_URL``/``LLM_BASE_URL``, ни соответствующий ключ
    не заданы — тогда голос мягко деградирует (см. оркестрацию в адаптере), приложение не падает.
    """

    enabled: bool = Field(default=False)
    base_url: str = Field(default="")
    api_key: str = Field(default="")
    model: str = Field(default=_DEFAULT_MODEL)
    # Пустая строка = автоопределение языка; по умолчанию ``ru``.
    language: str = Field(default=_DEFAULT_LANGUAGE)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SttEnvSettings:
        env = dict(environ if environ is not None else os.environ)
        raw_p = (env.get(_PROVIDER_ENV) or "").strip().lower()

        base = (env.get(_BASE_ENV) or env.get(_FALLBACK_BASE) or "").strip()
        key = (env.get(_KEY_ENV) or env.get(_FALLBACK_KEY) or "").strip()
        model = (env.get(_MODEL_ENV) or "").strip() or _DEFAULT_MODEL
        # ``STT_LANGUAGE`` может быть явно пустым (автоопределение) — уважаем это.
        if _LANG_ENV in env:
            language = env[_LANG_ENV].strip()
        else:
            language = _DEFAULT_LANGUAGE

        if raw_p in ("off", "none", "disabled"):
            enabled = False
        elif raw_p in ("openai_compatible", "on", "enabled"):
            enabled = True
        else:
            # Автоопределение: включаем, если есть куда ходить (URL + ключ).
            enabled = bool(base and key)

        return cls(
            enabled=enabled and bool(base and key),
            base_url=base,
            api_key=key,
            model=model,
            language=language,
        )


class OpenAICompatibleSttClient:
    """POST ``{base_url}/audio/transcriptions`` (multipart), совместимо с Whisper API."""

    __slots__ = ("_api_key", "_base", "_client", "_default_model", "_language")

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str = _DEFAULT_MODEL,
        language: str = _DEFAULT_LANGUAGE,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._default_model = default_model.strip() or _DEFAULT_MODEL
        self._language = language.strip()
        self._client = client or httpx.Client(timeout=_STT_TIMEOUT)

    @classmethod
    def from_settings(
        cls,
        settings: SttEnvSettings,
        *,
        client: httpx.Client | None = None,
    ) -> OpenAICompatibleSttClient:
        return cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            default_model=settings.model,
            language=settings.language,
            client=client,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleSttClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "audio.ogg",
        content_type: str = "audio/ogg",
        model: str | None = None,
    ) -> str:
        """Вернуть распознанный текст (может быть пустым, если тишина/шум)."""
        if not audio:
            msg = "STT: пустое аудио"
            raise TranscriptionError(msg)

        data: dict[str, str] = {"model": (model or self._default_model)}
        if self._language:
            data["language"] = self._language
        files = {"file": (filename, audio, content_type)}
        url = f"{self._base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            response = self._client.post(url, headers=headers, data=data, files=files)
        except httpx.RequestError as e:
            raise TranscriptionError(
                f"STT HTTP request failed: {e}",
                provider_detail=str(e),
            ) from e

        return _parse_transcription(response)


def _parse_transcription(response: httpx.Response) -> str:
    text_preview = response.text[:512] if response.text else ""
    if response.status_code >= 400:
        detail = _extract_error_detail(response)
        raise TranscriptionError(
            f"STT API error HTTP {response.status_code}",
            status_code=response.status_code,
            provider_detail=detail or text_preview or None,
        )
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise TranscriptionError(
            "STT API returned non-JSON body",
            status_code=response.status_code,
            provider_detail=text_preview or None,
        ) from e
    if not isinstance(data, dict):
        raise TranscriptionError(
            "STT API JSON root must be an object",
            status_code=response.status_code,
        )
    text = data.get("text")
    if not isinstance(text, str):
        raise TranscriptionError(
            "STT API response has no text field",
            status_code=response.status_code,
            provider_detail=json.dumps(data)[:512],
        )
    return text.strip()


def _extract_error_detail(response: httpx.Response) -> str | None:
    try:
        data = response.json()
    except json.JSONDecodeError:
        return response.text[:512] if response.text else None
    if not isinstance(data, dict):
        return None
    err = data.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str):
            return msg
    if isinstance(err, str):
        return err
    return None


def build_stt_client_from_env(
    environ: Mapping[str, str] | None = None,
) -> OpenAICompatibleSttClient | None:
    """STT-клиент из окружения или ``None``, если провайдер не сконфигурирован."""
    settings = SttEnvSettings.from_env(environ)
    if not settings.enabled:
        return None
    return OpenAICompatibleSttClient.from_settings(settings)
