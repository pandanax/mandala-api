"""Регистрация команд бота через Telegram ``setMyCommands`` при старте приложения.

Чтобы после каждого деплоя список команд подсвечивался в чате автоматически,
вызываем ``setMyCommands`` на старте. Вызов не критичен: любые ошибки глотаем
с предупреждением в лог, старт приложения не ломаем.
"""

from __future__ import annotations

import logging
import os

import httpx

from mandala.adapters.telegram.secrets import mask_bot_token

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.telegram.org"

# (command, description) — команда без ведущего «/».
# Бургер-меню (☰): «Натальная карта» и «Прогноз» (постоянные точки входа, их больше нет
# среди inline-кнопок под ответами), затем профиль/рестарт/help/промо/покупка сообщений. Основной
# поток inline-кнопок под ответами — контекстная навигация модели «куда дальше», а НЕ
# статические сервисные действия (см. docs/agent.md).
BOT_COMMANDS: list[tuple[str, str]] = [
    ("natal", "Натальная карта"),
    ("matrix", "Матрица судьбы"),
    ("numerology", "Нумерология"),
    ("forecast", "Прогноз"),
    ("profile", "Мой профиль"),
    ("start", "Начать заново"),
    ("reset", "Полный сброс профиля"),
    ("help", "Помощь"),
    ("promo", "Промо-код"),
    ("topup", "Купить сообщения"),
]


async def register_bot_commands_if_configured(
    *,
    base_url: str = _DEFAULT_BASE,
) -> bool:
    """Зарегистрировать команды бота, если заданы ``TELEGRAM_BOT_TOKEN`` и ``…_VERTICAL_ID``.

    Возвращает ``True`` при успешном вызове ``setMyCommands``, иначе ``False``.
    Любые ошибки (сеть, ``ok: false``, отсутствие env) не пробрасываются — только лог.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    vertical_id = os.environ.get("TELEGRAM_VERTICAL_ID")
    if not token or not vertical_id:
        logger.info("setMyCommands пропущен: TELEGRAM_BOT_TOKEN / TELEGRAM_VERTICAL_ID не заданы")
        return False

    commands = [{"command": cmd, "description": desc} for cmd, desc in BOT_COMMANDS]
    url = f"{base_url.rstrip('/')}/bot{token.strip()}/setMyCommands"
    masked = mask_bot_token(token)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        ) as client:
            r = await client.post(url, json={"commands": commands})
        data = r.json()
        if not isinstance(data, dict) or not data.get("ok"):
            desc = data.get("description") if isinstance(data, dict) else data
            logger.warning("setMyCommands вернул ok=false token=%s: %s", masked, desc)
            return False
    except Exception as e:  # noqa: BLE001 — старт не должен падать из-за Telegram
        logger.warning("setMyCommands не выполнен token=%s: %s", masked, e)
        return False

    logger.info(
        "setMyCommands ok token=%s vertical_id=%s commands=%s",
        masked,
        vertical_id,
        len(commands),
    )
    return True
