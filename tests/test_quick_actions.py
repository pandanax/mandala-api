"""Разворот коротких callback-кодов в текст запроса."""

from __future__ import annotations

from mandala.verticals.quick_actions import (
    ASTROLOGY_REPLY_KEYBOARD,
    FORECAST_MENU_CODE,
    expand_inbound_quick_action,
    is_forecast_menu,
    is_forecast_request,
)


def test_expand_astrology_natal() -> None:
    out = expand_inbound_quick_action("astrology", "mdl:natal")
    assert out is not None
    assert "натальн" in out.lower()
    assert out != "mdl:natal"


def test_reply_keyboard_is_content_navigation_only() -> None:
    # Основной поток — навигация по контенту; профиль/сброс/help ушли в бургер-меню.
    flat = [btn for row in ASTROLOGY_REPLY_KEYBOARD for btn in row]
    assert flat == ["🔮 Натальная карта", "📊 Прогноз"]
    assert "👤 Профиль" not in flat
    assert "🔄 Начать заново" not in flat


def test_forecast_button_expands_to_menu_code() -> None:
    assert expand_inbound_quick_action("astrology", "📊 Прогноз") == FORECAST_MENU_CODE
    assert expand_inbound_quick_action("astrology", "mdl:forecast_menu") == FORECAST_MENU_CODE


def test_is_forecast_menu() -> None:
    assert is_forecast_menu(FORECAST_MENU_CODE) is True
    assert is_forecast_menu("  __forecast_menu__  ") is True
    assert is_forecast_menu("mdl:natal") is False
    assert is_forecast_menu(None) is False


def test_is_forecast_request_generic_text_true() -> None:
    # свободный текст-запрос прогноза без периода → показать меню-кнопки
    for t in ("прогноз", "хочу прогноз", "дай прогноз", "Прогноз?", "можно гороскоп"):
        assert is_forecast_request(t) is True, t


def test_is_forecast_request_with_period_false() -> None:
    # период уже назван — не перехватываем, пусть отвечает LLM напрямую
    for t in ("прогноз на неделю", "дай прогноз на сегодня", "прогноз на месяц", "гороскоп на год"):
        assert is_forecast_request(t) is False, t


def test_is_forecast_request_non_forecast_false() -> None:
    for t in ("натальная карта", "привет", "", None, "/start"):
        assert is_forecast_request(t) is False


def test_is_forecast_request_ignores_long_context() -> None:
    long_text = (
        "Расскажи, чем астрологический прогноз отличается от гадания и как его читать правильно"
    )
    assert is_forecast_request(long_text) is False


def test_expand_unknown_code_unchanged() -> None:
    assert expand_inbound_quick_action("astrology", "mdl:zzz") == "mdl:zzz"


def test_expand_therapy() -> None:
    out = expand_inbound_quick_action("therapy", "mdl_th:vent")
    assert out is not None
    assert "выговориться" in out.lower()
