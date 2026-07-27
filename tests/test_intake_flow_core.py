"""Чистое ядро анкеты с пофазовым подтверждением — офлайн, без БД и без сети.

Прогоняем :func:`mandala.services.intake_flow.step_intake` как конечный автомат: патчи
состояния применяем shallow-merge (как JSONB ``||`` в БД), резолвер места инъектируем
(город/пояс — фейк, сеть не трогаем). Покрываем:

* подтверждение каждого поля (Верно/Исправить);
* валидацию каждого поля + ОСОБУЮ валидацию места (не резолвится → повторный запрос,
  БЕЗ продвижения и без сохранения);
* подтверждение всей анкеты перед сохранением (финал → agent_card_patch + finalize);
* каждое интерактивное сообщение несёт непустой inline-keyboard.
"""

from __future__ import annotations

from typing import Any

from mandala.services.intake_flow import (
    CB_CONFIRM,
    CB_EDIT,
    CB_FIELD_PREFIX,
    CB_REDO,
    CB_REDO_ALL,
    CB_SAVE,
    KEY_INTAKE_COMPLETE,
    KEY_INTAKE_PHASE,
    PHASE_FIELD_CONFIRM,
    PHASE_FIELD_PICK,
    PHASE_FORM_CONFIRM,
    PHASE_INPUT,
    IntakeOutcome,
    PlaceResolution,
    PlaceResolveError,
    step_intake,
)
from mandala.verticals.intake_config import IntakeStep, intake_steps_for_vertical

_STEPS = intake_steps_for_vertical("astrology")
assert _STEPS is not None
STEPS: tuple[IntakeStep, ...] = tuple(_STEPS)


def _ok_resolver(city: str) -> PlaceResolution:
    return PlaceResolution(lat=55.75, lng=37.61, tz="Europe/Moscow", resolved_name=city)


def _fail_resolver(city: str) -> PlaceResolution:
    raise PlaceResolveError(f"⚠️ Не удалось найти город «{city}». Уточните ближайший крупный город.")


def _codes(msg: Any) -> list[str]:
    return [c.get("callback_data", "") for row in (msg.buttons or []) for c in row]


def _assert_all_have_buttons(messages: list[Any]) -> None:
    assert messages, "ожидались сообщения"
    for m in messages:
        assert m.buttons, f"сообщение без inline-кнопок: {m.text!r}"


class Runner:
    """Гоняет ядро, применяя state_patch к общему состоянию (как shallow JSONB merge)."""

    def __init__(self, resolver: Any = _ok_resolver) -> None:
        self.state: dict[str, Any] = {}
        self.agent_card: dict[str, Any] = {}
        self.resolver = resolver

    def send(self, text: str) -> IntakeOutcome:
        outcome = step_intake(
            steps=STEPS,
            state=dict(self.state),
            agent_card=dict(self.agent_card),
            user_text=text,
            resolve_place=self.resolver,
        )
        if outcome.state_patch:
            self.state.update(outcome.state_patch)
        if outcome.agent_card_patch:
            self.agent_card.update(outcome.agent_card_patch)
        return outcome


def test_field_echo_then_confirm_advances() -> None:
    r = Runner()
    # Ввод имени → эхо «Имя: … Верно?» с кнопками Верно/Исправить.
    out = r.send("Иван Иванов")
    _assert_all_have_buttons(out.messages)
    assert "Иван Иванов" in (out.messages[0].text or "")
    assert "Верно" in (out.messages[0].text or "")
    assert set(_codes(out.messages[0])) == {CB_CONFIRM, CB_REDO}
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FIELD_CONFIRM
    assert not r.state.get(KEY_INTAKE_COMPLETE)

    # «Верно» → фиксируем поле, спрашиваем дату (следующий шаг), кнопки есть.
    out2 = r.send(CB_CONFIRM)
    _assert_all_have_buttons(out2.messages)
    assert out2.committed_field == ("full_name", "Иван Иванов")
    assert r.state[KEY_INTAKE_PHASE] == PHASE_INPUT
    assert "дат" in (out2.messages[0].text or "").lower()


def test_redo_reasks_same_field_without_commit() -> None:
    r = Runner()
    r.send("Иван Иванов")
    out = r.send(CB_REDO)
    _assert_all_have_buttons(out.messages)
    assert r.state[KEY_INTAKE_PHASE] == PHASE_INPUT
    # committed_field отсутствует — поле не зафиксировано.
    assert out.committed_field is None
    assert (
        "имя" in (out.messages[0].text or "").lower()
        or "обращат" in (out.messages[0].text or "").lower()
    )


