"""Профиль: горячие команды /natal и /matrix присутствуют в тексте.

Оффлайн, без сети/БД — чистый рендер ``build_profile_message``.
"""

from __future__ import annotations

from mandala.services.profile_view import build_profile_message
from mandala.verticals.client_knowledge import (
    AGENT_CARD_DESTINY_MATRIX_DATA,
    AGENT_CARD_NATAL_CHART_DATA,
)


def _tokens(text: str | None) -> set[str]:
    return set((text or "").split())


def test_natal_block_carries_hot_natal_command() -> None:
    card = {
        "full_name": "Тест",
        "birth_date": "07.01.1987",
        AGENT_CARD_NATAL_CHART_DATA: {
            "sun_sign": "Козерог",
            "moon_sign": "Лев",
            "ascendant": "Рыбы",
            "calculated_at": "2026-07-28T10:00:00",
        },
    }
    msg = build_profile_message("astrology", card)
    # Горячая команда должна стоять отдельным токеном (Telegram сделает её кликабельной).
    assert "/natal" in _tokens(msg.text)


def test_matrix_block_carries_hot_matrix_command() -> None:
    card = {
        "full_name": "Тест",
        "birth_date": "07.01.1987",
        AGENT_CARD_DESTINY_MATRIX_DATA: {
            "comfort_zone": {"n": 8, "name": "Сила"},
        },
    }
    msg = build_profile_message("astrology", card)
    assert "/matrix" in (msg.text or "")


def test_both_hot_commands_present_together() -> None:
    card = {
        "birth_date": "07.01.1987",
        AGENT_CARD_NATAL_CHART_DATA: {"sun_sign": "Козерог", "moon_sign": "Лев"},
        AGENT_CARD_DESTINY_MATRIX_DATA: {"comfort_zone": {"n": 8, "name": "Сила"}},
    }
    text = build_profile_message("astrology", card).text
    assert "/natal" in _tokens(text)
    assert "/matrix" in (text or "")


def test_no_natal_command_without_natal_data() -> None:
    card = {"birth_date": "07.01.1987"}
    text = build_profile_message("astrology", card).text
    assert "/natal" not in _tokens(text)
