"""Эмбеддинги через OpenAI-compatible ``POST …/embeddings`` (тикет 16)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from mandala.llm.exceptions import LlmProviderError

_DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=15.0, pool=15.0)


class OpenAICompatibleEmbeddingClient:
    """Батч-эмбеддинги; тот же ``base_url`` / ключ, что и у чат-модели."""

    __slots__ = ("_api_key", "_base", "_client", "_default_model")

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key.strip()
        self._default_model = model.strip()
        self._client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def embed_texts(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        """Вернуть векторы в том же порядке, что и ``texts``."""
        if not texts:
            return []
        use_model = (model or self._default_model).strip()
        payload: dict[str, Any] = {"model": use_model, "input": list(texts)}
        url = f"{self._base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._client.post(url, headers=headers, json=payload)
        except httpx.RequestError as e:
            raise LlmProviderError(
                f"embeddings HTTP request failed: {e}",
                provider_detail=str(e),
            ) from e
        return _parse_embeddings_response(response)


def _parse_embeddings_response(response: httpx.Response) -> list[list[float]]:
    preview = response.text[:512] if response.text else ""
    if response.status_code >= 400:
        detail = _extract_error_detail(response)
        raise LlmProviderError(
            f"embeddings API error HTTP {response.status_code}",
            status_code=response.status_code,
            provider_detail=detail or preview or None,
        )
    try:
        data = response.json()
    except json.JSONDecodeError as e:
        raise LlmProviderError(
            "embeddings API returned non-JSON body",
            status_code=response.status_code,
            provider_detail=preview or None,
        ) from e
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise LlmProviderError(
            "embeddings API response has no data[]",
            status_code=response.status_code,
            provider_detail=preview or None,
        )
    vectors: list[list[float]] = []
    indexed: list[tuple[int, list[float]]] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            continue
        emb = row.get("embedding")
        idx_raw = row.get("index")
        order = int(idx_raw) if isinstance(idx_raw, int) else i
        if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
            indexed.append((order, [float(x) for x in emb]))
    indexed.sort(key=lambda t: t[0])
    vectors = [v for _, v in indexed]
    if not vectors:
        raise LlmProviderError(
            "embeddings API: malformed embedding vectors",
            status_code=response.status_code,
        )
    return vectors


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
