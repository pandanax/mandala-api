"""Операционные логи (тикет 20): единый стиль полей, без сырого PII на INFO.

Поля в сообщениях — через :func:`op_format`: как минимум ``vertical_id``; при
наличии — ``user_id`` (UUID), ``channel``, ``stage``, ``intent``, ``resource``,
``outcome``, ``reason``, ``update_id``, счётчики доставки и т.д.

На **INFO** не попадают: текст переписки, промпты, полные токены бота и
API-ключи. Для токена бота — :func:`mandala.adapters.telegram.secrets.mask_bot_token`;
для ключей провайдеров — :func:`mask_api_key`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

_STANDARD_ORDER: tuple[str, ...] = (
    "vertical_id",
    "user_id",
    "channel",
    "stage",
    "intent",
    "resource",
    "outcome",
    "reason",
    "update_id",
    "n_messages",
    "n_photo",
    "billing",
    "reply_chars",
    "has_image_url",
    "plan_id",
    "provider",
)


def mask_api_key(api_key: str) -> str:
    """Сократить API-ключ до префикса/суффикса (для логов при необходимости)."""
    key = api_key.strip()
    if not key:
        return "(empty)"
    if len(key) <= 8:
        return "…"
    return f"{key[:4]}…{key[-2:]}"


def op_format(*, vertical_id: str, **fields: Any) -> str:
    """Собрать строку ``k=v`` в согласованном порядке (без None)."""
    parts: list[str] = [f"vertical_id={vertical_id}"]
    for key in _STANDARD_ORDER:
        if key == "vertical_id":
            continue
        if key not in fields:
            continue
        val = fields[key]
        if val is None:
            continue
        if isinstance(val, UUID):
            val = str(val)
        parts.append(f"{key}={val}")
    for key, val in sorted(fields.items()):
        if key in _STANDARD_ORDER:
            continue
        if val is None:
            continue
        if isinstance(val, UUID):
            val = str(val)
        parts.append(f"{key}={val}")
    return " ".join(parts)
