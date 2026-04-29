"""Маскирование секретов для логов (тикет 9).

Для API-ключей провайдеров (LLM и т.д.) — :func:`mandala.observability.mask_api_key`.
"""

from __future__ import annotations


def mask_bot_token(token: str) -> str:
    """Сократить токен бота до префикса + ``…`` (не логировать полный секрет)."""
    token = token.strip()
    if not token:
        return "(empty)"
    if ":" in token:
        prefix, _rest = token.split(":", 1)
        if len(prefix) <= 12:
            return f"{prefix}:…"
        return f"{prefix[:12]}:…"
    if len(token) <= 8:
        return "…"
    return f"{token[:4]}…{token[-2:]}"
