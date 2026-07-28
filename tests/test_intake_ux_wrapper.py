"""Полный флоу анкеты через обёртку ``handle_intake_before_llm`` — офлайн.

БД заменяем in-memory фейками (общий ``store`` вместо ``conn``), геокодер мокаем,
Матрицу и карту считаем ПО-НАСТОЯЩЕМУ (kerykeion online=False + чистая нумерология).
Покрываем сквозные требования капитана:

* авто-расчёт и сохранение натальной карты и Матрицы Судьбы при сохранении профиля;
* ``/natal`` и ``/matrix`` — мгновенный рендер из БД (без LLM);
* редактирование профиля (тот же флоу, пере-сохранение с пересчётом);
* особая валидация места (город не найден → повторный запрос, БЕЗ сохранения);
* промо → инлайн-навигация;
* КАЖДОЕ сообщение бота несёт непустой inline-keyboard (включая ошибки);
* завершённого пользователя обычный текст не «ловит» — уходит к LLM (None).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.repositories.profiles import ClientProfileDTO
from mandala.services import scenario_intake
from mandala.services.intake_flow import (
    CB_CONFIRM,
    CB_FIELD_PREFIX,
    CB_PROFILE_EDIT,
    CB_SAVE,
    KEY_INTAKE_COMPLETE,
)
from mandala.verticals.client_knowledge import (
    AGENT_CARD_DESTINY_MATRIX_DATA,
    AGENT_CARD_NATAL_CHART_DATA,
)

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


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    s: dict[str, Any] = {"profiles": {}, "messages": []}
    monkeypatch.setattr(scenario_intake, "ProfileRepository", lambda conn: _FakeProfiles(conn))
    monkeypatch.setattr(scenario_intake, "MessageRepository", lambda conn: _FakeMessages(conn))
    return s


def _assert_all_have_buttons(messages: list[OutboundMessage]) -> None:
    assert messages, "ожидались сообщения"
    for m in messages:
        if m.invoice is not None:
            continue
        assert m.buttons, f"сообщение без inline-кнопок: {m.text!r}"


def _run(store: dict[str, Any], uid: UUID, text: str) -> list[OutboundMessage]:
    """Один ход через обёртку: свежий снимок профиля из фейкового store."""
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


def _drive_full_intake(store: dict[str, Any], uid: UUID) -> list[list[OutboundMessage]]:
    seq = [
        "Иван Иванов",
        CB_CONFIRM,
        "17.03.1992",
        CB_CONFIRM,
        "Москва",
        CB_CONFIRM,
        "14:30",
        CB_CONFIRM,  # → сводка
        CB_SAVE,  # → сохранение + расчёт
    ]
    outs = []
    for text in seq:
        out = _run(store, uid, text)
        _assert_all_have_buttons(out)
        outs.append(out)
    return outs


def test_full_intake_saves_profile_and_computes_chart_and_matrix(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        outs = _drive_full_intake(store, uid)

    row = store["profiles"][uid]
    ac = row["agent_card"]
    assert ac["full_name"] == "Иван Иванов"
    assert ac["birth_date"] == "17.03.1992"
    assert ac["birth_place"] == "Москва"
    assert ac["birth_time"] == "14:30"
    # Карта (Swiss Ephemeris) и Матрица Судьбы посчитаны математикой и сохранены в БД.
    assert isinstance(ac.get(AGENT_CARD_NATAL_CHART_DATA), dict)
    assert ac[AGENT_CARD_NATAL_CHART_DATA].get("sun_sign")
    assert isinstance(ac.get(AGENT_CARD_DESTINY_MATRIX_DATA), dict)
    assert ac[AGENT_CARD_DESTINY_MATRIX_DATA].get("comfort_zone")
    assert row["scenario_state"].get(KEY_INTAKE_COMPLETE) is True
    # Финальное сообщение — успех + инлайн-навигация.
    final = outs[-1]
    assert any("сохран" in (m.text or "").lower() for m in final)


def test_instant_natal_and_matrix_render_from_db(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

    # Мгновенный рендер БЕЗ сети (геокодер НЕ вызывается — координаты уже в БД):
    # колесо-картинка (фото) + блочный текст с навигацией на терминальном сообщении.
    natal = _run(store, uid, "/natal")
    assert any(m.photo_bytes or m.photo for m in natal), "ожидалось колесо-фото"
    text_msg = natal[-1]
    assert text_msg.buttons, "у терминального текста должна быть навигация"
    assert "натальная карта" in (text_msg.text or "").lower()

    matrix = _run(store, uid, "/matrix")
    _assert_all_have_buttons(matrix)
    assert "карта судьбы" in (matrix[0].text or "").lower()


def test_profile_edit_reflow_and_recompute(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)
        old_calc = store["profiles"][uid]["agent_card"][AGENT_CARD_NATAL_CHART_DATA][
            "calculated_at"
        ]

        # Входим в правку из профиля.
        pick = _run(store, uid, CB_PROFILE_EDIT)
        _assert_all_have_buttons(pick)

        # Меняем место рождения.
        _run(store, uid, f"{CB_FIELD_PREFIX}birth_place")
        with patch(_GEO, return_value=(59.94, 30.31, "Europe/Moscow")):
            _run(store, uid, "Санкт-Петербург")
            summary = _run(store, uid, CB_CONFIRM)  # return_to_summary → сводка
            _assert_all_have_buttons(summary)
            saved = _run(store, uid, CB_SAVE)
    _assert_all_have_buttons(saved)

    ac = store["profiles"][uid]["agent_card"]
    assert ac["birth_place"] == "Санкт-Петербург"
    # Карта пересчитана (новое время расчёта) и профиль остаётся завершённым.
    assert ac[AGENT_CARD_NATAL_CHART_DATA]["calculated_at"] != old_calc
    assert store["profiles"][uid]["scenario_state"].get(KEY_INTAKE_COMPLETE) is True
    assert any("обновл" in (m.text or "").lower() for m in saved)


def test_birth_place_not_found_reprompts_before_save(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    _run(store, uid, "Иван Иванов")
    _run(store, uid, CB_CONFIRM)
    _run(store, uid, "17.03.1992")
    _run(store, uid, CB_CONFIRM)
    with patch(_GEO, side_effect=ValueError("City not found: 'Атлантида'")):
        out = _run(store, uid, "Атлантида")
    _assert_all_have_buttons(out)
    assert "не удалось найти город" in (out[0].text or "").lower()
    # Место НЕ сохранено, профиль не завершён.
    assert "birth_place" not in store["profiles"][uid]["agent_card"]
    assert store["profiles"][uid]["scenario_state"].get(KEY_INTAKE_COMPLETE) is not True


def test_promo_returns_inline_nav(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch("mandala.services.promo.activate_promo", return_value=True):
        out = _run(store, uid, "/promo FREEBIE")
    _assert_all_have_buttons(out)
    assert "активирован" in (out[0].text or "").lower()


def test_completed_user_plain_text_passes_through_to_llm(store: dict[str, Any]) -> None:
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)
    # Завершённый профиль + обычный текст → обёртка не перехватывает (None → к LLM).
    profile = _FakeProfiles(store).get_by_user_id(uid)
    event = InboundEvent(
        vertical_id="astrology", channel="web", external_user_id=str(uid), text="расскажи про луну"
    )
    assert scenario_intake.handle_intake_before_llm(store, event, uid, profile) is None  # type: ignore[arg-type]


def test_legacy_completed_user_can_start_edit(store: dict[str, Any]) -> None:
    """Существующий (v1) завершённый пользователь: обычный текст → None; правка работает."""
    uid = uuid4()
    store["profiles"][uid] = {
        "agent_card": {
            "full_name": "Пётр Петров",
            "birth_date": "01.01.1990",
            "birth_place": "Казань",
            "birth_time": "10:00",
        },
        "scenario_state": {"intake_complete": True},
        "vertical_id": "astrology",
    }
    # Обычный текст уходит к LLM.
    profile = _FakeProfiles(store).get_by_user_id(uid)
    ev = InboundEvent(
        vertical_id="astrology", channel="web", external_user_id=str(uid), text="привет"
    )
    assert scenario_intake.handle_intake_before_llm(store, ev, uid, profile) is None  # type: ignore[arg-type]
    # Кнопка «Редактировать» запускает флоу правки.
    out = _run(store, uid, CB_PROFILE_EDIT)
    _assert_all_have_buttons(out)
    codes = [c.get("callback_data", "") for m in out for row in (m.buttons or []) for c in row]
    assert f"{CB_FIELD_PREFIX}full_name" in codes
