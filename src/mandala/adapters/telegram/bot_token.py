"""Резолвинг токена бота по ``vertical_id`` (мультитенантно: env).

Один процесс может обслуживать несколько ботов (разные вертикали). Источник маппинга —
окружение, приоритет (высший → низший):

1. ``TELEGRAM_BOT_TOKEN_<VERTICAL>`` — по одной env-переменной на вертикаль
   (``<VERTICAL>`` = slug в верхнем регистре, напр. ``TELEGRAM_BOT_TOKEN_ASTROLOGY``).
2. ``TELEGRAM_BOT_TOKENS`` — JSON-объект ``{"<vertical>": "<token>", ...}``.
3. ``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_VERTICAL_ID`` — обратная совместимость (одна вертикаль).

Неизвестная вертикаль → ``None`` (вызывающий логирует ``no_bot_token`` и не падает).
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
_ENV_VERTICAL = "TELEGRAM_VERTICAL_ID"
_ENV_TOKENS_JSON = "TELEGRAM_BOT_TOKENS"
_ENV_TOKEN_PREFIX = "TELEGRAM_BOT_TOKEN_"


def load_bot_token_map() -> dict[str, str]:
    """Собрать маппинг ``vertical_id → token`` из окружения по приоритету.

    Более высокий приоритет перезаписывает более низкий, поэтому источники применяются
    в порядке низший → высший. Пустые/битые значения тихо пропускаются.
    """
    mapping: dict[str, str] = {}

    # 3) Обратная совместимость: одиночный токен + вертикаль (низший приоритет).
    legacy_vertical = os.environ.get(_ENV_VERTICAL, "").strip()
    legacy_token = os.environ.get(_ENV_TOKEN, "").strip()
    if legacy_vertical and legacy_token:
        mapping[legacy_vertical] = legacy_token

    # 2) JSON-объект vertical → token.
    raw_json = os.environ.get(_ENV_TOKENS_JSON, "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except (ValueError, TypeError):
            logger.warning("%s: некорректный JSON, пропускаю", _ENV_TOKENS_JSON)
        else:
            if isinstance(parsed, dict):
                for vid, token in parsed.items():
                    if isinstance(token, str) and token.strip():
                        mapping[str(vid).strip()] = token.strip()
            else:
                logger.warning("%s: ожидался JSON-объект, пропускаю", _ENV_TOKENS_JSON)

    # 1) Per-vertical env TELEGRAM_BOT_TOKEN_<VERTICAL> (высший приоритет).
    #    Отсекаем сам TELEGRAM_BOT_TOKEN (нет суффикса после префикса).
    for key, value in os.environ.items():
        if not key.startswith(_ENV_TOKEN_PREFIX):
            continue
        suffix = key[len(_ENV_TOKEN_PREFIX) :]
        if not suffix or not value.strip():
            continue
        mapping[suffix.lower()] = value.strip()

    return mapping


def get_bot_token_for_vertical(vertical_id: str) -> str | None:
    """Токен бота для вертикали или ``None``, если маппинга нет (без исключения)."""
    token = load_bot_token_map().get(vertical_id)
    if token:
        return token

    logger.warning("No bot token mapping found for vertical_id=%s", vertical_id)
    return None
