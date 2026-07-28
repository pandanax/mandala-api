"""Интеграция нумерологии в анкету/команды — офлайн (по образцу test_intake_ux_wrapper).

Самодостаточные in-memory фейки БД (общий ``store`` вместо ``conn``), геокодер мокаем.
Нумерологию, карту и Матрицу считаем ПО-НАСТОЯЩЕМУ (kerykeion online=False + чистая
арифметика). Проверяем:

* при сохранении профиля ``numerology_data`` считается из ИМЕНИ+ДАТЫ и пишется в agent_card;
* ``/numerology`` — мгновенный рендер из БД (без LLM), с инлайн-навигацией;
* без имени команда деградирует (числа даты есть, числа имени — нет), не падает;
* в ``/profile`` есть кнопка «🔢 Нумерология» (/numerology).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.repositories.profiles import ClientProfileDTO
from mandala.services import scenario_intake
from mandala.services.intake_flow import CB_CONFIRM, CB_SAVE
from mandala.verticals.client_knowledge import AGENT_CARD_NUMEROLOGY_DATA

_GEO = "mandala.astro.natal_chart._geocode_city"
_MOSCOW = (55.75, 37.61, "Europe/Moscow")


class _FakeProfiles:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def _row(self, uid: UUID) -> dict[str, Any]:
        row: dict[str, Any] = self._store["profiles"].setdefault(
            uid, {"agent_card": {}, "scenario_state": {}, "vertical_id": "astrology"}
        )
        return row

    def get_by_user_id(self, uid: UUID) -> ClientProfileDTO | None:
        row = self._store["profiles"].get(uid)
        if row is None:
            return None
        return ClientProfileDTO(
            user_id=uid,
            vertical_id=row["vertical_id"],
            agent_card=dict(row["agent_card"]),
            scenario_state=dict(row["scenario_state"]),
            display_name=None,
        )

    def ensure_row(self, *, user_id: UUID, vertical_id: str) -> None:
        self._row(user_id)["vertical_id"] = vertical_id

    def merge_agent_card(self, uid: UUID, patch: dict[str, Any]) -> None:
        self._row(uid)["agent_card"].update(patch)

    def merge_scenario_state(self, uid: UUID, patch: dict[str, Any]) -> None:
        self._row(uid)["scenario_state"].update(patch)

    def reset_session(self, uid: UUID) -> None:
        row = self._row(uid)
        row["agent_card"] = {}
        row["scenario_state"] = {}


class _FakeMessages:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def insert(self, **kw: Any) -> UUID:
        self._store.setdefault("messages", []).append(kw)
        return uuid4()

    def delete_for_user_vertical(self, *, user_id: UUID, vertical_id: str) -> int:
        n = len(self._store.get("messages", []))
        self._store["messages"] = []
        return n


class _FakeWallet:
    """Кошелёк-заглушка: /profile читает баланс, БД в офлайн-тестах нет."""

    def __init__(self, _conn: Any) -> None:
        pass

    def get_balance(self, *, user_id: UUID, vertical_id: str) -> int | None:
        return 20


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    s: dict[str, Any] = {"profiles": {}, "messages": []}
    monkeypatch.setattr(scenario_intake, "ProfileRepository", lambda conn: _FakeProfiles(conn))
    monkeypatch.setattr(scenario_intake, "MessageRepository", lambda conn: _FakeMessages(conn))
    monkeypatch.setattr(scenario_intake, "WalletRepository", _FakeWallet)
    return s


def _assert_all_have_buttons(messages: list[OutboundMessage]) -> None:
    assert messages, "ожидались сообщения"
    for m in messages:
        if m.invoice is not None:
            continue
        assert m.buttons, f"сообщение без inline-кнопок: {m.text!r}"


def _run(store: dict[str, Any], uid: UUID, text: str) -> list[OutboundMessage]:
    profile = _FakeProfiles(store).get_by_user_id(uid) or ClientProfileDTO(
        user_id=uid, vertical_id="astrology", agent_card={}, scenario_state={}, display_name=None
    )
    event = InboundEvent(
        vertical_id="astrology", channel="web", external_user_id=str(uid), text=text
    )
    out = scenario_intake.handle_intake_before_llm(store, event, uid, profile)  # type: ignore[arg-type]
    return out if out is not None else []


def _seed_user(store: dict[str, Any]) -> UUID:
    uid = uuid4()
    _FakeProfiles(store).ensure_row(user_id=uid, vertical_id="astrology")
    return uid


def _drive_full_intake(store: dict[str, Any], uid: UUID) -> None:
    for text in (
        "Иван Иванов",
        CB_CONFIRM,
        "17.03.1992",
        CB_CONFIRM,
        "Москва",
        CB_CONFIRM,
        "14:30",
        CB_CONFIRM,
        CB_SAVE,
    ):
        _assert_all_have_buttons(_run(store, uid, text))


def test_full_intake_computes_and_saves_numerology(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

    data = store["profiles"][uid]["agent_card"].get(AGENT_CARD_NUMEROLOGY_DATA)
    assert isinstance(data, dict)
    assert data["has_name"] is True
    assert data["full_name"] == "Иван Иванов"
    # Числа посчитаны из ИМЕНИ + даты (см. регресс-тест движка).
    assert data["numbers"]["life_path"] == 5
    assert data["numbers"]["soul_urge"] == 11


def test_instant_numerology_renders_from_db(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

    out = _run(store, uid, "/numerology")
    _assert_all_have_buttons(out)
    assert "нумерология" in (out[0].text or "").lower()
    # Мастер-число подписано, есть кнопка углублённого разбора (LLM).
    assert "мастер-число" in (out[0].text or "").lower()
    codes = [c.get("callback_data", "") for m in out for row in (m.buttons or []) for c in row]
    assert "mdl:numerology" in codes


def test_numerology_without_name_degrades(store: dict[str, Any]) -> None:
    """Старый профиль без имени: /numerology считает от даты, не падает."""
    uid = uuid4()
    store["profiles"][uid] = {
        "agent_card": {"birth_date": "17.03.1992"},
        "scenario_state": {"intake_complete": True},
        "vertical_id": "astrology",
    }
    out = _run(store, uid, "/numerology")
    _assert_all_have_buttons(out)
    text = (out[0].text or "").lower()
    assert "жизненн" in text  # число от даты есть
    # Числа имени не рассчитаны — есть подсказка добавить имя, но без падения.
    data = store["profiles"][uid]["agent_card"].get(AGENT_CARD_NUMEROLOGY_DATA)
    assert isinstance(data, dict) and data["has_name"] is False


def test_profile_shows_numerology_block_and_button(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

    profile = _run(store, uid, "/profile")
    # Профиль = только данные анкеты; доступ к нумерологии — через кнопку, не в теле.
    codes = [c.get("callback_data", "") for m in profile for row in (m.buttons or []) for c in row]
    assert "/numerology" in codes
