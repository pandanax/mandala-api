"""Мультитенантный маппинг токенов Telegram → вертикаль + маршрутизация polling."""

from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.engine import Engine

from mandala.adapters.telegram import polling
from mandala.adapters.telegram.bot_token import (
    get_bot_token_for_vertical,
    load_bot_token_map,
)


def _clear_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_VERTICAL_ID",
        "TELEGRAM_BOT_TOKENS",
        "TELEGRAM_BOT_TOKEN_ASTROLOGY",
        "TELEGRAM_BOT_TOKEN_THERAPY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_legacy_single_vertical(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111:aaa")
    monkeypatch.setenv("TELEGRAM_VERTICAL_ID", "astrology")

    assert load_bot_token_map() == {"astrology": "111:aaa"}
    assert get_bot_token_for_vertical("astrology") == "111:aaa"


def test_unknown_vertical_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111:aaa")
    monkeypatch.setenv("TELEGRAM_VERTICAL_ID", "astrology")

    # Неизвестная вертикаль не роняет — просто None.
    assert get_bot_token_for_vertical("therapy") is None


def test_json_map_multiple_verticals(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_token_env(monkeypatch)
    monkeypatch.setenv(
        "TELEGRAM_BOT_TOKENS",
        '{"astrology": "111:aaa", "therapy": "222:bbb"}',
    )

    assert load_bot_token_map() == {"astrology": "111:aaa", "therapy": "222:bbb"}
    assert get_bot_token_for_vertical("astrology") == "111:aaa"
    assert get_bot_token_for_vertical("therapy") == "222:bbb"


def test_per_vertical_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_ASTROLOGY", "111:aaa")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_THERAPY", "222:bbb")

    mapping = load_bot_token_map()
    assert mapping == {"astrology": "111:aaa", "therapy": "222:bbb"}


def test_precedence_per_vertical_over_json_over_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_token_env(monkeypatch)
    # legacy (низший)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "legacy:tok")
    monkeypatch.setenv("TELEGRAM_VERTICAL_ID", "astrology")
    # JSON (средний) переопределяет astrology, добавляет therapy
    monkeypatch.setenv(
        "TELEGRAM_BOT_TOKENS",
        '{"astrology": "json:tok", "therapy": "222:bbb"}',
    )
    # per-vertical (высший) переопределяет astrology
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN_ASTROLOGY", "env:tok")

    mapping = load_bot_token_map()
    assert mapping["astrology"] == "env:tok"
    assert mapping["therapy"] == "222:bbb"


def test_invalid_json_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_token_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKENS", "not-json{")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111:aaa")
    monkeypatch.setenv("TELEGRAM_VERTICAL_ID", "astrology")

    # Битый JSON не роняет — остаётся legacy-маппинг.
    assert load_bot_token_map() == {"astrology": "111:aaa"}


def test_empty_map_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_token_env(monkeypatch)
    with pytest.raises(RuntimeError):
        polling.run_polling_multi(token_map={})


def test_run_polling_multi_routes_each_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый (vertical, token) уходит в свой ``run_polling_forever``."""
    calls: list[tuple[str, str]] = []

    def _fake_forever(*, bot_token: str, vertical_id: str, engine: object) -> None:
        calls.append((vertical_id, bot_token))

    monkeypatch.setattr(polling, "run_polling_forever", _fake_forever)

    polling.run_polling_multi(
        token_map={"astrology": "111:aaa", "therapy": "222:bbb"},
        engine=cast(Engine, object()),
    )

    assert sorted(calls) == [("astrology", "111:aaa"), ("therapy", "222:bbb")]


def test_run_polling_multi_single_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def _fake_forever(*, bot_token: str, vertical_id: str, engine: object) -> None:
        calls.append((vertical_id, bot_token))

    monkeypatch.setattr(polling, "run_polling_forever", _fake_forever)

    polling.run_polling_multi(token_map={"astrology": "111:aaa"}, engine=cast(Engine, object()))

    assert calls == [("astrology", "111:aaa")]
