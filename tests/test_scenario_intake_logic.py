"""Логика анкеты и валидаторов без БД (тикет 13)."""

from __future__ import annotations

from mandala.services.scenario_intake import _extract_command, _vertical_greeting
from mandala.verticals.intake_config import intake_steps_for_vertical
from mandala.verticals.intake_validators import validator_from_spec


def test_two_verticals_different_chains() -> None:
    a = intake_steps_for_vertical("astrology")
    t = intake_steps_for_vertical("therapy")
    assert a is not None and t is not None
    keys_a = [s.field_key for s in a]
    keys_t = [s.field_key for s in t]
    assert keys_a != keys_t
    # Разный порядок смысловых полей относительно друг друга
    assert keys_a[0] != keys_t[0]


def test_unknown_vertical_no_intake() -> None:
    assert intake_steps_for_vertical("unknown_vertical_xyz") is None


def test_extract_command_recognises_known_slash_commands() -> None:
    assert _extract_command("/start") == "/start"
    assert _extract_command("/RESTART") == "/restart"
    assert _extract_command("/reset") == "/reset"
    assert _extract_command("/help some text") == "/help"
    # Telegram-style /cmd@botname
    assert _extract_command("/start@MandalaBot") == "/start"
    assert _extract_command("/reset@MandalaBot") == "/reset"


def test_extract_command_ignores_non_commands() -> None:
    assert _extract_command("") is None
    assert _extract_command("Москва") is None
    assert _extract_command("/unknown") is None
    assert _extract_command("привет /start") is None


def test_vertical_greeting_has_bot_description() -> None:
    g_astro = _vertical_greeting("astrology")
    g_therapy = _vertical_greeting("therapy")
    g_default = _vertical_greeting("unknown_x")
    # Приветствие в каждом случае содержит подсказку команд, включая /reset.
    for g in (g_astro, g_therapy, g_default):
        assert g.strip()
        assert "/start" in g
        assert "/reset" in g
        assert "/help" in g
    assert g_astro != g_therapy


def test_validate_full_name_requires_two_words() -> None:
    v = validator_from_spec({"kind": "full_name"})
    assert v("Иван Иванов") is None
    assert v("Иван Иванов Иванович") is None
    assert v("Иван") is not None
    assert v("") is not None
    assert v("/start") is not None


def test_validate_birth_date_accepts_dd_mm_yyyy() -> None:
    v = validator_from_spec({"kind": "birth_date"})
    assert v("17.03.1992") is None
    assert v("1.1.2000") is None
    assert v("17-03-1992") is None
    assert v("17/03/1992") is None


def test_validate_birth_date_rejects_garbage() -> None:
    v = validator_from_spec({"kind": "birth_date"})
    assert v("вчера") is not None
    assert v("32.01.1990") is not None  # день > 31
    assert v("01.13.1990") is not None  # месяц > 12
    assert v("01.01.1800") is not None  # год вне диапазона
    assert v("") is not None
