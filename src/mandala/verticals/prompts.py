"""Системные промпты per ``vertical_id`` (тикет 12).

Шаги анкеты — JSON ``intake_steps.json`` в пакете ``mandala.verticals``
(см. ``intake_config``).
"""

from __future__ import annotations

_DEFAULT_SYSTEM = "Ты полезный ассистент платформы Mandala. Отвечай по-русски, кратко и по делу."

VERTICAL_SYSTEM_PROMPTS: dict[str, str] = {
    "astrology": (
        "Ты дружелюбный ассистент по астрологии (демо-вертикаль). "
        "Не выдавай опасные медицинские советы; отвечай кратко по-русски."
    ),
    "therapy": (
        "Ты эмпатичный ассистент в демо-режиме разговорной поддержки. "
        "Не ставь диагнозы и не заменяй специалиста; отвечай кратко по-русски."
    ),
}


def get_vertical_system_prompt(vertical_id: str) -> str:
    """Вернуть системный промпт для slug вертикали или дефолт."""
    key = vertical_id.strip()
    return VERTICAL_SYSTEM_PROMPTS.get(key, _DEFAULT_SYSTEM)
