"""Реализация TextCompletionClient для OpenAI-compatible Chat Completions HTTP API."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from mandala.llm.exceptions import LlmProviderError
from mandala.llm.types import ChatMessage

_DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)


class OpenAICompatibleTextClient:
    """POST ``{base_url}/chat/completions`` с заголовком ``Authorization: Bearer …``."""

    __slots__ = ("_api_key", "_base", "_client", "_default_model")

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._default_model = default_model.strip()
        self._client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleTextClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if not messages:
            msg = "messages must not be empty"
            raise LlmProviderError(msg)

        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [m.model_dump() for m in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        url = f"{self._base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.RequestError as e:
            raise LlmProviderError(
                f"LLM HTTP request failed: {e}",
                provider_detail=str(e),
            ) from e

        return _parse_response(response)


def _parse_response(response: httpx.Response) -> str:
    text_preview = response.text[:512] if response.text else ""
    if response.status_code >= 400:
        detail = _extract_error_detail(response)
        raise LlmProviderError(
            f"LLM API error HTTP {response.status_code}",
            status_code=response.status_code,
            provider_detail=detail or text_preview or None,
        )

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise LlmProviderError(
            "LLM API returned non-JSON body",
            status_code=response.status_code,
            provider_detail=text_preview or None,
        ) from e

    if not isinstance(data, dict):
        raise LlmProviderError(
            "LLM API JSON root must be an object",
            status_code=response.status_code,
            provider_detail=text_preview or None,
        )

    return _extract_message_content(data, status_code=response.status_code)


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


def _extract_message_content(data: dict[str, Any], *, status_code: int) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmProviderError(
            "LLM API response has no choices",
            status_code=status_code,
            provider_detail=json.dumps(data)[:512],
        )

    first = choices[0]
    if not isinstance(first, dict):
        raise LlmProviderError(
            "LLM API choice is not an object",
            status_code=status_code,
        )

    msg = first.get("message")
    if not isinstance(msg, dict):
        # Некоторые прокси отдают legacy поле text
        alt = first.get("text")
        if isinstance(alt, str):
            return alt
        raise LlmProviderError(
            "LLM API choice has no message",
            status_code=status_code,
        )

    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(str(p["text"]))
        if parts:
            return "".join(parts)

    raise LlmProviderError(
        "LLM API message content missing or unsupported",
        status_code=status_code,
    )
