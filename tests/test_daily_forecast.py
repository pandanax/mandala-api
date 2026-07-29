"""Утренняя рассылка — чистая логика, контент и настройка ``/morning`` (офлайн)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from mandala.repositories.profiles import ClientProfileDTO
from mandala.services import daily_forecast as df
from mandala.services import daily_forecast_settings as dfs
from mandala.verticals.client_knowledge import (
    AGENT_CARD_DAILY_FORECAST_ENABLED,
    AGENT_CARD_DAILY_FORECAST_LAST_SENT,
    AGENT_CARD_DAILY_FORECAST_TIME,
    AGENT_CARD_NATAL_CHART_DATA,
)


def _msk(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=df.MSK)


# --- parse_hhmm / settings accessors ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("08:30", (8, 30)),
        ("00:00", (0, 0)),
        ("23:59", (23, 59)),
        ("7:5", (7, 5)),
        ("24:00", None),
        ("10:60", None),
        ("abc", None),
        ("10", None),
        ("", None),
    ],
)
def test_parse_hhmm(raw: str, expected: tuple[int, int] | None) -> None:
    assert df.parse_hhmm(raw) == expected


def test_enabled_missing_key_defaults_true() -> None:
    assert df.is_daily_forecast_enabled({}) is True
    assert df.is_daily_forecast_enabled({AGENT_CARD_DAILY_FORECAST_ENABLED: False}) is False
    assert df.is_daily_forecast_enabled({AGENT_CARD_DAILY_FORECAST_ENABLED: True}) is True


def test_time_default_and_normalization() -> None:
    assert df.daily_forecast_time({}) == "10:00"
    assert df.daily_forecast_time({AGENT_CARD_DAILY_FORECAST_TIME: "7:5"}) == "07:05"
    # Мусор → дефолт.
    assert df.daily_forecast_time({AGENT_CARD_DAILY_FORECAST_TIME: "nope"}) == "10:00"


# --- should_send_daily_forecast (все ветки) ----------------------------------------


def test_should_send_disabled() -> None:
    ac = {AGENT_CARD_DAILY_FORECAST_ENABLED: False}
    assert df.should_send_daily_forecast(ac, _msk(2026, 7, 29, 10)) is False


def test_should_send_default_enabled_at_configured_time() -> None:
    # Нет ключей → включено, дефолт 10:00 → ровно в 10:00 шлём.
    assert df.should_send_daily_forecast({}, _msk(2026, 7, 29, 10, 0)) is True


def test_should_not_send_before_time() -> None:
    assert df.should_send_daily_forecast({}, _msk(2026, 7, 29, 9, 59)) is False


def test_should_not_send_already_sent_today() -> None:
    ac = {AGENT_CARD_DAILY_FORECAST_LAST_SENT: "2026-07-29"}
    assert df.should_send_daily_forecast(ac, _msk(2026, 7, 29, 10, 0)) is False


def test_should_send_new_day_after_yesterday_sent() -> None:
    ac = {AGENT_CARD_DAILY_FORECAST_LAST_SENT: "2026-07-28"}
    assert df.should_send_daily_forecast(ac, _msk(2026, 7, 29, 10, 0)) is True


def test_catchup_window_within_bound() -> None:
    # cfg 10:00, сейчас 12:59 → 179 мин ≤ 180 → шлём (догон после простоя).
    assert df.should_send_daily_forecast({}, _msk(2026, 7, 29, 12, 59)) is True


def test_catchup_window_exceeded() -> None:
    # cfg 10:00, сейчас 13:01 → 181 мин > 180 → НЕ шлём (не с большим опозданием).
    assert df.should_send_daily_forecast({}, _msk(2026, 7, 29, 13, 1)) is False


def test_custom_time_respected() -> None:
    ac = {AGENT_CARD_DAILY_FORECAST_TIME: "08:00"}
    assert df.should_send_daily_forecast(ac, _msk(2026, 7, 29, 7, 59)) is False
    assert df.should_send_daily_forecast(ac, _msk(2026, 7, 29, 8, 0)) is True


# --- контент (билдер девиза) --------------------------------------------------------


class _StubLLM:
    def __init__(self, reply: str = "Сегодня твой день! 🚀") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: Any, *, max_tokens: int | None = None, **kw: Any) -> str:
        self.calls.append({"messages": list(messages), "max_tokens": max_tokens})
        return self.reply


class _FailLLM:
    def complete(self, *a: Any, **kw: Any) -> str:
        raise RuntimeError("LLM down")


class _SeqLLM:
    """Отдаёт заранее заданную последовательность ответов (для проверки ретрая)."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0

    def complete(self, *a: Any, **kw: Any) -> str:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def test_build_slogan_general_when_no_birth_date() -> None:
    llm = _StubLLM()
    out = df.build_daily_slogan({}, llm_client=llm, now=_msk(2026, 7, 29, 10))
    assert out == "Сегодня твой день! 🚀"
    assert len(llm.calls) == 1
    # Малый бюджет токенов.
    assert llm.calls[0]["max_tokens"] == df.DAILY_SLOGAN_MAX_TOKENS


