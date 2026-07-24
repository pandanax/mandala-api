"""Подменю прогноза, постоянная клавиатура, /help с картинкой и setMyCommands."""

from __future__ import annotations

import asyncio

from mandala.domain.contracts import OutboundMessage
from mandala.domain.handler import _handle_forecast_menu, _with_astrology_keyboard
from mandala.services.scenario_intake import _astrology_help_message
from mandala.verticals.quick_actions import ASTROLOGY_REPLY_KEYBOARD


def test_forecast_menu_returns_four_period_inline_buttons() -> None:
    out = _handle_forecast_menu()
    assert len(out) == 1
    msg = out[0]
    assert msg.buttons is not None
    codes = [cell["callback_data"] for row in msg.buttons for cell in row]
    assert codes == ["mdl:fc_today", "mdl:fc_week", "mdl:fc_month", "mdl:fc_year"]
    # Подменю показывается без вызова LLM и сохраняет нижнюю клавиатуру.
    assert msg.reply_keyboard == ASTROLOGY_REPLY_KEYBOARD


def test_with_astrology_keyboard_adds_to_last_message() -> None:
    result = [OutboundMessage(text="a"), OutboundMessage(text="b")]
    out = _with_astrology_keyboard(result, "astrology")
    assert out[-1].reply_keyboard == ASTROLOGY_REPLY_KEYBOARD
    # Не перетираем клавиатуру у уже сконфигурированного сообщения.
    assert out[0].reply_keyboard is None


def test_with_astrology_keyboard_preserves_existing_keyboard() -> None:
    custom = [["x"]]
    result = [OutboundMessage(text="a", reply_keyboard=custom)]
    out = _with_astrology_keyboard(result, "astrology")
    assert out[-1].reply_keyboard == custom


def test_with_astrology_keyboard_skips_other_verticals_and_empty() -> None:
    result = [OutboundMessage(text="a")]
    assert _with_astrology_keyboard(result, "therapy")[0].reply_keyboard is None
    assert _with_astrology_keyboard([], "astrology") == []


def test_help_message_has_photo_and_bold_menu() -> None:
    out = _astrology_help_message()
    assert len(out) == 1
    msg = out[0]
    assert msg.photo is not None and msg.photo.startswith("https://")
    assert msg.text is not None
    # Жирный оформлен markdown-ом **…**, который delivery-слой превратит в <b> HTML.
    assert "**Mandala**" in msg.text
    assert "**Меню**" in msg.text
    assert "**Команды**" in msg.text
    assert msg.reply_keyboard == ASTROLOGY_REPLY_KEYBOARD


def test_register_bot_commands_noop_without_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_VERTICAL_ID", raising=False)
    from mandala.adapters.telegram.bot_commands import register_bot_commands_if_configured

    assert asyncio.run(register_bot_commands_if_configured()) is False
