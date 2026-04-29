"""Контракт поиска по KB для текстового ответа (тикет 16)."""

from __future__ import annotations

from typing import Protocol


class KbSearchPort(Protocol):
    """Поиск top-k чанков **только** в границах ``vertical_id``."""

    def search(self, *, vertical_id: str, query: str, limit: int) -> list[str]:
        """Вернуть тексты чанков (уже отфильтрованы по вертикали)."""
        ...