def test_invalid_value_reprompts_and_does_not_advance() -> None:
    r = Runner()
    out = r.send("я")  # слишком коротко для full_name (нужно 2 слова)
    _assert_all_have_buttons(out.messages)
    # Не перешли в подтверждение поля, состояние не двинулось.
    assert not out.state_patch
    assert r.state.get(KEY_INTAKE_PHASE) in (None, "")
    # Сообщение — понятная ошибка + повторный вопрос.
    assert "не могу принять" in (out.messages[0].text or "").lower()


def test_birth_place_not_resolved_reprompts_before_save() -> None:
    r = Runner(resolver=_fail_resolver)
    r.send("Иван Иванов")
    r.send(CB_CONFIRM)
    r.send("17.03.1992")
    r.send(CB_CONFIRM)
    # Теперь шаг места: город не резолвится → повторный запрос, БЕЗ подтверждения/сохранения.
    out = r.send("Атлантида")
    _assert_all_have_buttons(out.messages)
    assert "не удалось найти город" in (out.messages[0].text or "").lower()
    assert not out.state_patch  # не продвинулись
    assert r.state[KEY_INTAKE_PHASE] == PHASE_INPUT
    assert "birth_place" not in r.agent_card


def test_birth_place_resolved_shows_timezone_in_echo() -> None:
    r = Runner()
    r.send("Иван Иванов")
    r.send(CB_CONFIRM)
    r.send("17.03.1992")
    r.send(CB_CONFIRM)
    out = r.send("Москва")
    _assert_all_have_buttons(out.messages)
    text = out.messages[0].text or ""
    assert "Москва" in text
    assert "Europe/Moscow" in text  # часовой пояс определён и показан
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FIELD_CONFIRM


def _drive_to_form_confirm(r: Runner) -> IntakeOutcome:
    r.send("Иван Иванов")
    r.send(CB_CONFIRM)
    r.send("17.03.1992")
    r.send(CB_CONFIRM)
    r.send("Москва")
    r.send(CB_CONFIRM)
    r.send("14:30")
    return r.send(CB_CONFIRM)  # последний confirm → сводка всей анкеты


def test_whole_form_confirmation_before_save() -> None:
    r = Runner()
    out = _drive_to_form_confirm(r)
    _assert_all_have_buttons(out.messages)
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FORM_CONFIRM
    summary = out.messages[0].text or ""
    # Сводка содержит все поля.
    for token in ("Иван Иванов", "17.03.1992", "Москва", "14:30"):
        assert token in summary
    assert set(_codes(out.messages[0])) == {CB_SAVE, CB_EDIT}
    # Профиль ещё НЕ сохранён: полей в agent_card нет, complete не выставлен.
    assert "full_name" not in r.agent_card
    assert not r.state.get(KEY_INTAKE_COMPLETE)


def test_save_finalizes_with_all_fields() -> None:
    r = Runner()
    _drive_to_form_confirm(r)
    out = r.send(CB_SAVE)
    assert out.finalize is True
    assert out.agent_card_patch == {
        "full_name": "Иван Иванов",
        "birth_date": "17.03.1992",
        "birth_place": "Москва",
        "birth_time": "14:30",
    }
    assert out.state_patch[KEY_INTAKE_COMPLETE] is True
    assert out.state_patch[KEY_INTAKE_PHASE] == ""


def test_edit_from_summary_picks_single_field_and_returns_to_summary() -> None:
    r = Runner()
    _drive_to_form_confirm(r)
    # «Изменить» → выбор поля.
    out_pick = r.send(CB_EDIT)
    _assert_all_have_buttons(out_pick.messages)
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FIELD_PICK
    codes = _codes(out_pick.messages[0])
    assert f"{CB_FIELD_PREFIX}birth_time" in codes
    assert CB_REDO_ALL in codes

    # Выбираем «время рождения» → просят новое значение.
    out_field = r.send(f"{CB_FIELD_PREFIX}birth_time")
    _assert_all_have_buttons(out_field.messages)
    assert r.state[KEY_INTAKE_PHASE] == PHASE_INPUT

    # Вводим новое время → эхо-подтверждение.
    r.send("09:15")
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FIELD_CONFIRM
    # «Верно» → возвращаемся к сводке (не идём по шагам дальше).
    out_summary = r.send(CB_CONFIRM)
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FORM_CONFIRM
    assert "09:15" in (out_summary.messages[0].text or "")


def test_typed_value_during_confirm_is_treated_as_correction() -> None:
    r = Runner()
    r.send("Иван Иванов")
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FIELD_CONFIRM
    # Пользователь вместо клика ввёл другое имя — трактуем как исправление значения.
    out = r.send("Пётр Петров")
    assert r.state[KEY_INTAKE_PHASE] == PHASE_FIELD_CONFIRM
    assert "Пётр Петров" in (out.messages[0].text or "")
