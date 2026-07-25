"""Подменю прогноза, постоянная клавиатура, /help с картинкой, setMyCommands, сплиттер."""

from __future__ import annotations

import asyncio

from mandala.adapters.telegram.text_format import TELEGRAM_MAX_TEXT_CHARS, split_text_for_telegram
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


def test_split_text_short_returns_single_chunk() -> None:
    text = "Короткий текст"
    assert split_text_for_telegram(text) == [text]


def test_split_text_long_paragraph_breaks_at_boundary() -> None:
    # Два абзаца, суммарно > лимита — должны стать двумя кусками.
    para_a = "А" * (TELEGRAM_MAX_TEXT_CHARS - 10)
    para_b = "Б" * 50
    text = para_a + "\n\n" + para_b
    parts = split_text_for_telegram(text)
    assert len(parts) == 2
    assert parts[0] == para_a
    assert parts[1] == para_b


def test_split_text_each_part_within_limit() -> None:
    # Случайный длинный текст — гарантируем, что ни один кусок не превышает лимит.
    long = "\n\n".join(["Слово " * 300 for _ in range(5)])
    parts = split_text_for_telegram(long)
    assert all(len(p) <= TELEGRAM_MAX_TEXT_CHARS for p in parts)
    # Контент не теряется: суммарная длина совпадает с исходной (с поправкой на trim).
    assert sum(len(p) for p in parts) <= len(long)
    assert len(parts) > 1


def test_register_bot_commands_noop_without_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_VERTICAL_ID", raising=False)
    from mandala.adapters.telegram.bot_commands import register_bot_commands_if_configured

    assert asyncio.run(register_bot_commands_if_configured()) is False


def test_burger_menu_contains_profile_reset_help() -> None:
    # Профиль/рестарт/help — в бургер-меню (setMyCommands), не в основном потоке кнопок.
    from mandala.adapters.telegram.bot_commands import BOT_COMMANDS

    names = [cmd for cmd, _ in BOT_COMMANDS]
    assert "profile" in names
    assert "reset" in names
    assert "help" in names


def test_build_profile_message_renders_fields_and_keyboard() -> None:
    from mandala.services.profile_view import build_profile_message

    msg = build_profile_message(
        "astrology",
        {"full_name": "Аня", "birth_date": "1990-01-01", "astro_system": "western"},
    )
    assert msg.text is not None
    assert "Ваш профиль" in msg.text
    assert "Аня" in msg.text
    assert "1990-01-01" in msg.text
    # Профиль показывается с постоянной клавиатурой навигации по контенту.
    assert msg.reply_keyboard == ASTROLOGY_REPLY_KEYBOARD
    # Сброс — через команду меню, а не кнопку основного потока.
    assert "/reset" in msg.text
