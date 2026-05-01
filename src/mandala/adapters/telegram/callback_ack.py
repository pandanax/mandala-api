"""Подтверждение inline-callback в Telegram (answerCallbackQuery)."""

from __future__ import annotations

import logging
from typing import Any

from mandala.adapters.telegram.bot_api import TelegramApiError, TelegramBotApiClient

logger = logging.getLogger(__name__)


def answer_callback_query_if_present(
    api: TelegramBotApiClient,
    update: dict[str, Any],
) -> None:
    """Если апдейт с ``callback_query`` — ответить API (ошибки не рвут основной поток)."""
    cq = update.get("callback_query")
    if not isinstance(cq, dict):
        return
    raw_id = cq.get("id")
    if raw_id is None:
        return
    try:
        api.answer_callback_query(callback_query_id=str(raw_id))
    except TelegramApiError as e:
        logger.debug("answerCallbackQuery skipped: %s", e.description)
