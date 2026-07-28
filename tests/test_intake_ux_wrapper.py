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


def test_profile_natal_button_routes_to_render(store: dict[str, Any]) -> None:
    # Жалоба капитана: в /profile должна быть РАБОЧАЯ отсылка на /natal.
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

        # 1) В профиле есть кнопка с callback "/natal".
        profile = _run(store, uid, "/profile")
        codes = [
            c.get("callback_data", "") for m in profile for row in (m.buttons or []) for c in row
        ]
        assert "/natal" in codes, "в профиле нет кнопки на натальную карту"

        # 2) Клик по ней (callback "/natal" приходит как event.text) → рендер карты.
        natal = _run(store, uid, "/natal")
    assert any(m.photo_bytes or m.photo for m in natal), "клик из профиля не открыл карту"
    assert "натальная карта" in (natal[-1].text or "").lower()


def test_natal_photo_caption_carries_time(store: dict[str, Any]) -> None:
    # Подпись к колесу зависит от даты, ВРЕМЕНИ и места — время должно быть в подписи.
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)  # время 14:30
        natal = _run(store, uid, "/natal")
    photo = next(m for m in natal if m.photo_bytes or m.photo)
    assert "14:30" in (photo.text or ""), f"нет времени в подписи: {photo.text!r}"


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


def test_start_on_completed_profile_shows_menu_not_reintake(store: dict[str, Any]) -> None:
    """Баг капитана: завершил профиль → жмёт /start → анкета открывалась заново.

    Теперь /start при завершённой анкете = меню «Анкета уже заполнена…», БЕЗ
    сброса scenario_state и БЕЗ повторного вопроса; наталка/матрица в agent_card целы.
    """
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

    ac_before = dict(store["profiles"][uid]["agent_card"])
    state_before = dict(store["profiles"][uid]["scenario_state"])
    assert isinstance(ac_before.get(AGENT_CARD_NATAL_CHART_DATA), dict)

    out = _run(store, uid, "/start")
    _assert_all_have_buttons(out)

    # Меню «уже заполнено», а не вопрос анкеты.
    assert any("анкета уже заполнена" in (m.text or "").lower() for m in out)
    prompts = " ".join((m.text or "") for m in out).lower()
    assert "как вас зовут" not in prompts and "имя" not in prompts

    # scenario_state НЕ сброшен — анкета остаётся завершённой.
    state_after = store["profiles"][uid]["scenario_state"]
    assert state_after.get(KEY_INTAKE_COMPLETE) is True
    assert state_after == state_before
    # agent_card (наталка/матрица) не тронут.
    assert store["profiles"][uid]["agent_card"] == ac_before


def test_restart_on_completed_profile_shows_menu(store: dict[str, Any]) -> None:
    """/restart ведёт себя как /start: завершённый профиль → меню, без переоткрытия."""
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)

    out = _run(store, uid, "/restart")
    _assert_all_have_buttons(out)
    assert any("анкета уже заполнена" in (m.text or "").lower() for m in out)
    assert store["profiles"][uid]["scenario_state"].get(KEY_INTAKE_COMPLETE) is True


def test_start_on_new_user_begins_intake(store: dict[str, Any]) -> None:
    """Новый/незавершённый пользователь: /start запускает анкету с первого вопроса."""
    uid = _seed_user(store)
    out = _run(store, uid, "/start")
    _assert_all_have_buttons(out)
    # Есть вопрос анкеты (первый шаг), «уже заполнена» — нет.
    text = " ".join((m.text or "") for m in out).lower()
    assert "анкета уже заполнена" not in text
    assert len(out) >= 2, "ожидались приветствие + первый вопрос анкеты"
    assert store["profiles"][uid]["scenario_state"].get(KEY_INTAKE_COMPLETE) is not True


def test_reset_is_the_only_full_wipe(store: dict[str, Any]) -> None:
    """/reset — единственный полный сброс: анкета обнулена, сообщения удалены."""
    uid = _seed_user(store)
    with patch(_GEO, return_value=_MOSCOW):
        _drive_full_intake(store, uid)
    store["messages"] = [{"x": 1}, {"x": 2}]

    out = _run(store, uid, "/reset")
    _assert_all_have_buttons(out)
    # Полный сброс: анкета не завершена, наталка стёрта, сообщения удалены.
    row = store["profiles"][uid]
    assert row["scenario_state"].get(KEY_INTAKE_COMPLETE) is not True
    assert AGENT_CARD_NATAL_CHART_DATA not in row["agent_card"]
    assert store["messages"] == []
    # Снова задан первый вопрос анкеты.
    assert len(out) >= 2
