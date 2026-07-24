"""Разворот коротких callback-кодов в текст запроса."""

from __future__ import annotations

from mandala.verticals.quick_actions import (
    ASTROLOGY_REPLY_KEYBOARD,
    FORECAST_MENU_CODE,
    expand_inbound_quick_action,
    is_forecast_menu,
)


def test_expand_astrology_natal() -> None:
    out = expand_inbound_quick_action("astrology", "mdl:natal")
    assert out is not None
    assert "натальн" in out.lower()
    assert out != "mdl:natal"


def test_reply_keyboard_is_four_buttons_two_rows() -> None:
    assert len(ASTROLOGY_REPLY_KEYBOARD) == 2
    assert all(len(row) == 2 for row in ASTROLOGY_REPLY_KEYBOARD)
    flat = [btn for row in ASTROLOGY_REPLY_KEYBOARD for btn in row]
    assert flat == ["🔮 Натальная карта", "📊 Прогноз", "👤 Профиль", "🔄 Начать заново"]


def test_forecast_button_expands_to_menu_code() -> None:
    assert expand_inbound_quick_action("astrology", "📊 Прогноз") == FORECAST_MENU_CODE
    assert expand_inbound_quick_action("astrology", "mdl:forecast_menu") == FORECAST_MENU_CODE


def test_is_forecast_menu() -> None:
    assert is_forecast_menu(FORECAST_MENU_CODE) is True
    assert is_forecast_menu("  __forecast_menu__  ") is True
    assert is_forecast_menu("mdl:natal") is False
    assert is_forecast_menu(None) is False


def test_expand_unknown_code_unchanged() -> None:
    assert expand_inbound_quick_action("astrology", "mdl:zzz") == "mdl:zzz"


def test_expand_therapy() -> None:
    out = expand_inbound_quick_action("therapy", "mdl_th:vent")
    assert out is not None
    assert "выговориться" in out.lower()
