"""Мгновенный человекочитаемый рендер натальной карты и Матрицы Судьбы из БД.

Команды ``/natal`` и ``/matrix`` должны отвечать ПОЧТИ МГНОВЕННО — это
детерминированный рендер уже сохранённых в ``agent_card`` данных, БЕЗ прогона через
LLM (иначе не «мгновенно») и без пересчёта. Математику считает и сохраняет
``services.scenario_intake`` при сохранении профиля (Swiss Ephemeris для карты,
модуль Матрицы Судьбы для матрицы) — здесь только форматирование.

Кнопки под рендером — инлайн-навигация «что дальше»: углублённый разбор через LLM
(``mdl:natal`` / ``mdl:matrix``), переход между картой и матрицей (``/natal`` /
``/matrix`` как callback-команды), прогноз и профиль.
"""

from __future__ import annotations

from typing import Any

from mandala.domain.contracts import OutboundMessage


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def natal_nav_buttons() -> list[list[dict[str, str]]]:
    """Навигация под натальной картой."""
    return [
        [_btn("🔮 Углублённый разбор (ИИ)", "mdl:natal")],
        [_btn("🌌 Карта судьбы", "/matrix"), _btn("📊 Прогноз", "mdl:forecast_menu")],
        [_btn("👤 Профиль", "mdl:profile")],
    ]


def matrix_nav_buttons() -> list[list[dict[str, str]]]:
    """Навигация под Матрицей Судьбы."""
    return [
        [_btn("🔮 Разбор Карты судьбы (ИИ)", "mdl:matrix")],
        [_btn("🪐 Натальная карта", "/natal"), _btn("📊 Прогноз", "mdl:forecast_menu")],
        [_btn("👤 Профиль", "mdl:profile")],
    ]


def render_natal_chart_text(natal_data: dict[str, Any]) -> str:
    """Человекочитаемый разбор сохранённой натальной карты (без LLM)."""
    system = natal_data.get("chart_system", "")
    lines: list[str] = ["🪐 **Ваша натальная карта**"]
    if system:
        lines.append(f"_Система: {system}_")
    lines.append("")
    lines.append(f"☀️ **Солнце:** {natal_data.get('sun_sign', '?')}")
    lines.append(f"🌙 **Луна:** {natal_data.get('moon_sign', '?')}")
    asc = natal_data.get("ascendant")
    if asc:
        lines.append(f"⬆️ **Асцендент:** {asc}")
    elif not natal_data.get("time_known", True):
        lines.append("⬆️ **Асцендент:** время рождения неизвестно — не рассчитан")

    planets = natal_data.get("planets")
    if isinstance(planets, dict) and planets:
        lines.append("")
        lines.append("**Планеты в знаках:**")
        for planet, data in planets.items():
            if not isinstance(data, dict):
                continue
            sign = data.get("sign", "?")
            deg = data.get("degree")
            house = data.get("house")
            retro = " ℞" if data.get("retrograde") else ""
            piece = f"• {planet}: {sign}"
            if isinstance(deg, (int, float)):
                piece += f", {deg}°"
            if house:
                piece += f" (дом {house})"
            piece += retro
            lines.append(piece)

    aspects = natal_data.get("aspects")
    if isinstance(aspects, list) and aspects:
        lines.append("")
        lines.append("**Ключевые аспекты:**")
        for asp in aspects[:8]:
            if not isinstance(asp, dict):
                continue
            orb = asp.get("orb")
            orb_s = f" (орб {orb}°)" if isinstance(orb, (int, float)) else ""
            lines.append(
                f"• {asp.get('planet1', '?')} — {asp.get('planet2', '?')}: "
                f"{asp.get('aspect', '?')}{orb_s}"
            )

    lines.append("")
    lines.append("Нажмите «Углублённый разбор», чтобы я растолковал карту подробно.")
    return "\n".join(lines)


def render_natal_chart_message(natal_data: dict[str, Any]) -> OutboundMessage:
    return OutboundMessage(text=render_natal_chart_text(natal_data), buttons=natal_nav_buttons())


def _arc_str(arc: object) -> str:
    if isinstance(arc, dict):
        n = arc.get("n")
        name = arc.get("name", "?")
        return f"{n} ({name})" if n is not None else str(name)
    return str(arc)


def render_destiny_matrix_text(dm: dict[str, Any]) -> str:
    """Человекочитаемый рендер сохранённой Матрицы Судьбы (без LLM).

    Показываем ядро (личностный квадрат), предназначение и денежный/любовный каналы —
    это КОДЫ (арканы), их толкование даёт углублённый разбор через ИИ (кнопка ниже).
    """
    lines: list[str] = ["🌌 **Ваша Карта судьбы** (Матрица Судьбы)"]
    bd = dm.get("birth_date")
    if bd:
        lines.append(f"_По дате рождения: {bd}_")
    lines.append("")
    lines.append("**Личностный квадрат:**")
    lines.append(f"• Портрет (день): {_arc_str(dm.get('day'))}")
    lines.append(f"• Таланты рода (месяц): {_arc_str(dm.get('month'))}")
    lines.append(f"• Материальная карма (год): {_arc_str(dm.get('year'))}")
    lines.append(f"• Кармическая задача: {_arc_str(dm.get('karma'))}")
    lines.append(f"• ⭐ Зона комфорта (центр): {_arc_str(dm.get('comfort_zone'))}")

    purpose = dm.get("purpose")
    if isinstance(purpose, dict):
        lines.append("")
        lines.append("**Предназначение:**")
        lines.append(f"• Личное (до ~40): {_arc_str(purpose.get('personal'))}")
        lines.append(f"• Социальное (~40–60): {_arc_str(purpose.get('social'))}")
        lines.append(f"• Духовное (60+): {_arc_str(purpose.get('spiritual'))}")

    money = dm.get("money_line")
    love = dm.get("relationship_line")
    if isinstance(money, dict) or isinstance(love, dict):
        lines.append("")
        lines.append("**Каналы:**")
        if isinstance(money, dict):
            lines.append(f"• 💰 Деньги: итог {_arc_str(money.get('total'))}")
        if isinstance(love, dict):
            lines.append(f"• ❤️ Отношения: итог {_arc_str(love.get('total'))}")

    lines.append("")
    lines.append("Нажмите «Разбор Карты судьбы», чтобы я растолковал арканы подробно.")
    return "\n".join(lines)


def render_destiny_matrix_message(dm: dict[str, Any]) -> OutboundMessage:
    return OutboundMessage(text=render_destiny_matrix_text(dm), buttons=matrix_nav_buttons())


__all__ = [
    "matrix_nav_buttons",
    "natal_nav_buttons",
    "render_destiny_matrix_message",
    "render_destiny_matrix_text",
    "render_natal_chart_message",
    "render_natal_chart_text",
]
