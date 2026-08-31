"""Provider-agnostic speech-to-text with a Yandex SpeechKit implementation.

Telegram orchestration lives in :mod:`mandala.adapters.telegram.voice_transcribe`.
This module only accepts in-memory Ogg/Opus bytes and calls the configured STT provider;
audio is never written to disk or retained here.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel, Field

_DEFAULT_ENDPOINT = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
_DEFAULT_MODEL = "general"
_DEFAULT_LANGUAGE = "ru-RU"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_RETRIES = 2
_TRANSIENT_STATUS_CODES = frozenset({408, 429})
_METADATA_TOKEN_URL = (
    "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"
)
_TRUE = frozenset({"1", "true", "yes", "on"})
_metadata_token_lock = threading.Lock()
_metadata_token_cache: tuple[float, str] | None = None


class TranscriptionError(RuntimeError):
    """Base STT failure with retryability preserved for UX/telemetry."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class SttTimeoutError(TranscriptionError):
    """SpeechKit timed out after bounded retries."""


class SttTemporaryError(TranscriptionError):
    """Network, rate-limit or provider 5xx failure."""


class SttPermanentError(TranscriptionError):
    """Bad credentials/request/audio or an invalid provider response."""


class SttConfigurationError(SttPermanentError):
    """Unsupported or incomplete STT runtime configuration."""


@dataclass(frozen=True, slots=True)
class TranscriptionMetadata:
    """Minimal source metadata passed to an STT provider (never audio content)."""

    filename: str = "voice.ogg"
    content_type: str = "audio/ogg"
    duration_seconds: int | None = None
    file_size_bytes: int | None = None
    file_unique_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    provider: str
    model: str
    audio_duration_ms: int | None
    latency_ms: float
    confidence: float | None = None


class SpeechToTextProvider(Protocol):
    """Channel-independent STT boundary."""

    def transcribe(
        self,
        audio: bytes,
        *,
        metadata: TranscriptionMetadata,
    ) -> TranscriptionResult: ...

    def close(self) -> None: ...


class SttEnvSettings(BaseModel):
    """Yandex SpeechKit settings loaded only from runtime environment."""

    enabled: bool = True
    provider: str = "yandex_speechkit"
    endpoint: str = _DEFAULT_ENDPOINT
    api_key: str = ""
    iam_token: str = ""
    folder_id: str = ""
    model: str = _DEFAULT_MODEL
    language: str = _DEFAULT_LANGUAGE
    timeout_seconds: float = Field(default=_DEFAULT_TIMEOUT_SECONDS, ge=1, le=120)
    max_retries: int = Field(default=_DEFAULT_MAX_RETRIES, ge=0, le=5)
    use_metadata_iam: bool = True

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key or self.iam_token)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        vertical_id: str | None = None,
    ) -> SttEnvSettings:
        env = dict(environ if environ is not None else os.environ)
        provider = (env.get("STT_PROVIDER") or "yandex_speechkit").strip().lower()
        enabled = provider not in {"off", "none", "disabled"}
        suffix = "" if not vertical_id else "_" + _vertical_suffix(vertical_id)
        language = (
            env.get(f"STT_LANGUAGE{suffix}")
            if suffix and f"STT_LANGUAGE{suffix}" in env
            else env.get("STT_LANGUAGE")
        )
        return cls(
            enabled=enabled,
            provider=provider,
            endpoint=(env.get("YANDEX_SPEECHKIT_ENDPOINT") or _DEFAULT_ENDPOINT).strip(),
            api_key=(env.get("YANDEX_SPEECHKIT_API_KEY") or "").strip(),
            iam_token=(
                env.get("YANDEX_SPEECHKIT_IAM_TOKEN") or env.get("YC_IAM_TOKEN") or ""
            ).strip(),
            folder_id=(
                env.get("YANDEX_SPEECHKIT_FOLDER_ID") or env.get("YC_FOLDER_ID") or ""
            ).strip(),
            model=(env.get("STT_MODEL") or _DEFAULT_MODEL).strip(),
            language=(language or _DEFAULT_LANGUAGE).strip(),
            timeout_seconds=_float_env(
                env.get("STT_TIMEOUT_SECONDS"),
                default=_DEFAULT_TIMEOUT_SECONDS,
                minimum=1,
                maximum=120,
            ),
            max_retries=_int_env(
                env.get("STT_MAX_RETRIES"),
                default=_DEFAULT_MAX_RETRIES,
                minimum=0,
                maximum=5,
            ),
            use_metadata_iam=(
                env.get("YANDEX_SPEECHKIT_USE_METADATA_IAM", "true").strip().lower() in _TRUE
            ),
        )


