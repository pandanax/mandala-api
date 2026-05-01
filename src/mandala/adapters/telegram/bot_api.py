"""HTTP-клиент Bot API с ретраями (тикет 9).

Webhook и единая HTTP-точка — ``тикет 10``; здесь только вызовы ``api.telegram.org``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

import httpx

from mandala.adapters.telegram.secrets import mask_bot_token

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.telegram.org"
_MAX_RETRIES = 5


class TelegramApiError(RuntimeError):
    """Ответ Telegram с ``ok: false``."""

    def __init__(self, description: str) -> None:
        super().__init__(description)
        self.description = description


class TelegramBotApiClient:
    """Минимальный клиент: ``getUpdates``, ``sendMessage``, ``sendPhoto``."""

    __slots__ = ("_token", "_base", "_client")

    def __init__(
        self,
        token: str,
        *,
        base_url: str = _DEFAULT_BASE,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token.strip()
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=70.0, write=10.0, pool=10.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramBotApiClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _url(self, method: str) -> str:
        return f"{self._base}/bot{self._token}/{method}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        """POST JSON; ретраи на 429 и 5xx; в логах — только ``mask_bot_token``."""
        body = payload or {}
        masked = mask_bot_token(self._token)
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                r = self._client.post(self._url(method), json=body)
            except httpx.RequestError as e:
                last_err = e
                wait = min(2.0**attempt, 30.0)
                logger.warning(
                    "telegram HTTP error method=%s attempt=%s token=%s wait=%.1fs err=%s",
                    method,
                    attempt + 1,
                    masked,
                    wait,
                    e,
                )
                time.sleep(wait)
                continue

            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after", "2"))
                wait = min(max(retry_after, 1.0), 60.0)
                logger.warning(
                    "telegram 429 method=%s attempt=%s token=%s retry_after=%.1fs",
                    method,
                    attempt + 1,
                    masked,
                    wait,
                )
                time.sleep(wait)
                continue

            if r.status_code >= 500:
                wait = min(2.0**attempt, 30.0)
                logger.warning(
                    "telegram 5xx method=%s status=%s attempt=%s token=%s wait=%.1fs",
                    method,
                    r.status_code,
                    attempt + 1,
                    masked,
                    wait,
                )
                time.sleep(wait)
                continue

            data = r.json()
            if not isinstance(data, dict):
                msg = "telegram: ответ не JSON-объект"
                raise TelegramApiError(msg)

            if not data.get("ok"):
                desc = str(data.get("description", data))
                raise TelegramApiError(desc)

            return data.get("result")

        if last_err is not None:
            raise last_err
        msg = "telegram: исчерпаны ретраи"
        raise TelegramApiError(msg)

    def get_updates(self, *, offset: int | None = None, timeout: int = 30) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        raw = self.call("getUpdates", params)
        if raw is None:
            return []
        return cast(list[dict[str, Any]], raw)

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        p: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            p["reply_markup"] = reply_markup
        out = self.call("sendMessage", p)
        assert isinstance(out, dict)
        return out

    def send_photo(
        self,
        *,
        chat_id: int,
        photo: str,
        caption: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        p: dict[str, Any] = {"chat_id": chat_id, "photo": photo}
        if caption is not None:
            p["caption"] = caption
        if reply_markup is not None:
            p["reply_markup"] = reply_markup
        out = self.call("sendPhoto", p)
        assert isinstance(out, dict)
        return out

    def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool | None = None,
    ) -> bool:
        """``answerCallbackQuery`` — снять «часики» после нажатия inline-кнопки."""
        p: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            p["text"] = text
        if show_alert is not None:
            p["show_alert"] = show_alert
        raw = self.call("answerCallbackQuery", p)
        return bool(raw)

    def answer_pre_checkout_query(
        self,
        *,
        pre_checkout_query_id: str,
        ok: bool,
        error_message: str | None = None,
    ) -> bool:
        """``answerPreCheckoutQuery`` — подтверждение or отказ (Stars)."""
        p: dict[str, Any] = {
            "pre_checkout_query_id": pre_checkout_query_id,
            "ok": ok,
        }
        if not ok and error_message:
            p["error_message"] = error_message
        raw = self.call("answerPreCheckoutQuery", p)
        return bool(raw)
