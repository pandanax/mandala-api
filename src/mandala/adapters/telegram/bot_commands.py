"""Регистрация команд бота через Telegram ``setMyCommands`` при старте приложения.

Чтобы после каждого деплоя список команд подсвечивался в чате автоматически,
вызываем ``setMyCommands`` на старте. Вызов не критичен: любые ошибки глотаем
с предупреждением в лог, старт приложения не ломаем.
"""

from __future__ import annotations

import logging

import httpx

from mandala.adapters.telegram.bot_token import load_bot_token_map
from mandala.adapters.telegram.secrets import mask_bot_token
from mandala.verticals.registry import ASTROLOGY_COMMANDS, get_vertical_definition

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.telegram.org"

# (command, description) — команда без ведущего «/».
# Бургер-меню (☰): «Натальная карта» и «Прогноз» (постоянные точки входа, их больше нет
# среди inline-кнопок под ответами), затем профиль/рестарт/help/промо/покупка сообщений. Основной
# поток inline-кнопок под ответами — контекстная навигация модели «куда дальше», а НЕ
# статические сервисные действия (см. docs/agent.md).
BOT_COMMANDS: list[tuple[str, str]] = list(ASTROLOGY_COMMANDS)


async def register_bot_commands_if_configured(
    *,
    base_url: str = _DEFAULT_BASE,
) -> bool:
    """Зарегистрировать команды бота, если заданы ``TELEGRAM_BOT_TOKEN`` и ``…_VERTICAL_ID``.

    Возвращает ``True`` при успешном вызове ``setMyCommands``, иначе ``False``.
    Любые ошибки (сеть, ``ok: false``, отсутствие env) не пробрасываются — только лог.
    """
    token_map = load_bot_token_map()
    if not token_map:
        logger.info("setMyCommands пропущен: Telegram bot tokens не заданы")
        return False
    all_ok = True
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
    ) as client:
        for vertical_id, token in token_map.items():
            definition = get_vertical_definition(vertical_id)
            if definition is None:
                logger.warning("setMyCommands пропущен: unknown vertical_id=%s", vertical_id)
                all_ok = False
                continue
            commands = [{"command": cmd, "description": desc} for cmd, desc in definition.commands]
            masked = mask_bot_token(token)
            try:
                r = await client.post(
                    f"{base_url.rstrip('/')}/bot{token.strip()}/setMyCommands",
                    json={"commands": commands},
                )
                data = r.json()
                if not isinstance(data, dict) or not data.get("ok"):
                    desc = data.get("description") if isinstance(data, dict) else data
                    logger.warning("setMyCommands вернул ok=false token=%s: %s", masked, desc)
                    all_ok = False
                    continue
            except Exception as e:  # noqa: BLE001
                logger.warning("setMyCommands не выполнен token=%s: %s", masked, e)
                all_ok = False
                continue
            logger.info(
                "setMyCommands ok token=%s vertical_id=%s commands=%s",
                masked,
                vertical_id,
                len(commands),
            )
    return all_ok
