"""Детерминированный чанкинг текста для индексации KB (тикет 16)."""

from __future__ import annotations


def chunk_text(text: str, *, chunk_chars: int, overlap: int) -> list[str]:
    """Разбить нормализованный текст на чанки фиксированной длины с перекрытием.

    Границы — по символам (MVP); для продакшена возможен чанкинг по параграфам (тикет 17+).
    """
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    if chunk_chars <= 0:
        return [raw]
    step = max(1, chunk_chars - max(0, overlap))
    out: list[str] = []
    i = 0
    while i < len(raw):
        piece = raw[i : i + chunk_chars].strip()
        if piece:
            out.append(piece)
        i += step
    return out if out else [raw[:chunk_chars]]
