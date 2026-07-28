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
    AGENT_CARD_NUMEROLOGY_DATA,
)


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def _profile_buttons(vertical_id: str) -> list[list[dict[str, str]]]:
    """Кнопка «Редактировать» (тот же флоу правки) + быстрый доступ к карте/матрице."""
    rows: list[list[dict[str, str]]] = [[_btn("✏️ Редактировать", CB_PROFILE_EDIT)]]
    if vertical_id.strip() == "astrology":
        rows.append([_btn("🪐 Натальная карта", "/natal"), _btn("🌌 Карта судьбы", "/matrix")])
        rows.append([_btn("🔢 Нумерология", "/numerology")])
    return rows


def build_profile_message(
    vertical_id: str,
    agent_card: dict[str, Any],
    *,
    message_balance: int | None = None,
) -> OutboundMessage:
    """Собрать сообщение «Ваш профиль» из ``agent_card``.

    Под профилем — инлайн-кнопка «Редактировать» (запускает стандартный флоу правки
    по полям с подтверждениями) и быстрый доступ к натальной карте / Карте судьбы.
    Постоянной нижней reply-клавиатуры нет.

    ``message_balance`` — баланс кошелька сообщений (пакетная модель): показываем строкой
    «Осталось сообщений: N». При активном промо («вечный пакет») — «∞ (безлимит)», число не
    показываем. Если баланс не передан и промо нет — строку опускаем (безопасный дефолт).
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
        lines.append("🪐 **Натальная карта рассчитана** — открыть: /natal")
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

    numerology_data = agent_card.get(AGENT_CARD_NUMEROLOGY_DATA)
    if isinstance(numerology_data, dict) and numerology_data:
        numbers = numerology_data.get("numbers")
        life_path = numbers.get("life_path") if isinstance(numbers, dict) else None
        lp_s = f" — жизненный путь: {life_path}" if life_path is not None else ""
        lines.append("")
        lines.append(f"🔢 **Нумерология рассчитана**{lp_s} — открыть: /numerology")

    promo = agent_card.get("activated_promo")
    promo_active = isinstance(promo, str) and bool(promo.strip())

    # Баланс кошелька сообщений. Промо = «вечный пакет» → безлимит (∞), число не показываем.
    if promo_active:
        lines.append("")
        lines.append("💬 **Сообщения:** ∞ (безлимит)")
    elif message_balance is not None:
        lines.append("")
        lines.append(f"💬 **Осталось сообщений:** {message_balance}")

    if promo_active:
        lines.append("")
        lines.append(f"✅ Промо-код активирован: `{promo}` — вечный пакет (безлимит навсегда)")

    lines.append("")
    lines.append("Пополнить баланс — /topup. Полный сброс данных — /reset (в меню бота).")

    return OutboundMessage(text="\n".join(lines), buttons=_profile_buttons(vertical_id))
