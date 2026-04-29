"""Адаптер Telegram (тикет 9): long polling, Bot API, ``InboundEvent`` / ``OutboundMessage``."""

from mandala.adapters.telegram.bot_api import TelegramApiError, TelegramBotApiClient
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.adapters.telegram.polling import (
    process_telegram_update,
    resolve_telegram_vertical_id,
    run_polling_forever,
)
from mandala.adapters.telegram.secrets import mask_bot_token

__all__ = [
    "TelegramApiError",
    "TelegramBotApiClient",
    "deliver_outbound_messages",
    "mask_bot_token",
    "process_telegram_update",
    "resolve_telegram_vertical_id",
    "run_polling_forever",
    "telegram_update_to_inbound_event",
]