def test_build_slogan_llm_failure_returns_none() -> None:
    out = df.build_daily_slogan({}, llm_client=_FailLLM(), now=_msk(2026, 7, 29, 10))
    assert out is None


def test_build_slogan_empty_reply_returns_none() -> None:
    out = df.build_daily_slogan({}, llm_client=_StubLLM(reply="   "), now=_msk(2026, 7, 29, 10))
    assert out is None


def test_build_slogan_strips_service_suffixes() -> None:
    reply = 'Лови волну удачи! 🌊\n---mandala-nav---\n{"buttons":[]}'
    out = df.build_daily_slogan({}, llm_client=_StubLLM(reply=reply), now=_msk(2026, 7, 29, 10))
    assert out == "Лови волну удачи! 🌊"


@pytest.mark.parametrize(
    "reply",
    [
        # Прямое воспроизведение прод-бага «Се»: слабая модель приклеила служебный маркер
        # сразу после обрывка «Се», split_llm_nav_suffix взял head-до-маркера.
        'Се---mandala-nav---\n{"buttons":[{"label":"x","q":"y"}]}',
        'Се\n---mandala---\n{"natal_chart_text":"..."}',
        "Се",  # чистый обрывок без маркера
        "🌟",  # односимвольная пустышка
        "Вперёд",  # одно слово — не девиз
    ],
)
def test_build_slogan_never_sends_garbage(reply: str) -> None:
    """Регресс «Се»: неправдоподобный вывод (обрывок/пустышка) → ретрай, затем None."""
    llm = _SeqLLM([reply, reply])
    out = df.build_daily_slogan({}, llm_client=llm, now=_msk(2026, 7, 29, 10))
    assert out is None
    assert llm.calls == 2  # был ровно один ретрай


def test_build_slogan_retry_recovers_valid_after_garbage() -> None:
    """Первый ответ — обрывок «Се», ретрай отдаёт нормальный девиз → шлём его."""
    llm = _SeqLLM(['Се---mandala-nav---\n{"buttons":[]}', "Сегодня твой день, сияй ярко! 🌟"])
    out = df.build_daily_slogan({}, llm_client=llm, now=_msk(2026, 7, 29, 10))
    assert out == "Сегодня твой день, сияй ярко! 🌟"
    assert llm.calls == 2


def test_build_slogan_strips_raw_agent_card_without_allowed_key() -> None:
    """agent-card с неразрешённым ключом штатный сплиттер не режет — предохранитель режет."""
    reply = 'Сегодня твой день, лови удачу! ✨\n---mandala---\n{"foo":"bar"}'
    out = df.build_daily_slogan({}, llm_client=_StubLLM(reply=reply), now=_msk(2026, 7, 29, 10))
    assert out == "Сегодня твой день, лови удачу! ✨"
    assert "mandala" not in (out or "")


def test_is_plausible_slogan() -> None:
    assert df.is_plausible_slogan("Сегодня — твой день! 🌟")
    assert not df.is_plausible_slogan("Се")
    assert not df.is_plausible_slogan("Вперёд")  # одно слово
    assert not df.is_plausible_slogan("   ")
    assert not df.is_plausible_slogan("🌟")


def test_build_slogan_personalized_uses_sun_sign(monkeypatch: pytest.MonkeyPatch) -> None:
    # Транзиты мокаем — офлайн и детерминированно.
    monkeypatch.setattr(
        "mandala.astro.natal_chart.calculate_current_transits",
        lambda *a, **k: {"planets": {"Солнце": {"sign": "Лев"}}},
    )
    llm = _StubLLM()
    ac = {"birth_date": "17.03.1992", AGENT_CARD_NATAL_CHART_DATA: {"sun_sign": "Рыбы"}}
    out = df.build_daily_slogan(ac, llm_client=llm, now=_msk(2026, 7, 29, 10))
    assert out == "Сегодня твой день! 🚀"
    user_msg = llm.calls[0]["messages"][1].content
    assert "Рыбы" in user_msg


