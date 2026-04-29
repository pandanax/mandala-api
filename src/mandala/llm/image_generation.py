"""Абстракция клиента генерации изображений (тикеты 14–15)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ImageGenerationResult:
    """Результат генерации: URL от провайдера и/или метаданные заглушки."""

    prompt_echo: str
    """Нормализованный промпт (короткая цитата для подписи)."""

    image_url: str | None = None
    """Публичный URL изображения (``sendPhoto`` в Telegram), если провайдер отдал ссылку."""

    stub_ref: str | None = None
    """Для заглушки — внутренний ref; при реальном API — ``None``."""


class ImageGenerationClient(Protocol):
    """Синхронный клиент генерации изображений; бэкенд подменяется без смены роутера."""

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> ImageGenerationResult:
        """Сгенерировать изображение по текстовому описанию."""
        ...


class StubImageGenerationClient:
    """Заглушка без сетевых вызовов (тикет 14)."""

    __slots__ = ()

    def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> ImageGenerationResult:
        _ = model
        p = (prompt or "").strip() or "—"
        return ImageGenerationResult(
            prompt_echo=p[:500],
            image_url=None,
            stub_ref="stub14",
        )
