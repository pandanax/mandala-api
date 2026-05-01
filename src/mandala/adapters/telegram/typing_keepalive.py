"""Индикатор «печатает» (``sendChatAction``) на время долгого ``handle_inbound``."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TypeVar

from mandala.adapters.telegram.bot_api import TelegramBotApiClient

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 4.0

T = TypeVar("T")


def run_with_typing_keepalive(
    api: TelegramBotApiClient,
    chat_id: int,
    fn: Callable[[], T],
) -> T:
    """Пока ``fn()`` выполняется, периодически шлёт ``typing`` (Bot API ~5 с на один вызов)."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                api.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                logger.debug("telegram typing keepalive failed chat_id=%s", chat_id, exc_info=True)
                return
            if stop.wait(_INTERVAL_SEC):
                break

    thread = threading.Thread(target=loop, name="tg-typing", daemon=True)
    thread.start()
    try:
        return fn()
    finally:
        stop.set()
        thread.join(timeout=1.0)
