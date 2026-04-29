"""HTTP-клиент: OpenAI-compatible ``POST .../images/generations`` (тикет 15)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from mandala.llm.exceptions import LlmProviderError
from mandala.llm.image_generation import ImageGenerationResult

_IMAGE_TIMEOUT = httpx.Timeout(connect=20.0, read=180.0, write=20.0, pool=20.0)


class OpenAICompatibleImageClient:
    """Тело запроса совместимо с OpenAI Images API (url в ответе)."""

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
        self._client = client or httpx.Client(timeout=_IMAGE_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenAICompatibleImageClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> ImageGenerationResult:
        p = (prompt or "").strip() or "—"
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "prompt": p[:4000],
            "n": 1,
            "size": "1024x1024",
            "response_format": "url",
        }
        url = f"{self._base}/images/generations"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.RequestError as e:
            raise LlmProviderError(
                f"image API HTTP request failed: {e}",
                provider_detail=str(e),
            ) from e

        image_url = _parse_image_url(response)
        return ImageGenerationResult(
            prompt_echo=p[:500],
            image_url=image_url,
            stub_ref=None,
        )


def _parse_image_url(response: httpx.Response) -> str:
    text_preview = response.text[:512] if response.text else ""
    if response.status_code >= 400:
        detail = _extract_error_detail(response)
        raise LlmProviderError(
            f"image API error HTTP {response.status_code}",
            status_code=response.status_code,
            provider_detail=detail or text_preview or None,
        )
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise LlmProviderError(
            "image API returned non-JSON body",
            status_code=response.status_code,
            provider_detail=text_preview or None,
        ) from e
    if not isinstance(data, dict):
        raise LlmProviderError(
            "image API JSON root must be an object",
            status_code=response.status_code,
        )
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise LlmProviderError(
            "image API response has no data[]",
            status_code=response.status_code,
            provider_detail=json.dumps(data)[:512],
        )
    first = items[0]
    if not isinstance(first, dict):
        raise LlmProviderError("image API data[0] must be an object")
    u = first.get("url")
    if not isinstance(u, str) or not u.strip():
        raise LlmProviderError(
            "image API data[0].url missing (need response_format=url)",
            status_code=response.status_code,
        )
    return u.strip()


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
