"""Доставка ``OutboundMessage`` в Telegram (тикет 9)."""

from __future__ import annotations

from typing import Any

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.domain import OutboundMessage


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
) -> None:
    """Отправить ответы пользователю (``sendMessage`` / ``sendPhoto``)."""
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