def test_build_forecast_message_has_short_nav_buttons() -> None:
    msg = df.build_daily_forecast_message("Вперёд! 🚀")
    assert msg.text == "Вперёд! 🚀"
    assert msg.buttons
    codes = [b["callback_data"] for row in msg.buttons for b in row]
    assert "mdl:fc_today" in codes
    assert "mdl:morning" in codes


# --- настройка /morning (детерминированно, без LLM) --------------------------------


class _FakeProfiles:
    def __init__(self, store: dict[UUID, dict[str, Any]]) -> None:
        self._store = store

    def get_by_user_id(self, uid: UUID) -> ClientProfileDTO | None:
        ac = self._store.get(uid)
        if ac is None:
            return None
        return ClientProfileDTO(
            user_id=uid,
            vertical_id="astrology",
            agent_card=dict(ac),
            scenario_state={},
            display_name=None,
        )

    def merge_agent_card(self, uid: UUID, patch: dict[str, Any]) -> None:
        self._store.setdefault(uid, {}).update(patch)


@pytest.fixture
def settings_store(monkeypatch: pytest.MonkeyPatch) -> dict[UUID, dict[str, Any]]:
    store: dict[UUID, dict[str, Any]] = {}
    monkeypatch.setattr(dfs, "ProfileRepository", lambda conn: _FakeProfiles(store))
    return store


def _act(store: dict[UUID, dict[str, Any]], uid: UUID, text: str) -> Any:
    out = dfs.handle_daily_forecast_action(store, user_id=uid, text=text)  # type: ignore[arg-type]
    assert len(out) == 1
    assert out[0].buttons, "у настройки должны быть кнопки"
    return out[0]


def test_is_daily_forecast_action() -> None:
    assert dfs.is_daily_forecast_action("/morning")
    assert dfs.is_daily_forecast_action("/morning 08:30")
    assert dfs.is_daily_forecast_action("mdl:morning")
    assert dfs.is_daily_forecast_action("mdl:morning:off")
    assert dfs.is_daily_forecast_action("mdl:morning:set:08:00")
    assert not dfs.is_daily_forecast_action("/natal")
    assert not dfs.is_daily_forecast_action("привет")
    assert not dfs.is_daily_forecast_action(None)


def test_morning_show_default_state(settings_store: dict[UUID, dict[str, Any]]) -> None:
    uid = uuid4()
    msg = _act(settings_store, uid, "/morning")
    # Дефолт: включено, 10:00 — состояние не изменилось (показ без побочек).
    assert "10:00" in (msg.text or "")
    assert "включена" in (msg.text or "").lower()
    assert settings_store.get(uid) in (None, {})


def test_morning_toggle_off_then_on(settings_store: dict[UUID, dict[str, Any]]) -> None:
    uid = uuid4()
    _act(settings_store, uid, "mdl:morning:off")
    assert settings_store[uid][AGENT_CARD_DAILY_FORECAST_ENABLED] is False
    msg = _act(settings_store, uid, "mdl:morning:on")
    assert settings_store[uid][AGENT_CARD_DAILY_FORECAST_ENABLED] is True
    assert "включ" in (msg.text or "").lower()


def test_morning_text_off(settings_store: dict[UUID, dict[str, Any]]) -> None:
    uid = uuid4()
    _act(settings_store, uid, "/morning off")
    assert settings_store[uid][AGENT_CARD_DAILY_FORECAST_ENABLED] is False


def test_morning_set_time_preset(settings_store: dict[UUID, dict[str, Any]]) -> None:
    uid = uuid4()
    msg = _act(settings_store, uid, "mdl:morning:set:08:00")
    assert settings_store[uid][AGENT_CARD_DAILY_FORECAST_TIME] == "08:00"
    # Установка времени включает рассылку.
    assert settings_store[uid][AGENT_CARD_DAILY_FORECAST_ENABLED] is True
    assert "08:00" in (msg.text or "")


def test_morning_set_time_text(settings_store: dict[UUID, dict[str, Any]]) -> None:
    uid = uuid4()
    _act(settings_store, uid, "/morning 8:5")
    assert settings_store[uid][AGENT_CARD_DAILY_FORECAST_TIME] == "08:05"


def test_morning_invalid_time_rejected(settings_store: dict[UUID, dict[str, Any]]) -> None:
    uid = uuid4()
    msg = _act(settings_store, uid, "/morning 25:00")
    assert uid not in settings_store or AGENT_CARD_DAILY_FORECAST_TIME not in settings_store[uid]
    assert "не понял" in (msg.text or "").lower()
