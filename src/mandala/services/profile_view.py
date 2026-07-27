"""Единый рендер профиля клиента.

Используется и командой бургер-меню ``/profile`` (см. ``scenario_intake``), и
callback-кнопкой ``__show_profile__`` (см. ``domain.handler``), чтобы вид профиля был
одинаковым независимо от точки входа.
"""

from __future__ import annotations

from typing import Any

from mandala.domain.contracts import OutboundMessage
from mandala.services.intake_flow import CB_PROFILE_EDIT
from mandala.verticals.client_knowledge import (
    AGENT_CARD_ASTRO_SYSTEM,
    AGENT_CARD_DESTINY_MATRIX_DATA,
    AGENT_CARD_NATAL_CHART_DATA,
)


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def _profile_buttons(vertical_id: str) -> list[list[dict[str, str]]]:
    """Кнопка «Редактировать» (тот же флоу правки) + быстрый доступ к карте/матрице."""
    rows: list[list[dict[str, str]]] = [[_btn("✏️ Редактировать", CB_PROFILE_EDIT)]]
    if vertical_id.strip() == "astrology":
        rows.append([_btn("🪐 Натальная карта", "/natal"), _btn("🌌 Карта судьбы", "/matrix")])
    return rows


def build_profile_message(vertical_id: str, agent_card: dict[str, Any]) -> OutboundMessage:
    """Собрать сообщение «Ваш профиль» из ``agent_card``.

    Под профилем — инлайн-кнопка «Редактировать» (запускает стандартный флоу правки
    по полям с подтверждениями) и быстрый доступ к натальной карте / Карте судьбы.
    Постоянной нижней reply-клавиатуры нет.
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

    matrix_data = agent_card.get(AGENT_CARD_DESTINY_MATRIX_DATA)
    if isinstance(matrix_data, dict) and matrix_data:
        comfort = matrix_data.get("comfort_zone")
        comfort_s = (
            f"{comfort.get('n')} ({comfort.get('name')})" if isinstance(comfort, dict) else "?"
        )
        lines.append("")
        lines.append(f"🌌 **Карта судьбы рассчитана** — зона комфорта: {comfort_s} (/matrix)")

    promo = agent_card.get("activated_promo")
    if isinstance(promo, str) and promo.strip():
        lines.append("")
        lines.append(f"✅ Промо-код активирован: `{promo}` — подписка без ограничений")

    lines.append("")
    lines.append("Полный сброс данных — команда /reset (в меню бота).")

    return OutboundMessage(text="\n".join(lines), buttons=_profile_buttons(vertical_id))
