"""Typed product registry and capability boundary for every vertical."""

from __future__ import annotations

from dataclasses import dataclass

BotCommand = tuple[str, str]

ASTROLOGY_COMMANDS: tuple[BotCommand, ...] = (
    ("natal", "Натальная карта"),
    ("matrix", "Матрица судьбы"),
    ("numerology", "Нумерология"),
    ("forecast", "Прогноз"),
    ("morning", "Утренний прогноз"),
    ("profile", "Мой профиль"),
    ("start", "Начать заново"),
    ("reset", "Полный сброс профиля"),
    ("help", "Помощь"),
    ("promo", "Промо-код"),
    ("topup", "Купить сообщения"),
)

NUTRITION_COMMANDS: tuple[BotCommand, ...] = (
    ("start", "Начать"),
    ("meal", "Записать, что съел"),
    ("foodlog", "Дневник за сегодня"),
    ("foodweek", "Дневник за 7 дней"),
    ("profile", "Мой профиль"),
    ("plan", "План питания"),
    ("checkin", "Отметить прогресс"),
    ("help", "Помощь"),
    ("reset", "Полный сброс профиля"),
    ("promo", "Промо-код"),
    ("topup", "Купить сообщения"),
)


@dataclass(frozen=True, slots=True)
class VerticalDefinition:
    slug: str
    commands: tuple[BotCommand, ...]
    capabilities: frozenset[str]
    allowed_llm_profile_keys: frozenset[str]

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


_COMMON = frozenset({"text_chat", "rag", "profile", "message_wallet", "telegram_stars"})

VERTICALS: dict[str, VerticalDefinition] = {
    "astrology": VerticalDefinition(
        slug="astrology",
        commands=ASTROLOGY_COMMANDS,
        capabilities=_COMMON | {"astrology_tools", "daily_forecast", "image_generation"},
        allowed_llm_profile_keys=frozenset({"nav_map"}),
    ),
    "therapy": VerticalDefinition(
        slug="therapy",
        commands=(("start", "Начать"), ("profile", "Мой профиль"), ("help", "Помощь")),
        capabilities=_COMMON | {"image_generation"},
        allowed_llm_profile_keys=frozenset(),
    ),
    "nutrition": VerticalDefinition(
        slug="nutrition",
        commands=NUTRITION_COMMANDS,
        capabilities=_COMMON | {"meal_plan", "check_in", "food_diary"},
        allowed_llm_profile_keys=frozenset({"nav_map"}),
    ),
}


def get_vertical_definition(vertical_id: str) -> VerticalDefinition | None:
    return VERTICALS.get(vertical_id.strip())


def has_capability(vertical_id: str, capability: str) -> bool:
    definition = get_vertical_definition(vertical_id)
    return bool(definition and definition.supports(capability))
