"""Абстракция текстового клиента LLM (тикет 11)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from mandala.llm.types import ChatMessage


class TextCompletionClient(Protocol):
    """Синхронный клиент текстовых completions; бэкенд подменяется без смены агента."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Вернуть текст ответа ассистента."""
        ...
