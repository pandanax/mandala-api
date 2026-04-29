"""FastAPI приложение с health и webhook endpoints (тикет 10)."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.db.engine import create_engine_from_env
from mandala.domain.handler import handle_inbound

logger = logging.getLogger(__name__)

# Глобальный engine - создается при старте приложения
_engine: Engine | None = None


def get_engine() -> Engine:
    """Получить SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_engine_from_env()
    return _engine


def create_app() -> FastAPI:
    """Создать и настроить FastAPI приложение."""
    app = FastAPI(
        title="Mandala HTTP API",
        description="HTTP приложение для обработки webhook и health checks (тикет 10)",
        version="0.1.0",
    )

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
            logger.info("Received webhook update for vertical_id=%s", vertical_id)

            # Конвертируем Update -> InboundEvent
            event = telegram_update_to_inbound_event(update_data, vertical_id=vertical_id)
            if event is None:
                logger.info("Update ignored for vertical_id=%s", vertical_id)
                return {"status": "ignored"}

            # Обрабатываем событие через домен
            engine = get_engine()
            with engine.begin() as conn:
                outbound_messages = handle_inbound(event, conn)

            # Если есть ответы - отправляем их через Telegram Bot API
            if outbound_messages and event.raw_ref:
                chat_id = event.raw_ref.get("chat_id")
                if chat_id is not None:
                    # Получаем токен бота для отправки ответов
                    bot_token = _get_bot_token_for_vertical(vertical_id)
                    if bot_token:
                        api = TelegramBotApiClient(bot_token)
                        deliver_outbound_messages(
                            api, chat_id=int(chat_id), messages=outbound_messages
                        )
                        logger.info(
                            "Delivered %d messages for vertical_id=%s",
                            len(outbound_messages),
                            vertical_id,
                        )
                    else:
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
