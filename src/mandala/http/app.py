"""FastAPI приложение с health и webhook endpoints (тикет 10)."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from mandala import metrics
from mandala.adapters.telegram.billing_updates import process_telegram_billing_update
from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.bot_commands import register_bot_commands_if_configured
from mandala.adapters.telegram.bot_token import get_bot_token_for_vertical
from mandala.adapters.telegram.callback_ack import answer_callback_query_if_present
from mandala.adapters.telegram.daily_forecast_scheduler import (
    start_daily_forecast_scheduler,
    stop_daily_forecast_scheduler,
)
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.webhook_delivery import process_telegram_webhook_update_async
from mandala.http.engine_access import get_engine
from mandala.http.web_chat import router as web_chat_router
from mandala.llm.factory import log_effective_models
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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Старт/остановка приложения.

    На старте регистрируем команды бота через ``setMyCommands``, чтобы после
    каждого деплоя они автоматически подсвечивались в чате. Ошибку глотаем внутри
    :func:`register_bot_commands_if_configured` — старт не должен падать.

    Также логируем effective-модель каждой вертикали, чтобы рассинхрон
    ``LLM_MODEL`` (env) ↔ bundled ``vertical_overrides.json`` был виден сразу после деплоя.

    Здесь же включаем эмиссию метрик в YC Monitoring, если задан
    ``MANDALA_METRICS_ENABLED`` (см. :mod:`mandala.metrics`); при выключенных
    метриках это no-op и фоновый поток не создаётся.

    Также стартуем планировщик утренней рассылки (проактивный девиз-мотиватор,
    см. :mod:`mandala.adapters.telegram.daily_forecast_scheduler`) — фоновая asyncio-задача;
    выключается env-рубильником ``MANDALA_DAILY_FORECAST_ENABLED``. При выходе — аккуратная отмена.
    """
    log_effective_models(logger)
    await register_bot_commands_if_configured()
    metrics.init_from_env()
    daily_forecast_task = start_daily_forecast_scheduler(get_engine)
    try:
        yield
    finally:
        await stop_daily_forecast_scheduler(daily_forecast_task)
        metrics.shutdown()


def create_app() -> FastAPI:
    """Создать и настроить FastAPI приложение."""
    app = FastAPI(
        title="Mandala HTTP API",
        description="HTTP приложение для обработки webhook и health checks (тикет 10)",
        version="0.1.0",
        lifespan=_lifespan,
    )

    @app.middleware("http")
    async def _metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Метрики приложения: RPS, латентность и статусы по нормализованному роуту.

        Покрывает и здоровье (``/health``), и Telegram-webhook (по метке ``route``).
        Инструментация — no-op, если метрики выключены.
        """
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            metrics.record_http_request(
                route=metrics.normalize_route(request.url.path),
                method=request.method,
                status=status,
                elapsed_ms=(time.perf_counter() - start) * 1000.0,
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

            if _telegram_update_is_billing(update_data):
                engine = get_engine()
                bot_token = get_bot_token_for_vertical(vertical_id)
                if not bot_token:
                    logger.error("No bot token for vertical_id=%s (Stars / оплата)", vertical_id)
                    raise HTTPException(
                        status_code=500, detail="Bot token not configured for this vertical"
                    )
                token = bot_token

                # Синхронный биллинг-ход (БД + Telegram API) уводим в worker-поток,
                # чтобы не блокировать event-loop; сама идемпотентная логика не меняется.
                def _run_billing() -> bool:
                    with TelegramBotApiClient(token) as api:
                        return process_telegram_billing_update(
                            update_data,
                            vertical_id=vertical_id,
                            engine=engine,
                            api=api,
                        )

                if await to_thread.run_sync(_run_billing):
                    return {"status": "ok"}

            event = telegram_update_to_inbound_event(update_data, vertical_id=vertical_id)
            if event is None:
                cq = update_data.get("callback_query")
                if isinstance(cq, dict):
                    logger.error(
                        "funnel webhook %s",
                        op_format(
                            vertical_id=vertical_id,
                            stage="inbound_map_none_callback",
                            update_id=upd_id,
                            callback_query_id=cq.get("id"),
                            has_message=cq.get("message") is not None,
                            has_from=cq.get("from") is not None,
                            data_preview=str(cq.get("data", ""))[:48],
                        ),
                    )
                    bot_token = get_bot_token_for_vertical(vertical_id)
                    if bot_token:
                        with TelegramBotApiClient(bot_token) as api:
                            answer_callback_query_if_present(api, update_data)
                else:
                    logger.info(
                        "funnel webhook %s",
                        op_format(vertical_id=vertical_id, stage="ignored", update_id=upd_id),
                    )
                return {"status": "ignored"}

            await process_telegram_webhook_update_async(update_data, vertical_id=vertical_id)
            return {"status": "ok"}

        except Exception as e:
            logger.error("Webhook processing failed for vertical_id=%s: %s", vertical_id, e)
            raise HTTPException(status_code=500, detail="Webhook processing failed") from e

    return app