def _float_env(raw: str | None, *, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(raw) if raw is not None else default
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _int_env(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _vertical_suffix(vertical_id: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in vertical_id.upper())


class YandexSpeechKitProvider:
    """Yandex SpeechKit synchronous v1: raw Ogg/Opus body, model ``general``."""

    __slots__ = (
        "_api_key",
        "_client",
        "_endpoint",
        "_folder_id",
        "_iam_token",
        "_language",
        "_max_retries",
        "_sleep",
    )

    def __init__(
        self,
        *,
        endpoint: str = _DEFAULT_ENDPOINT,
        api_key: str = "",
        iam_token: str = "",
        folder_id: str = "",
        language: str = _DEFAULT_LANGUAGE,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip() and not iam_token.strip():
            raise SttConfigurationError("SpeechKit credentials are not configured")
        self._endpoint = endpoint.strip() or _DEFAULT_ENDPOINT
        self._api_key = api_key.strip()
        self._iam_token = iam_token.strip()
        self._folder_id = folder_id.strip()
        self._language = language.strip() or _DEFAULT_LANGUAGE
        self._max_retries = min(max(int(max_retries), 0), 5)
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout_seconds))
        self._sleep = sleeper

    @classmethod
    def from_settings(
        cls,
        settings: SttEnvSettings,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> YandexSpeechKitProvider:
        if settings.provider != "yandex_speechkit":
            raise SttConfigurationError("Only yandex_speechkit STT provider is supported")
        if settings.model != _DEFAULT_MODEL:
            raise SttConfigurationError("SpeechKit STT model must be general")
        return cls(
            endpoint=settings.endpoint,
            api_key=settings.api_key,
            iam_token=settings.iam_token,
            folder_id=settings.folder_id,
            language=settings.language,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            client=client,
            sleeper=sleeper,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> YandexSpeechKitProvider:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def transcribe(
        self,
        audio: bytes,
        *,
        metadata: TranscriptionMetadata,
    ) -> TranscriptionResult:
        if not audio:
            raise SttPermanentError("STT audio is empty")
        if metadata.content_type.lower() not in {"audio/ogg", "audio/opus"}:
            raise SttPermanentError("SpeechKit expects Telegram Ogg/Opus voice audio")

        params = {
            "topic": _DEFAULT_MODEL,
            "lang": self._language,
            "format": "oggopus",
        }
        # SpeechKit docs require folderId for a user-account IAM token, but explicitly say
        # not to send it for service-account authentication (API keys are service-account keys).
        if self._folder_id and not self._api_key:
            params["folderId"] = self._folder_id
        authorization = f"Api-Key {self._api_key}" if self._api_key else f"Bearer {self._iam_token}"
        headers = {
            "Authorization": authorization,
            "Content-Type": "application/octet-stream",
        }
        started = time.perf_counter()
        response = self._post_with_retries(audio, params=params, headers=headers)
        text = _parse_yandex_result(response)
        duration_ms = (
            metadata.duration_seconds * 1000 if metadata.duration_seconds is not None else None
        )
        return TranscriptionResult(
            text=text,
            provider="yandex_speechkit",
            model=_DEFAULT_MODEL,
            audio_duration_ms=duration_ms,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _post_with_retries(
        self,
        audio: bytes,
        *,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: TranscriptionError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.post(
                    self._endpoint,
                    params=params,
                    headers=headers,
                    content=audio,
                )
            except httpx.TimeoutException as exc:
                last_error = SttTimeoutError("SpeechKit request timed out", retryable=True)
                if attempt >= self._max_retries:
                    raise last_error from exc
                self._sleep(_retry_delay(attempt))
                continue
            except httpx.RequestError as exc:
                last_error = SttTemporaryError("SpeechKit network failure", retryable=True)
                if attempt >= self._max_retries:
                    raise last_error from exc
                self._sleep(_retry_delay(attempt))
                continue

            if response.status_code in _TRANSIENT_STATUS_CODES or response.status_code >= 500:
                last_error = SttTemporaryError(
                    "SpeechKit temporary HTTP failure",
                    status_code=response.status_code,
                    retryable=True,
                )
                if attempt >= self._max_retries:
                    raise last_error
                self._sleep(_retry_delay(attempt))
                continue
            if response.status_code >= 400:
                raise SttPermanentError(
                    "SpeechKit rejected the request",
                    status_code=response.status_code,
                )
            return response

        assert last_error is not None
        raise last_error


def _retry_delay(attempt: int) -> float:
    return float(min(0.5 * (2**attempt), 2.0))


def _parse_yandex_result(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise SttPermanentError(
            "SpeechKit returned non-JSON response",
            status_code=response.status_code,
        ) from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, str):
        raise SttPermanentError(
            "SpeechKit response has no result field",
            status_code=response.status_code,
        )
    return result.strip()


def build_stt_provider_from_env(
    environ: Mapping[str, str] | None = None,
    *,
    vertical_id: str | None = None,
    metadata_token_provider: Callable[[], str | None] | None = None,
) -> YandexSpeechKitProvider | None:
    """Build Yandex SpeechKit from env; return ``None`` when disabled/unconfigured."""
    settings = SttEnvSettings.from_env(environ, vertical_id=vertical_id)
    if not settings.enabled:
        return None
    if not settings.configured and settings.use_metadata_iam:
        token_provider = metadata_token_provider or _metadata_iam_token
        iam_token = token_provider()
        if iam_token:
            settings = settings.model_copy(update={"iam_token": iam_token})
    if not settings.configured:
        return None
    return YandexSpeechKitProvider.from_settings(settings)


def _metadata_iam_token() -> str | None:
    """Resolve the attached YC VM service-account token with a short in-process cache."""
    global _metadata_token_cache
    now = time.monotonic()
    with _metadata_token_lock:
        if _metadata_token_cache is not None and _metadata_token_cache[0] > now:
            return _metadata_token_cache[1]
        try:
            with httpx.Client(timeout=httpx.Timeout(3.0)) as client:
                response = client.get(
                    _METADATA_TOKEN_URL,
                    headers={"Metadata-Flavor": "Google"},
                )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            return None
        expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        ttl = float(expires_in) if isinstance(expires_in, (int, float)) else 600.0
        _metadata_token_cache = (now + max(min(ttl - 60.0, 600.0), 60.0), token)
        return token


__all__ = [
    "SpeechToTextProvider",
    "SttConfigurationError",
    "SttEnvSettings",
    "SttPermanentError",
    "SttTemporaryError",
    "SttTimeoutError",
    "TranscriptionError",
    "TranscriptionMetadata",
    "TranscriptionResult",
    "YandexSpeechKitProvider",
    "build_stt_provider_from_env",
]
