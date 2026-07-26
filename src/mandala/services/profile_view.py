"""Единый рендер профиля клиента.

Используется и командой бургер-меню ``/profile`` (см. ``scenario_intake``), и
callback-кнопкой ``__show_profile__`` (см. ``domain.handler``), чтобы вид профиля был
одинаковым независимо от точки входа.
"""

from __future__ import annotations

from typing import Any

from mandala.domain.contracts import OutboundMessage
from mandala.verticals.client_knowledge import (
    AGENT_CARD_ASTRO_SYSTEM,
    AGENT_CARD_NATAL_CHART_DATA,
)


def build_profile_message(vertical_id: str, agent_card: dict[str, Any]) -> OutboundMessage:
    """Собрать сообщение «Ваш профиль» из ``agent_card``.

    Инлайн-навигация под сообщением добавляется вызывающим кодом
    (:func:`mandala.services.nav_guarantee.ensure_nav`); постоянной нижней
    reply-клавиатуры больше нет. Профиль/сброс/help живут в бургер-меню, поэтому
    здесь только показ данных и подсказка по сбросу.
    """
    lines: list[str] = ["👤 **Ваш профиль**", ""]

    for key, label in (
        ("full_name", "Имя"),
        ("birth_date", "Дата рождения"),
        ("birth_place", "Место рождения"),
        ("birth_time", "Время рождения"),
    ):
        val = agent_card.get(key)
        if isinstance(val, str) and val.strip():
            lines.append(f"**{label}:** {val.strip()}")

    system = agent_card.get(AGENT_CARD_ASTRO_SYSTEM)
    if isinstance(system, str) and system.strip():
        label = "🕉️ Ведическая (Lahiri)" if system == "vedic" else "🌟 Западная (тропическая)"
        lines.append(f"**Система:** {label}")

    natal_data = agent_card.get(AGENT_CARD_NATAL_CHART_DATA)
    if isinstance(natal_data, dict) and natal_data:
        lines.append("")
        lines.append("🪐 **Рассчитанная натальная карта:**")
        lines.append(f"  ☀️ Солнце: {natal_data.get('sun_sign', '?')}")
        lines.append(f"  🌙 Луна: {natal_data.get('moon_sign', '?')}")
        asc = natal_data.get("ascendant")
        if asc:
            lines.append(f"  ⬆️ Асцендент: {asc}")
        calc_at = natal_data.get("calculated_at", "")
        if calc_at:
            lines.append(f"  📐 Рассчитано: {str(calc_at)[:10]}")
    elif agent_card.get("natal_chart_text"):
        lines.append("")
        lines.append("📋 Натальная карта сохранена (текстовая версия).")

    promo = agent_card.get("activated_promo")
    if isinstance(promo, str) and promo.strip():
        lines.append("")
        lines.append(f"✅ Промо-код активирован: `{promo}` — подписка без ограничений")

    lines.append("")
    lines.append("Полный сброс данных — команда /reset (в меню бота).")

    return OutboundMessage(text="\n".join(lines))
