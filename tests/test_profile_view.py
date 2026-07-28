"""Профиль: тело — только данные анкеты, под ним ровно 4 кнопки.

Оффлайн, без сети/БД — чистый рендер ``build_profile_message``.
"""

from __future__ import annotations

from mandala.domain.contracts import OutboundMessage
from mandala.services.intake_flow import CB_PROFILE_EDIT
from mandala.services.profile_view import build_profile_message
from mandala.verticals.client_knowledge import (
    AGENT_CARD_DESTINY_MATRIX_DATA,
    AGENT_CARD_NATAL_CHART_DATA,
    AGENT_CARD_NUMEROLOGY_DATA,
)


def _all_callbacks(msg: OutboundMessage) -> set[str]:
    return {b["callback_data"] for row in (msg.buttons or []) for b in row}


def test_body_carries_only_intake_fields() -> None:
    card = {
        "full_name": "Тест Тестов",
        "birth_date": "07.01.1987",
        "birth_place": "Москва",
        "birth_time": "14:30",
        "astro_system": "western",
    }
    text = build_profile_message("astrology", card).text or ""
    assert "Ваш профиль" in text
    assert "Тест Тестов" in text
    assert "07.01.1987" in text
    assert "Москва" in text
    assert "14:30" in text
    assert "Западная" in text


def test_body_omits_computed_blocks_and_balance() -> None:
    """Тело не дублирует расчёты карт/матрицы/нумерологии и прозу про баланс/topup."""
    card = {
        "full_name": "Тест",
        "birth_date": "07.01.1987",
        AGENT_CARD_NATAL_CHART_DATA: {"sun_sign": "Козерог", "moon_sign": "Лев"},
        AGENT_CARD_DESTINY_MATRIX_DATA: {"comfort_zone": {"n": 8, "name": "Сила"}},
        AGENT_CARD_NUMEROLOGY_DATA: {"numbers": {"life_path": 5}},
        "activated_promo": "TESTME",
    }
    text = build_profile_message("astrology", card, message_balance=7).text or ""
    for forbidden in (
        "рассчитана",
        "Солнце",
        "зона комфорта",
        "жизненный путь",
        "Осталось сообщений",
        "/topup",
        "безлимит",
    ):
        assert forbidden not in text, forbidden


def test_exactly_four_buttons_with_expected_callbacks() -> None:
    text_msg = build_profile_message("astrology", {"birth_date": "07.01.1987"})
    buttons = [b for row in (text_msg.buttons or []) for b in row]
    assert len(buttons) == 4
    assert _all_callbacks(text_msg) == {"/natal", "/matrix", "/numerology", CB_PROFILE_EDIT}


def test_message_balance_arg_accepted_but_not_rendered() -> None:
    """Совместимость: ``message_balance`` принимается, но в теле не показывается."""
    text = build_profile_message("astrology", {"birth_date": "07.01.1987"}, message_balance=3).text
    assert "3" not in (text or "").replace("07.01.1987", "")
    assert "Осталось" not in (text or "")
