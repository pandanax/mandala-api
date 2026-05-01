"""Синхронная обработка webhook: БД + доставка + ``answerCallbackQuery``."""

from __future__ import annotations

import logging
from typing import Any

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.bot_token import get_bot_token_for_vertical
from mandala.adapters.telegram.callback_ack import answer_callback_query_if_present
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.adapters.telegram.typing_keepalive import run_with_typing_keepalive
from mandala.domain.handler import handle_inbound
from mandala.http.engine_access import get_engine
from mandala.observability import op_format

logger = logging.getLogger(__name__)


def process_telegram_webhook_update(
    update_data: dict[str, Any],
    *,
    vertical_id: str,
) -> None:
    """``handle_inbound`` → ``deliver`` → ``answerCallbackQuery`` (один запрос HTTP)."""
    raw_uid = update_data.get("update_id")
    upd_id = raw_uid if isinstance(raw_uid, int) else None
    try:
        event = telegram_update_to_inbound_event(update_data, vertical_id=vertical_id)
        if event is None:
            return
        raw_ref = event.raw_ref or {}
        chat_id_early = raw_ref.get("chat_id")
        bot_token = get_bot_token_for_vertical(vertical_id)
        engine = get_engine()
        with engine.begin() as conn:
            if bot_token and chat_id_early is not None:
                with TelegramBotApiClient(bot_token) as typing_api:
                    outbound_messages = run_with_typing_keepalive(
                        typing_api,
                        int(chat_id_early),
                        lambda: handle_inbound(event, conn),
                    )
            else:
                outbound_messages = handle_inbound(event, conn)

        if not bot_token:
            if outbound_messages:
                logger.error(
                    "funnel webhook %s",
                    op_format(
                        vertical_id=vertical_id,
                        stage="no_bot_token",
                        update_id=upd_id,
                        n_messages=len(outbound_messages),
                    ),
                )
            return

        chat_id = raw_ref.get("chat_id")

        with TelegramBotApiClient(bot_token) as api:
            if outbound_messages and chat_id is not None:
                deliver_outbound_messages(
                    api,
                    chat_id=int(chat_id),
                    messages=outbound_messages,
                    vertical_id=vertical_id,
                )
                logger.info(
                    "funnel webhook %s",
                    op_format(
                        vertical_id=vertical_id,
                        stage="delivered",
                        update_id=upd_id,
                        n_messages=len(outbound_messages),
                    ),
                )
            elif outbound_messages and chat_id is None:
                logger.error(
                    "funnel webhook %s",
                    op_format(
                        vertical_id=vertical_id,
                        stage="deliver_skip",
                        update_id=upd_id,
                        reason="no_chat_id",
                        n_messages=len(outbound_messages),
                    ),
                )
            else:
                logger.warning(
                    "funnel webhook %s",
                    op_format(
                        vertical_id=vertical_id,
                        stage="empty_outbound",
                        update_id=upd_id,
                        has_callback=isinstance(update_data.get("callback_query"), dict),
                    ),
                )

            answer_callback_query_if_present(api, update_data)
    except Exception:
        logger.exception(
            "funnel webhook %s",
            op_format(vertical_id=vertical_id, stage="webhook_processing_error", update_id=upd_id),
        )
