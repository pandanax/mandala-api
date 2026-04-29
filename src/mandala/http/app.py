"""FastAPI приложение с health и webhook endpoints (тикет 10)."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from mandala.adapters.telegram.billing_updates import process_telegram_billing_update
from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.domain.handler import handle_inbound
from mandala.http.engine_access import get_engine
from mandala.http.web_chat import router as web_chat_router
from mandala.observability import op_format

logger = logging.getLogger(__name__)


def _telegram_update_is_billing(update: dict[str, Any]) -> bool:
    """``pre_checkout_query`` или ``message.successful_payment`` (тикет 19)."""
    if "pre_checkout_query" in update:
        return True
    msg = update.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("successful_payment"), dict):
        return True
    return False


def create_app() -> FastAPI:
    """Создать и настроить FastAPI приложение."""
    app = FastAPI(
        title="Mandala HTTP API",
        description="HTTP приложение для обработки webhook и health checks (тикет 10)",
        version="0.1.0",
    )
    app.include_router(web_chat_router)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Проверка доступности приложения и PostgreSQL."""
        try:
            engine = get_engine()
            with engine.begin() as conn:
                # Простой запрос для проверки доступности БД
                result = conn.execute(text("SELECT 1 as test")).fetchone()
                if result is None or result[0] != 1:
                    raise HTTPException(status_code=503, detail="Database check failed")
        except SQLAlchemyError as e:
            logger.error("Database health check failed: %s", e)
            raise HTTPException(status_code=503, detail="Database unavailable") from e
        except Exception as e:
            logger.error("Health check failed: %s", e)
            raise HTTPException(status_code=503, detail="Service unavailable") from e

        return {"status": "ok", "database": "ok"}

    @app.post("/webhooks/telegram/{vertical_id}")
    async def telegram_webhook(vertical_id: str, request: Request) -> dict[str, str]:
        """Webhook endpoint для обработки обновлений от Telegram."""
        # Проверка секретного токена Telegram (X-Telegram-Bot-Api-Secret-Token)
        secret_token = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
        if secret_token:
            received_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if not received_token or received_token != secret_token:
                logger.warning("Invalid webhook secret token for vertical_id=%s", vertical_id)
                raise HTTPException(status_code=403, detail="Invalid secret token")

        try:
            # Получаем JSON body от Telegram
            update_data: dict[str, Any] = await request.json()
            raw_uid = update_data.get("update_id")
            upd_id = raw_uid if isinstance(raw_uid, int) else None
            logger.info(
                "funnel webhook %s",
                op_format(vertical_id=vertical_id, stage="received", update_id=upd_id),
            )

            engine = get_engine()
            if _telegram_update_is_billing(update_data):
                bot_token = _get_bot_token_for_vertical(vertical_id)
                if not bot_token:
                    logger.error("No bot token for vertical_id=%s (Stars / оплата)", vertical_id)
                    raise HTTPException(
                        status_code=500, detail="Bot token not configured for this vertical"
                    )
                with TelegramBotApiClient(bot_token) as api:
                    if process_telegram_billing_update(
                        update_data,
                        vertical_id=vertical_id,
                        engine=engine,
                        api=api,
                    ):
                        return {"status": "ok"}
            # Обычные апдейты: обрабатываем даже без токена (ответ в Telegram невозможен).
            event = telegram_update_to_inbound_event(update_data, vertical_id=vertical_id)
            if event is None:
                logger.info(
                    "funnel webhook %s",
                    op_format(vertical_id=vertical_id, stage="ignored", update_id=upd_id),
                )
                return {"status": "ignored"}

            with engine.begin() as conn:
                outbound_messages = handle_inbound(event, conn)

            bot_token = _get_bot_token_for_vertical(vertical_id)
            if (
                bot_token
                and outbound_messages
                and event.raw_ref
                and event.raw_ref.get("chat_id") is not None
            ):
                with TelegramBotApiClient(bot_token) as api:
                    deliver_outbound_messages(
                        api,
                        chat_id=int(event.raw_ref["chat_id"]),
                        messages=outbound_messages,
                        vertical_id=vertical_id,
                    )
                logger.info(
                    "funnel webhook %s",
                    op_format(
                        vertical_id=vertical_id,
                        stage="delivered",
                        n_messages=len(outbound_messages),
                    ),
                )
            elif outbound_messages and not bot_token:
                logger.error("No bot token found for vertical_id=%s", vertical_id)

            return {"status": "ok"}

        except Exception as e:
            logger.error("Webhook processing failed for vertical_id=%s: %s", vertical_id, e)
            raise HTTPException(status_code=500, detail="Webhook processing failed") from e

    return app


def _get_bot_token_for_vertical(vertical_id: str) -> str | None:
    """Получить токен бота для конкретной вертикали.

    TODO: несколько токенов → вертикалей (таблица/конфиг) — вне scope тикета 10;
    сейчас один ``TELEGRAM_BOT_TOKEN`` + совпадение ``vertical_id`` с ``TELEGRAM_VERTICAL_ID``.
    """
    # Пока проверяем, что запрошенная вертикаль соответствует настроенной
    configured_vertical = os.environ.get("TELEGRAM_VERTICAL_ID")
    if configured_vertical and configured_vertical == vertical_id:
        return os.environ.get("TELEGRAM_BOT_TOKEN")

    # TODO: запрос к БД/конфигу соответствия токен ↔ vertical_id (см. план, тикет 9+).
    logger.warning("No bot token mapping found for vertical_id=%s", vertical_id)
    return None
