"""Long polling: ``getUpdates`` → домен → отправка ответов.

Мультитенантно: ``run_polling_multi`` поллит несколько токенов/вертикалей конкурентно
(по одному потоку на токен), каждый апдейт маршрутизируется в свой ``vertical_id``.
Маппинг токенов — ``bot_token.load_bot_token_map`` (env).
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from sqlalchemy.engine import Engine

from mandala.adapters.telegram.billing_updates import process_telegram_billing_update
from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.bot_token import load_bot_token_map
from mandala.adapters.telegram.callback_ack import answer_callback_query_if_present
from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.adapters.telegram.photo_cache import persist_photo_file_ids
from mandala.adapters.telegram.secrets import mask_bot_token
from mandala.adapters.telegram.typing_keepalive import run_with_typing_keepalive
from mandala.adapters.telegram.voice_transcribe import resolve_voice_to_text
from mandala.db.engine import create_engine_from_env
from mandala.domain import OutboundMessage, handle_inbound
from mandala.observability import op_format

logger = logging.getLogger(__name__)

_ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
_ENV_VERTICAL = "TELEGRAM_VERTICAL_ID"


def resolve_telegram_vertical_id() -> str:
    """``vertical_id`` для одного бота из окружения (обратная совместимость).

    Для нескольких ботов используйте ``run_polling_multi`` + ``load_bot_token_map``.
    """
    raw = os.environ.get(_ENV_VERTICAL, "").strip()
    if not raw:
        msg = (
            f"Задайте {_ENV_VERTICAL} (slug из ``agent_verticals``, например astrology) "
            f"или несколько токенов через {_ENV_TOKEN}_<VERTICAL> / TELEGRAM_BOT_TOKENS."
        )
        raise RuntimeError(msg)
    return raw


def process_telegram_update(
    update: dict[str, Any],
    *,
    vertical_id: str,
    engine: Engine,
    api: TelegramBotApiClient,
) -> None:
    """Один ``update``: маппинг → транзакция БД → доставка исходящих."""
    if process_telegram_billing_update(
        update,
        vertical_id=vertical_id,
        engine=engine,
        api=api,
    ):
        return
    event = telegram_update_to_inbound_event(update, vertical_id=vertical_id)
    if event is None:
        return
    raw_uid = update.get("update_id")
    upd_id = raw_uid if isinstance(raw_uid, int) else None
    logger.info(
        "funnel telegram_inbound %s",
        op_format(
            vertical_id=vertical_id,
            channel="telegram",
            stage="mapped",
            update_id=upd_id,
        ),
    )
    raw = event.raw_ref or {}
    chat_id = raw.get("chat_id")
    if chat_id is None:
        logger.warning("telegram: нет chat_id в raw_ref, update_id=%s", update.get("update_id"))
        return

    # Голос/аудио → текст (STT) до общего пайплайна; сбой = мягкое сообщение, не падаем.
    resolution = resolve_voice_to_text(event, api)
    if resolution.soft_message is not None:
        deliver_outbound_messages(
            api,
            chat_id=int(chat_id),
            messages=[OutboundMessage(text=resolution.soft_message)],
            vertical_id=vertical_id,
        )
        return
    event = resolution.event

    with engine.begin() as conn:
        outbound = run_with_typing_keepalive(api, int(chat_id), lambda: handle_inbound(event, conn))

    uploaded = deliver_outbound_messages(
        api,
        chat_id=int(chat_id),
        messages=outbound,
        vertical_id=vertical_id,
    )
    persist_photo_file_ids(engine, event, uploaded)
    answer_callback_query_if_present(api, update)


def run_polling_forever(
    *,
    bot_token: str | None = None,
    vertical_id: str | None = None,
    engine: Engine | None = None,
) -> None:
    """Бесконечный long polling (MVP). Останавливается по ``KeyboardInterrupt``."""
    token = (bot_token or os.environ.get(_ENV_TOKEN, "")).strip()
    if not token:
        msg = f"Задайте {_ENV_TOKEN}"
        raise RuntimeError(msg)
    vid = vertical_id if vertical_id is not None else resolve_telegram_vertical_id()
    eng = engine if engine is not None else create_engine_from_env()

    logger.info(
        "telegram polling старт vertical_id=%s token=%s",
        vid,
        mask_bot_token(token),
    )
    next_offset: int | None = None
    with TelegramBotApiClient(token) as api:
        while True:
            updates = api.get_updates(offset=next_offset, timeout=30)
            for u in updates:
                uid = u.get("update_id")
                if isinstance(uid, int):
                    next_offset = uid + 1
                try:
                    process_telegram_update(u, vertical_id=vid, engine=eng, api=api)
                except Exception:
                    logger.exception(
                        "telegram: ошибка обработки update_id=%s token=%s",
                        u.get("update_id"),
                        mask_bot_token(token),
                    )


def run_polling_multi(
    *,
    token_map: dict[str, str] | None = None,
    engine: Engine | None = None,
) -> None:
    """Поллить несколько токенов/вертикалей конкурентно (по потоку на токен).

    ``token_map`` (``vertical_id → token``) по умолчанию берётся из окружения
    (``load_bot_token_map``). Один engine на все потоки (пул потокобезопасен).
    Для одной вертикали работает как ``run_polling_forever`` (без лишних потоков).
    """
    mapping = token_map if token_map is not None else load_bot_token_map()
    if not mapping:
        msg = (
            f"Не задан ни один токен: {_ENV_TOKEN}+{_ENV_VERTICAL}, "
            f"{_ENV_TOKEN}_<VERTICAL> или TELEGRAM_BOT_TOKENS."
        )
        raise RuntimeError(msg)

    eng = engine if engine is not None else create_engine_from_env()

    if len(mapping) == 1:
        (only_vid, only_token) = next(iter(mapping.items()))
        run_polling_forever(bot_token=only_token, vertical_id=only_vid, engine=eng)
        return

    logger.info("telegram polling мультитенант: вертикали=%s", sorted(mapping))
    threads: list[threading.Thread] = []
    for vid, token in mapping.items():
        thread = threading.Thread(
            target=run_polling_forever,
            kwargs={"bot_token": token, "vertical_id": vid, "engine": eng},
            name=f"tg-poll-{vid}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()
