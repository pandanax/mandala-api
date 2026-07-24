"""Короткие callback_data (Telegram ≤64 байта) → текст запроса для LLM.

Также: маппинг текстов кнопок ReplyKeyboard → те же callback-коды для единой точки расширения.
"""

from __future__ import annotations

_ASTROLOGY: dict[str, str] = {
    "mdl:natal": (
        "Составь детальную интерпретацию моей натальной карты по рассчитанным данным: "
        "позиции планет, доминанты, ключевые аспекты. Опиши характер, сильные стороны "
        "и основные жизненные темы."
    ),
    "mdl:fc_today": "Дай персональный астрологический прогноз на сегодня по моей карте.",
    "mdl:fc_week": "Дай астрологический прогноз на ближайшую неделю по моей карте.",
    "mdl:fc_month": "Дай астрологический прогноз на ближайший месяц по моей карте.",
    "mdl:fc_year": "Дай астрологический прогноз на ближайший год по моей карте.",
    "mdl:syn": (
        "Расскажи, как лучше запросить у тебя разбор совместимости: "
        "какие данные нужны о втором человеке."
    ),
    "mdl:th_fin": (
        "Сделай тематический разбор сохранённой натальной карты: сфера финансов, "
        "ресурса и стабильности."
    ),
    "mdl:th_rel": (
        "Сделай тематический разбор сохранённой натальной карты: отношения и партнёрство."
    ),
    "mdl:th_health": (
        "Сделай мягкий тематический разбор натальной карты в части энергии и режима: "
        "без медицинских диагнозов и назначений."
    ),
    # Переключение астрологической системы
    "mdl:switch_western": "__switch_system:western__",
    "mdl:switch_vedic": "__switch_system:vedic__",
    # Профиль и сброс
    "mdl:profile": "__show_profile__",
}

# Тексты кнопок ReplyKeyboard → код быстрого действия
_KEYBOARD_TEXT_TO_CODE: dict[str, str] = {
    "🔮 Натальная карта": "mdl:natal",
    "📅 Прогноз сегодня": "mdl:fc_today",
    "📆 На неделю": "mdl:fc_week",
    "🗓️ На месяц": "mdl:fc_month",
    "🔭 На год": "mdl:fc_year",
    "🌟 Западная": "mdl:switch_western",
    "🕉️ Ведическая": "mdl:switch_vedic",
    "💰 Финансы": "mdl:th_fin",
    "❤️ Отношения": "mdl:th_rel",
    "⚡ Здоровье": "mdl:th_health",
    "👤 Мой профиль": "mdl:profile",
    "🔄 Начать заново": "/reset",
}

# Специальные коды кнопок, которые не передаются в LLM, а обрабатываются иначе
SPECIAL_BUTTON_CODES = frozenset({"__switch_system:western__", "__switch_system:vedic__"})
RESET_BUTTON_TEXT = "🔄 Начать заново"

# Постоянная нижняя клавиатура для вертикали astrology
ASTROLOGY_REPLY_KEYBOARD: list[list[str]] = [
    ["🔮 Натальная карта", "📅 Прогноз сегодня"],
    ["📆 На неделю", "🗓️ На месяц", "🔭 На год"],
    ["🌟 Западная", "🕉️ Ведическая"],
    ["💰 Финансы", "❤️ Отношения", "⚡ Здоровье"],
    ["👤 Мой профиль", "🔄 Начать заново"],
]

_THERAPY: dict[str, str] = {
    "mdl_th:vent": "Хочется выговориться и привести мысли в порядок.",
    "mdl_th:mood": "Сейчас тяжело с настроением — помоги разобраться, с чего начать.",
    "mdl_th:anx": "Чувствую сильную тревогу — помоги структурировать, что происходит.",
}


def expand_inbound_quick_action(vertical_id: str, text: str | None) -> str | None:
    """Если ``text`` — известный код кнопки или текст ReplyKeyboard → полный запрос."""
    if text is None:
        return None
    raw = text.strip()
    if not raw:
        return text
    v = vertical_id.strip()
    table = _ASTROLOGY if v == "astrology" else _THERAPY if v == "therapy" else {}

    # Прямой код кнопки (callback_data или известный mdl:* ключ)
    expanded = table.get(raw)
    if expanded is not None:
        return expanded

    # Текст нижней клавиатуры → код → запрос
    code = _KEYBOARD_TEXT_TO_CODE.get(raw)
    if code is not None:
        return table.get(code, raw)

    return text


def is_system_switch(text: str | None) -> tuple[bool, str]:
    """Вернуть (True, 'western'|'vedic') если действие — переключение системы."""
    if text is None:
        return False, ""
    if text.strip() == "__switch_system:western__":
        return True, "western"
    if text.strip() == "__switch_system:vedic__":
        return True, "vedic"
    return False, ""


def is_show_profile(text: str | None) -> bool:
    """Вернуть True если действие — показать профиль клиента."""
    return text is not None and text.strip() == "__show_profile__"


def is_reset_button(text: str | None) -> bool:
    """Вернуть True если нажата кнопка сброса."""
    return text is not None and text.strip() == RESET_BUTTON_TEXT
