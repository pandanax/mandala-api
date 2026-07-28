"""Единый рендер профиля клиента.

Используется и командой бургер-меню ``/profile`` (см. ``scenario_intake``), и
callback-кнопкой ``__show_profile__`` (см. ``domain.handler``), чтобы вид профиля был
одинаковым независимо от точки входа.
"""

from __future__ import annotations

from typing import Any

from mandala.domain.contracts import OutboundMessage
from mandala.services.intake_flow import CB_PROFILE_EDIT
from mandala.verticals.client_knowledge import AGENT_CARD_ASTRO_SYSTEM


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def _profile_buttons(vertical_id: str) -> list[list[dict[str, str]]]:
    """Ровно 4 кнопки: натальная карта · Карта судьбы · нумерология · обновить профиль."""
    if vertical_id.strip() == "astrology":
        return [
            [_btn("🪐 Натальная карта", "/natal"), _btn("🌌 Карта судьбы", "/matrix")],
            [_btn("🔢 Нумерология", "/numerology"), _btn("✏️ Обновить профиль", CB_PROFILE_EDIT)],
        ]
    return [[_btn("✏️ Обновить профиль", CB_PROFILE_EDIT)]]


def build_profile_message(
    vertical_id: str,
    agent_card: dict[str, Any],
    *,
    message_balance: int | None = None,
) -> OutboundMessage:
    """Собрать сообщение «Ваш профиль» из ``agent_card``.

    Тело — **только данные анкеты** (имя, дата/время/место рождения, система). Всё остальное
    (расчёты натальной карты, Карты судьбы, нумерологии; баланс/промо/пополнение) доступно
    через свои команды и кнопки, поэтому в карточке профиля не дублируется.

    Под профилем — ровно 4 инлайн-кнопки: 🪐 Натальная карта · 🌌 Карта судьбы · 🔢 Нумерология ·
    ✏️ Обновить профиль (стандартный флоу правки по полям). Постоянной нижней reply-клавиатуры нет.

    ``message_balance`` оставлен в сигнатуре для совместимости вызовов, но в теле не рендерится
    (баланс виден через /topup).
    """
    _ = message_balance  # намеренно не показываем в карточке профиля
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

    return OutboundMessage(text="\n".join(lines), buttons=_profile_buttons(vertical_id))
