"""Доставка ``OutboundMessage`` в Telegram (тикет 9)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.domain import OutboundMessage
from mandala.observability import op_format

logger = logging.getLogger(__name__)


def _buttons_to_reply_markup(buttons: list[list[dict[str, str]]]) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for row in buttons:
        line: list[dict[str, str]] = []
        for cell in row:
            text = cell.get("text", "")
            cb = cell.get("callback_data", text)
            line.append({"text": text, "callback_data": cb})
        rows.append(line)
    return {"inline_keyboard": rows}


def deliver_outbound_messages(
    api: TelegramBotApiClient,
    *,
    chat_id: int,
    messages: list[OutboundMessage],
    vertical_id: str | None = None,
    user_id: UUID | None = None,
) -> None:
    """Отправить ответы пользователю (``sendMessage`` / ``sendPhoto``).

    ``vertical_id`` / ``user_id`` — только для операционных логов (тикет 20), без PII текста.
    """
    if vertical_id is not None and messages:
        n_photo = sum(1 for m in messages if m.photo)
        logger.info(
            "funnel outbound %s",
            op_format(
                vertical_id=vertical_id,
                user_id=user_id,
                stage="telegram_deliver",
                n_messages=len(messages),
                n_photo=n_photo,
            ),
        )
    for msg in messages:
        markup: dict[str, Any] | None = None
        if msg.buttons:
            markup = _buttons_to_reply_markup(msg.buttons)

        if msg.photo:
            api.send_photo(
                chat_id=chat_id,
                photo=msg.photo,
                caption=msg.text,
                reply_markup=markup,
            )
        elif msg.text is not None:
            api.send_message(chat_id=chat_id, text=msg.text, reply_markup=markup)
        # TODO(тикет 12+): ``requires_payment``, ``defer`` — сценарии оплаты и отложенных ответов.
