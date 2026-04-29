"""Форматирование извлечённых чанков для системного промпта (тикет 16)."""

from __future__ import annotations

from collections.abc import Sequence


def build_kb_context_block(chunks: Sequence[str], *, max_chars: int) -> str:
    """Склеить чанки с нумерацией; обрезать по ``max_chars`` (символы, MVP).

    Лимит токенов модели не считается здесь — см. документацию ``RAG_MAX_CONTEXT_CHARS``.
    """
    parts: list[str] = []
    used = 0
    header = (
        "Ниже — выдержки из внутренней базы знаний этой вертикали (RAG). "
        "Опирайся на них, если релевантно; не выдумывай факты вне них.\n"
    )
    used += len(header)
    for i, ch in enumerate(chunks, start=1):
        block = f"[{i}] {ch}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    if not parts:
        return ""
    return header + "\n".join(parts).rstrip()
