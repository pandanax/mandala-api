"""Резолвинг токена бота по ``vertical_id`` (MVP: env)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_bot_token_for_vertical(vertical_id: str) -> str | None:
    """Один ``TELEGRAM_BOT_TOKEN`` при совпадении ``TELEGRAM_VERTICAL_ID`` с ``vertical_id``."""
    configured_vertical = os.environ.get("TELEGRAM_VERTICAL_ID")
    if configured_vertical and configured_vertical == vertical_id:
        return os.environ.get("TELEGRAM_BOT_TOKEN")

    logger.warning("No bot token mapping found for vertical_id=%s", vertical_id)
    return None
