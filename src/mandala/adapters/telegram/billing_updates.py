"""События оплаты Bot API: pre_checkout, successful_payment (тикет 19)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.engine import Engine

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.observability import op_format
from mandala.services.billing import PostgresBillingProvider
from mandala.services.telegram_stars import handle_pre_checkout_query, handle_successful_payment

logger = logging.getLogger(__name__)


def process_telegram_billing_update(
    update: dict[str, Any],
    *,
    vertical_id: str,
    engine: Engine,
    api: TelegramBotApiClient,
) -> bool:
    """Если update — оплата Stars, обработать в БД и вернуть True (без handle_inbound)."""
    if "pre_checkout_query" in update and isinstance(update["pre_checkout_query"], dict):
        pcq = update["pre_checkout_query"]
        try:
            with engine.begin() as conn:
                ok, err = handle_pre_checkout_query(
                    conn,
                    vertical_id=vertical_id,
                    query=pcq,
                )
        except Exception:
            logger.exception("telegram: pre_checkout failed vertical_id=%s", vertical_id)
            try:
                api.answer_pre_checkout_query(
                    pre_checkout_query_id=str(pcq.get("id", "")),
                    ok=False,
                    error_message="Внутренняя ошибка. Позже.",
                )
            except Exception:  # noqa: BLE001
                logger.exception("answerPreCheckoutQuery (error path)")
            return True
        emsg = (err or "Отклонено")[:200]
        api.answer_pre_checkout_query(
            pre_checkout_query_id=str(pcq["id"]),
            ok=ok,
            error_message=emsg if not ok else None,
        )
        logger.info(
            "funnel billing %s",
            op_format(
                vertical_id=vertical_id,
                stage="telegram_pre_checkout",
                outcome="allow" if ok else "deny",
            ),
        )
        return True

    if "message" in update and isinstance(update["message"], dict):
        msg = update["message"]
        if isinstance(msg.get("successful_payment"), dict):
            with engine.begin() as conn:
                billing = PostgresBillingProvider(conn)
                handle_successful_payment(
                    conn,
                    vertical_id=vertical_id,
                    message=msg,
                    billing=billing,
                )
            logger.info(
                "funnel billing %s",
                op_format(
                    vertical_id=vertical_id,
                    stage="telegram_successful_payment",
                    outcome="processed",
                ),
            )
            chat = msg.get("chat")
            if isinstance(chat, dict) and "id" in chat:
                try:
                    api.send_message(
                        chat_id=int(chat["id"]),
                        text="План обновлён, спасибо за оплату.",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("telegram: send_message после successful_payment")
            return True

    return False
