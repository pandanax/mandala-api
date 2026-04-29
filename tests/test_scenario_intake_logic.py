"""Логика анкеты и валидаторов без БД (тикет 13)."""

from __future__ import annotations

from mandala.verticals.intake_config import intake_steps_for_vertical


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
