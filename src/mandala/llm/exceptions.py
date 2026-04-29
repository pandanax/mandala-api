"""Ошибки провайдера LLM (доменный слой интеграций, без привязки к FastAPI)."""

from __future__ import annotations


class LlmProviderError(Exception):
    """Сбой вызова LLM: HTTP, формат ответа или явная ошибка API."""

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
