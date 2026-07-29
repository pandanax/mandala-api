"""Планировщик утренней рассылки: фоновая asyncio-задача в HTTP-lifespan.

Раз в ~60 с считаем текущее МСК-время и для каждой вертикали с валидным bot-token
обходим получателей (см. :class:`DailyForecastRepository`). Для каждого — чистая проверка
``should_send_daily_forecast`` (``now`` инжектируется), при «пора» генерируем девиз (LLM,
БЕЗ списания квоты) и доставляем на ``external_user_id`` (=chat_id в личке). Идемпотентность —
через ``daily_forecast_last_sent`` (МСК-дата), переживает рестарт.

Тяжёлая работа (LLM/БД — синхронный стек) уводится с event-loop в worker-поток
(``anyio.to_thread.run_sync``), как в webhook_delivery. Пер-юзерный ``try/except``: падение
одного получателя не рушит батч; при неудаче ``last_sent`` НЕ ставится (повторит в окне).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from anyio import to_thread
from sqlalchemy.engine import Engine

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.bot_token import load_bot_token_map
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.llm import TextCompletionClient
from mandala.llm.factory import create_text_client_for_vertical
from mandala.repositories.daily_forecast import DailyForecastRecipient, DailyForecastRepository
from mandala.repositories.profiles import ProfileRepository
from mandala.services.daily_forecast import (
    build_daily_forecast_message,
    build_daily_slogan,
    now_msk,
    should_send_daily_forecast,
    today_str_msk,
)
from mandala.verticals.client_knowledge import AGENT_CARD_DAILY_FORECAST_LAST_SENT

logger = logging.getLogger(__name__)

_SLEEP_SECONDS = 60


def daily_forecast_globally_enabled() -> bool:
    """Глобальный env-рубильник ``MANDALA_DAILY_FORECAST_ENABLED`` (дефолт — включено)."""
    raw = os.getenv("MANDALA_DAILY_FORECAST_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _close_client_if_any(client: object) -> None:
    closer = getattr(client, "close", None)
    if callable(closer):
        with contextlib.suppress(Exception):
            closer()


def run_daily_forecast_tick(
    *,
    now: datetime,
    engine: Engine,
    token_map: dict[str, str] | None = None,
    make_api: Callable[[str], TelegramBotApiClient] | None = None,
    make_llm: Callable[[str], TextCompletionClient] | None = None,
) -> int:
    """Один синхронный тик рассылки по всем вертикалям. Возвращает число отправленных.

    Инъектируемые фабрики (``token_map`` / ``make_api`` / ``make_llm``) — для тестов; по
    умолчанию берутся из окружения. Каждая вертикаль изолирована ``try/except``.
    """
    tokens = token_map if token_map is not None else load_bot_token_map()
    api_factory = make_api or (lambda token: TelegramBotApiClient(token))
    llm_factory = make_llm or create_text_client_for_vertical

    sent_total = 0
    for vertical_id, token in tokens.items():
        if not token:
            continue
        try:
            sent_total += _process_vertical(
                vertical_id=vertical_id,
                token=token,
                now=now,
                engine=engine,
                api_factory=api_factory,
                llm_factory=llm_factory,
            )
        except Exception:
            logger.exception("daily forecast: vertical batch failed vertical_id=%s", vertical_id)
    return sent_total


def _process_vertical(
    *,
    vertical_id: str,
    token: str,
    now: datetime,
    engine: Engine,
    api_factory: Callable[[str], TelegramBotApiClient],
    llm_factory: Callable[[str], TextCompletionClient],
) -> int:
    """Обход получателей одной вертикали: выбрать «пора», сгенерировать и доставить."""
    with engine.begin() as conn:
        recipients = DailyForecastRepository(conn).list_recipients(vertical_id=vertical_id)
    due: list[DailyForecastRecipient] = [
        r for r in recipients if should_send_daily_forecast(r.agent_card, now)
    ]
    if not due:
        return 0

    logger.info(
        "daily forecast: vertical=%s recipients=%d due=%d", vertical_id, len(recipients), len(due)
    )
    llm = llm_factory(vertical_id)
    sent = 0
    try:
        with api_factory(token) as api:
            for r in due:
                if _send_one(
                    recipient=r, now=now, engine=engine, api=api, llm=llm, vertical_id=vertical_id
                ):
                    sent += 1
    finally:
        _close_client_if_any(llm)
    return sent


def _send_one(
    *,
    recipient: DailyForecastRecipient,
    now: datetime,
    engine: Engine,
    api: TelegramBotApiClient,
    llm: TextCompletionClient,
    vertical_id: str,
) -> bool:
    """Сгенерировать девиз и доставить одному получателю; при успехе проставить ``last_sent``.

    Любой сбой (LLM/доставка) не рушит батч и НЕ ставит ``last_sent`` — повтор в окне.
    """
    try:
        slogan = build_daily_slogan(recipient.agent_card, llm_client=llm, now=now)
        if slogan is None:
            return False  # LLM недоступен — не шлём «сломанное», last_sent не ставим
        try:
            chat_id = int(recipient.external_user_id)
        except (TypeError, ValueError):
            logger.warning("daily forecast: bad chat_id=%r", recipient.external_user_id)
            return False
        deliver_outbound_messages(
            api,
            chat_id=chat_id,
            messages=[build_daily_forecast_message(slogan)],
            vertical_id=vertical_id,
            user_id=recipient.user_id,
        )
        # Идемпотентность: помечаем день отправленным только ПОСЛЕ успешной доставки.
        with engine.begin() as conn:
            ProfileRepository(conn).merge_agent_card(
                recipient.user_id,
                {AGENT_CARD_DAILY_FORECAST_LAST_SENT: today_str_msk(now)},
            )
        return True
    except Exception:
        logger.exception("daily forecast: send failed user_id=%s", recipient.user_id)
        return False


async def daily_forecast_scheduler_loop(
    *,
    engine_provider: Callable[[], Engine],
    now_provider: Callable[[], datetime] = now_msk,
    sleep_seconds: int = _SLEEP_SECONDS,
) -> None:
    """Бесконечный цикл рассылки: тик → сон. Отменяется через ``task.cancel()``.

    Каждый тик уводит синхронную работу в worker-поток, чтобы не блокировать event-loop.
    ``now`` вычисляется в цикле и передаётся в тик (детерминизм + инжекция в тестах).
    """
    logger.info("daily forecast scheduler started (sleep=%ss)", sleep_seconds)
    try:
        while True:
            try:
                now = now_provider()
                engine = engine_provider()
                await to_thread.run_sync(
                    functools.partial(run_daily_forecast_tick, now=now, engine=engine)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("daily forecast: tick failed")
            await asyncio.sleep(sleep_seconds)
    except asyncio.CancelledError:
        logger.info("daily forecast scheduler stopped")
        raise


def start_daily_forecast_scheduler(
    engine_provider: Callable[[], Engine],
) -> asyncio.Task[Any] | None:
    """Запустить планировщик как фоновую задачу, если он не выключен env-рубильником.

    Возвращает ``asyncio.Task`` (для отмены в lifespan) или ``None``, если рассылка выключена.
    """
    if not daily_forecast_globally_enabled():
        logger.info("daily forecast scheduler disabled (MANDALA_DAILY_FORECAST_ENABLED)")
        return None
    return asyncio.create_task(daily_forecast_scheduler_loop(engine_provider=engine_provider))


async def stop_daily_forecast_scheduler(task: asyncio.Task[Any] | None) -> None:
    """Аккуратно остановить планировщик (отмена + подавление ``CancelledError``)."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
