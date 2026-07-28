"""Чистое ядро анкеты с пофазовым подтверждением (без БД и без сети).

Требования UX-апгрейда (см. заявку капитана):

1. **Подтверждение каждого поля.** Пользователь вводит значение → бот эхом
   «Имя: X. Верно?» с инлайн-кнопками [Верно ✅] / [Исправить ✏️]. «Верно» —
   фиксируем поле в ЧЕРНОВИКЕ и идём к следующему; «Исправить» — просим ввести заново.
2. **Валидация каждого поля** с понятным сообщением и повторным запросом. Для места
   рождения валидация ОСОБАЯ: значение не принимается, пока город не резолвится
   (геокодер находит + определяется часовой пояс) — это делает инжектируемый
   ``resolve_place`` (сеть живёт в обёртке, ядро остаётся чистым и офлайн-тестируемым).
3. **Подтверждение всей анкеты перед сохранением.** Когда все поля собраны — сводка
   + [Подтвердить и сохранить ✅] / [Изменить ✏️]. Профиль пишется в БД (обёрткой)
   ТОЛЬКО после подтверждения; до этого поля живут в ``scenario_state`` (draft).
4. **Редактирование** (``/profile`` → «Редактировать» или «Изменить» из сводки) —
   тот же флоу: выбор поля (или всё заново), подтверждение поля, сводка, пере-сохранение.

Ядро — ЧИСТАЯ функция :func:`step_intake`: принимает текущий ``scenario_state`` и
``agent_card`` (снимки) + ввод пользователя, возвращает :class:`IntakeOutcome`
(сообщения + патчи состояния/профиля + флаг финализации). Обёртка
``mandala.services.scenario_intake`` применяет патчи, пишет в БД, считает натальную
карту и Матрицу Судьбы и гарантирует инлайн-навигацию на каждом сообщении.

Инвариант навигации (сквозное требование): каждое сообщение анкеты несёт непустой
``buttons`` — интерактивные шаги (ввод/подтверждение/сводка/выбор поля) проставляют
кнопки здесь; чисто информационные (отмена/устаревшая кнопка) добираются
фолбэком в обёртке.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from mandala.domain.contracts import OutboundMessage
from mandala.verticals.intake_config import IntakeStep

# --- Версия схемы состояния анкеты (миграция v1 → v2) ------------------------------
# v1: только ``intake_step_index`` + ``intake_complete`` (без черновика/подтверждений).
# v2: добавлены фазы, черновик, режим правки. Существующие пользователи не ломаются:
# завершённые (complete=True, без фазы) идут прямым путём к LLM; незавершённые
# мигрируют — черновик засевается из уже накопленного ``agent_card``.
INTAKE_SCHEMA_VERSION = 2

# --- Ключи в ``scenario_state`` ----------------------------------------------------
KEY_INTAKE_STEP_INDEX = "intake_step_index"
KEY_INTAKE_COMPLETE = "intake_complete"
KEY_INTAKE_SCHEMA_VERSION = "intake_schema_version"
KEY_INTAKE_PHASE = "intake_phase"
KEY_INTAKE_DRAFT = "intake_draft"
KEY_INTAKE_PENDING = "intake_pending"
KEY_INTAKE_EDIT_ACTIVE = "intake_edit_active"
KEY_INTAKE_RETURN_SUMMARY = "intake_return_summary"

# Внутренний ключ черновика с кэшем резолва места (в ``agent_card`` не попадает).
_DRAFT_GEO_KEY = "__geo__"

# --- Фазы --------------------------------------------------------------------------
PHASE_INPUT = "input"  # ждём ввод значения текущего поля
PHASE_FIELD_CONFIRM = "field_confirm"  # показали эхо, ждём Верно/Исправить
PHASE_FORM_CONFIRM = "form_confirm"  # показали сводку, ждём Подтвердить/Изменить
PHASE_FIELD_PICK = "field_pick"  # выбор поля для правки

# --- callback_data кнопок анкеты (≤64 байта Telegram) ------------------------------
CB_CONFIRM = "mdl:intake:ok"  # Верно ✅ (поле)
CB_REDO = "mdl:intake:redo"  # Исправить ✏️ (это поле заново)
CB_SAVE = "mdl:intake:save"  # Подтвердить и сохранить ✅ (вся анкета)
CB_EDIT = "mdl:intake:edit"  # Изменить ✏️ (из сводки → выбор поля)
CB_RESTART = "mdl:intake:restart"  # Заполнить заново (с нуля)
CB_CANCEL = "mdl:intake:cancel"  # Отмена правки (профиль без изменений)
CB_REDO_ALL = "mdl:intake:all"  # Пройти всё заново (из выбора поля)
CB_FIELD_PREFIX = "mdl:intake:f:"  # Выбрать конкретное поле: mdl:intake:f:<field_key>
CB_PROFILE_EDIT = "mdl:profile:edit"  # Вход в правку из карточки профиля

# Человекочитаемые подписи полей для эха/сводки/выбора.
FIELD_LABELS: dict[str, str] = {
    "full_name": "Имя",
    "birth_date": "Дата рождения",
    "birth_place": "Место рождения",
    "birth_time": "Время рождения",
    "main_concern": "Тема запроса",
    "mood": "Настроение",
}


class PlaceResolveError(Exception):
    """Место рождения не резолвится (город/пояс). Несёт готовое сообщение пользователю."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class PlaceResolution:
    """Результат резолва места рождения (кэшируется в черновике для показа)."""

    lat: float
    lng: float
    tz: str
    resolved_name: str


# Инъекция резолва места: город → :class:`PlaceResolution` или ``PlaceResolveError``.
ResolvePlace = Callable[[str], PlaceResolution]


@dataclass
class IntakeOutcome:
    """Результат одного хода ядра анкеты (без побочных эффектов).

    ``messages`` — что показать пользователю (интерактивные несут ``buttons``).
    ``state_patch`` — shallow-merge в ``scenario_state``.
    ``agent_card_patch`` — shallow-merge в ``agent_card`` (непустой только при финале).
    ``finalize`` — обёртка должна атомарно сохранить профиль и посчитать карту+матрицу.
    ``editing`` — финал пришёл из режима правки (для формулировки сообщения).
    ``committed_field`` — ``(field_key, value)`` зафиксированного поля (обёртка пишет в
    историю сообщений, как раньше делал старый линейный сбор).
    """

    messages: list[OutboundMessage] = field(default_factory=list)
    state_patch: dict[str, Any] = field(default_factory=dict)
    agent_card_patch: dict[str, Any] = field(default_factory=dict)
    finalize: bool = False
    editing: bool = False
    committed_field: tuple[str, str] | None = None


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def input_prompt_buttons(*, edit_active: bool) -> list[list[dict[str, str]]]:
    """Инлайн-кнопки под сообщением-вопросом (ввод значения поля).

    В обычном сборе — «Заполнить заново»; в режиме правки существующего профиля —
    «Отмена» (выйти из правки, профиль без изменений).
    """
    if edit_active:
        return [[_btn("✖️ Отмена", CB_CANCEL)]]
    return [[_btn("🔄 Заполнить заново", CB_RESTART)]]


def _field_confirm_buttons() -> list[list[dict[str, str]]]:
    return [[_btn("Верно ✅", CB_CONFIRM), _btn("Исправить ✏️", CB_REDO)]]


def _form_confirm_buttons() -> list[list[dict[str, str]]]:
    return [[_btn("Подтвердить и сохранить ✅", CB_SAVE)], [_btn("Изменить ✏️", CB_EDIT)]]


def _field_pick_buttons(
    steps: Sequence[IntakeStep], *, edit_active: bool
) -> list[list[dict[str, str]]]:
    rows: list[list[dict[str, str]]] = []
    for step in steps:
        label = FIELD_LABELS.get(step.field_key, step.field_key)
        rows.append([_btn(f"✏️ {label}", f"{CB_FIELD_PREFIX}{step.field_key}")])
    rows.append([_btn("🔄 Пройти всё заново", CB_REDO_ALL)])
    if edit_active:
        rows.append([_btn("✖️ Отмена", CB_CANCEL)])
    return rows


def _label_for(field_key: str) -> str:
    return FIELD_LABELS.get(field_key, field_key)


def _field_index(steps: Sequence[IntakeStep], field_key: str) -> int:
    for i, step in enumerate(steps):
        if step.field_key == field_key:
            return i
    return -1


def _seed_draft_from_card(
    steps: Sequence[IntakeStep], agent_card: dict[str, Any]
) -> dict[str, Any]:
    """Засеять черновик уже известными значениями полей из ``agent_card``.

    Нужно и для миграции незавершённых v1-пользователей (частично заполненный
    ``agent_card`` без черновика), и для входа в правку готового профиля.
    """
    draft: dict[str, Any] = {}
    for step in steps:
        val = agent_card.get(step.field_key)
        if isinstance(val, str) and val.strip():
            draft[step.field_key] = val.strip()
    return draft


def _int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _echo_line(field_key: str, value: str, geo: PlaceResolution | None) -> str:
    label = _label_for(field_key)
    if field_key == "birth_time" and value.strip().lower() in (
        "не знаю",
        "неизвестно",
        "-",
        "нет",
    ):
        return f"{label}: не знаю (рассчитаю карту без асцендента и домов)."
    if field_key == "birth_time":
        return f"{label}: {value} (приму как МЕСТНОЕ время места рождения). Верно?"
    if field_key == "birth_place" and geo is not None:
        return f"{label}: {value} (часовой пояс {geo.tz}). Верно?"
    return f"{label}: {value}. Верно?"


def _prompt_message(step: IntakeStep, *, edit_active: bool, prefix: str = "") -> OutboundMessage:
    text = f"{prefix}{step.prompt}" if prefix else step.prompt
    return OutboundMessage(text=text, buttons=input_prompt_buttons(edit_active=edit_active))


def _summary_text(steps: Sequence[IntakeStep], draft: dict[str, Any]) -> str:
    lines = ["📋 Проверьте анкету перед сохранением:", ""]
    for step in steps:
        val = draft.get(step.field_key)
        shown = val.strip() if isinstance(val, str) and val.strip() else "—"
        lines.append(f"**{_label_for(step.field_key)}:** {shown}")
    lines.append("")
    lines.append("Всё верно? Сохраню профиль и рассчитаю карту.")
    return "\n".join(lines)


def _form_confirm_message(steps: Sequence[IntakeStep], draft: dict[str, Any]) -> OutboundMessage:
    return OutboundMessage(text=_summary_text(steps, draft), buttons=_form_confirm_buttons())


def _field_pick_message(steps: Sequence[IntakeStep], *, edit_active: bool) -> OutboundMessage:
    return OutboundMessage(
        text="Что изменить? Выберите поле или пройдите анкету заново.",
        buttons=_field_pick_buttons(steps, edit_active=edit_active),
    )


def _base_state(*, edit_active: bool) -> dict[str, Any]:
    """Общие ключи, которые каждый ход возвращает (чтобы shallow-merge был согласован)."""
    return {
        KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
        KEY_INTAKE_EDIT_ACTIVE: edit_active,
    }


def _process_value(
    *,
    steps: Sequence[IntakeStep],
    idx: int,
    text: str,
    draft: dict[str, Any],
    edit_active: bool,
    resolve_place: ResolvePlace,
) -> IntakeOutcome:
    """Провалидировать введённое значение поля и перейти к подтверждению поля.

    Синтаксическая валидация — валидатором шага; для места рождения дополнительно
    резолвим город (сеть в ``resolve_place``). При любой невалидности — повторный
    запрос БЕЗ продвижения (кнопки навигации на сообщении обязательно).
    """
    step = steps[idx]
    if not text:
        return IntakeOutcome(
            messages=[
                _prompt_message(step, edit_active=edit_active, prefix="Напишите ответ текстом. ")
            ]
        )
    err = step.validate(text)
    if err is not None:
        prefix = f"Пока не могу принять ответ: {err}. "
        return IntakeOutcome(
            messages=[_prompt_message(step, edit_active=edit_active, prefix=prefix)]
        )

    geo: PlaceResolution | None = None
    if step.field_key == "birth_place":
        try:
            geo = resolve_place(text)
        except PlaceResolveError as exc:
            return IntakeOutcome(
                messages=[
                    _prompt_message(step, edit_active=edit_active, prefix=f"{exc.user_message} ")
                ]
            )

    new_draft = dict(draft)
    if geo is not None:
        new_draft[_DRAFT_GEO_KEY] = {
            "lat": geo.lat,
            "lng": geo.lng,
            "tz": geo.tz,
            "resolved_name": geo.resolved_name,
        }
    state_patch = _base_state(edit_active=edit_active)
    state_patch.update(
        {
            KEY_INTAKE_PHASE: PHASE_FIELD_CONFIRM,
            KEY_INTAKE_STEP_INDEX: idx,
            KEY_INTAKE_PENDING: text,
            KEY_INTAKE_DRAFT: new_draft,
        }
    )
    return IntakeOutcome(
        messages=[
            OutboundMessage(
                text=_echo_line(step.field_key, text, geo), buttons=_field_confirm_buttons()
            )
        ],
        state_patch=state_patch,
    )


def _to_form_confirm(
    steps: Sequence[IntakeStep],
    draft: dict[str, Any],
    *,
    edit_active: bool,
    committed_field: tuple[str, str] | None = None,
) -> IntakeOutcome:
    state_patch = _base_state(edit_active=edit_active)
    state_patch.update(
        {
            KEY_INTAKE_PHASE: PHASE_FORM_CONFIRM,
            KEY_INTAKE_STEP_INDEX: len(steps),
            KEY_INTAKE_PENDING: "",
            KEY_INTAKE_RETURN_SUMMARY: False,
            KEY_INTAKE_DRAFT: draft,
        }
    )
    return IntakeOutcome(
        messages=[_form_confirm_message(steps, draft)],
        state_patch=state_patch,
        committed_field=committed_field,
    )


def _restart(steps: Sequence[IntakeStep]) -> IntakeOutcome:
    """Начать сбор с нуля (сбрасываем черновик и фазы)."""
    state_patch = _base_state(edit_active=False)
    state_patch.update(
        {
            KEY_INTAKE_PHASE: PHASE_INPUT,
            KEY_INTAKE_STEP_INDEX: 0,
            KEY_INTAKE_COMPLETE: False,
            KEY_INTAKE_PENDING: "",
            KEY_INTAKE_RETURN_SUMMARY: False,
            KEY_INTAKE_DRAFT: {},
        }
    )
    prefix = "Начинаем анкету заново. "
    return IntakeOutcome(
        messages=[_prompt_message(steps[0], edit_active=False, prefix=prefix)],
        state_patch=state_patch,
    )


def _cancel_edit() -> IntakeOutcome:
    """Выйти из правки существующего профиля без изменений."""
    state_patch = {
        KEY_INTAKE_PHASE: "",
        KEY_INTAKE_PENDING: "",
        KEY_INTAKE_RETURN_SUMMARY: False,
        KEY_INTAKE_EDIT_ACTIVE: False,
        KEY_INTAKE_DRAFT: {},
        KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
    }
    # Информационное сообщение без кнопок — обёртка добавит контекстную навигацию.
    return IntakeOutcome(
        messages=[OutboundMessage(text="Правка отменена — профиль остался без изменений.")],
        state_patch=state_patch,
    )


def _stale() -> IntakeOutcome:
    """Устаревшая кнопка анкеты у уже сохранённого профиля — мягко подсказать."""
    return IntakeOutcome(
        messages=[
            OutboundMessage(
                text=(
                    "Профиль уже сохранён. Откройте профиль, чтобы отредактировать, "
                    "или выберите действие ниже."
                )
            )
        ]
    )


def _start_profile_edit(steps: Sequence[IntakeStep], agent_card: dict[str, Any]) -> IntakeOutcome:
    """Вход в правку из карточки профиля: черновик из ``agent_card`` → выбор поля."""
    draft = _seed_draft_from_card(steps, agent_card)
    state_patch = {
        KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
        KEY_INTAKE_PHASE: PHASE_FIELD_PICK,
        KEY_INTAKE_DRAFT: draft,
        KEY_INTAKE_PENDING: "",
        KEY_INTAKE_RETURN_SUMMARY: False,
        KEY_INTAKE_EDIT_ACTIVE: True,
    }
    return IntakeOutcome(
        messages=[_field_pick_message(steps, edit_active=True)],
        state_patch=state_patch,
    )


def step_intake(
    *,
    steps: Sequence[IntakeStep],
    state: dict[str, Any],
    agent_card: dict[str, Any],
    user_text: str,
    resolve_place: ResolvePlace,
) -> IntakeOutcome:
    """Один ход анкеты. Чистая функция: без БД, без сети (резолв места инъектируется).

    Предполагается, что обёртка уже отсеяла служебные команды (``/help`` и т.п.) и
    решила, что анкета активна (см. :func:`is_intake_callback` и гейт в
    ``scenario_intake``). ``user_text`` — либо введённое значение, либо callback-код
    кнопки анкеты.
    """
    text = (user_text or "").strip()

    # Вход в правку профиля работает независимо от фазы (профиль уже сохранён).
    if text == CB_PROFILE_EDIT:
        return _start_profile_edit(steps, agent_card)

    phase = str(state.get(KEY_INTAKE_PHASE) or "")
    complete = bool(state.get(KEY_INTAKE_COMPLETE))
    draft = dict(state.get(KEY_INTAKE_DRAFT) or {})
    edit_active = bool(state.get(KEY_INTAKE_EDIT_ACTIVE))
    return_summary = bool(state.get(KEY_INTAKE_RETURN_SUMMARY))
    idx = _int(state.get(KEY_INTAKE_STEP_INDEX), 0)

    # Инициализация/миграция: нет фазы.
    if not phase:
        if complete:
            return _stale()
        phase = PHASE_INPUT
        if not draft:
            draft = _seed_draft_from_card(steps, agent_card)
        if idx < 0 or idx >= len(steps):
            idx = 0

    if phase == PHASE_INPUT:
        return _handle_input_phase(
            steps=steps,
            idx=idx,
            text=text,
            draft=draft,
            edit_active=edit_active,
            return_summary=return_summary,
            resolve_place=resolve_place,
        )
    if phase == PHASE_FIELD_CONFIRM:
        return _handle_field_confirm_phase(
            steps=steps,
            idx=idx,
            text=text,
            draft=draft,
            edit_active=edit_active,
            return_summary=return_summary,
            state=state,
            resolve_place=resolve_place,
        )
    if phase == PHASE_FORM_CONFIRM:
        return _handle_form_confirm_phase(
            steps=steps, text=text, draft=draft, edit_active=edit_active
        )
    if phase == PHASE_FIELD_PICK:
        return _handle_field_pick_phase(
            steps=steps, text=text, draft=draft, edit_active=edit_active
        )

    # Неизвестная фаза — чинимся к вводу текущего шага.
    safe_idx = idx if 0 <= idx < len(steps) else 0
    return _process_value(
        steps=steps,
        idx=safe_idx,
        text="",
        draft=draft,
        edit_active=edit_active,
        resolve_place=resolve_place,
    )


def _handle_input_phase(
    *,
    steps: Sequence[IntakeStep],
    idx: int,
    text: str,
    draft: dict[str, Any],
    edit_active: bool,
    return_summary: bool,
    resolve_place: ResolvePlace,
) -> IntakeOutcome:
    if text == CB_RESTART:
        return _restart(steps)
    if text == CB_CANCEL:
        return _cancel_edit() if edit_active else _restart(steps)
    if idx < 0 or idx >= len(steps):
        idx = 0
    return _process_value(
        steps=steps,
        idx=idx,
        text=text,
        draft=draft,
        edit_active=edit_active,
        resolve_place=resolve_place,
    )


def _handle_field_confirm_phase(
    *,
    steps: Sequence[IntakeStep],
    idx: int,
    text: str,
    draft: dict[str, Any],
    edit_active: bool,
    return_summary: bool,
    state: dict[str, Any],
    resolve_place: ResolvePlace,
) -> IntakeOutcome:
    if idx < 0 or idx >= len(steps):
        idx = 0
    step = steps[idx]

    if text == CB_CONFIRM:
        pending = str(state.get(KEY_INTAKE_PENDING) or "").strip()
        if not pending:
            # Потеряли значение (устаревшее состояние) — переспросим поле.
            return _reask_field(steps, idx, edit_active=edit_active)
        new_draft = dict(draft)
        new_draft[step.field_key] = pending
        committed = (step.field_key, pending)
        if return_summary:
            return _to_form_confirm(
                steps, new_draft, edit_active=edit_active, committed_field=committed
            )
        next_idx = idx + 1
        if next_idx >= len(steps):
            return _to_form_confirm(
                steps, new_draft, edit_active=edit_active, committed_field=committed
            )
        state_patch = _base_state(edit_active=edit_active)
        state_patch.update(
            {
                KEY_INTAKE_PHASE: PHASE_INPUT,
                KEY_INTAKE_STEP_INDEX: next_idx,
                KEY_INTAKE_PENDING: "",
                KEY_INTAKE_DRAFT: new_draft,
            }
        )
        return IntakeOutcome(
            messages=[_prompt_message(steps[next_idx], edit_active=edit_active)],
            state_patch=state_patch,
            committed_field=committed,
        )

    if text == CB_REDO:
        return _reask_field(steps, idx, edit_active=edit_active)
    if text == CB_RESTART:
        return _restart(steps)
    if text == CB_CANCEL:
        return _cancel_edit() if edit_active else _restart(steps)

    # Пользователь ввёл новое значение вместо клика — трактуем как исправление поля.
    return _process_value(
        steps=steps,
        idx=idx,
        text=text,
        draft=draft,
        edit_active=edit_active,
        resolve_place=resolve_place,
    )


def _reask_field(steps: Sequence[IntakeStep], idx: int, *, edit_active: bool) -> IntakeOutcome:
    step = steps[idx]
    state_patch = _base_state(edit_active=edit_active)
    state_patch.update(
        {
            KEY_INTAKE_PHASE: PHASE_INPUT,
            KEY_INTAKE_STEP_INDEX: idx,
            KEY_INTAKE_PENDING: "",
        }
    )
    return IntakeOutcome(
        messages=[
            _prompt_message(step, edit_active=edit_active, prefix="Хорошо, введите заново. ")
        ],
        state_patch=state_patch,
    )


def _handle_form_confirm_phase(
    *,
    steps: Sequence[IntakeStep],
    text: str,
    draft: dict[str, Any],
    edit_active: bool,
) -> IntakeOutcome:
    if text == CB_SAVE:
        field_keys = {s.field_key for s in steps}
        patch = {k: v for k, v in draft.items() if k in field_keys and isinstance(v, str)}
        state_patch = {
            KEY_INTAKE_COMPLETE: True,
            KEY_INTAKE_PHASE: "",
            KEY_INTAKE_STEP_INDEX: len(steps),
            KEY_INTAKE_PENDING: "",
            KEY_INTAKE_RETURN_SUMMARY: False,
            KEY_INTAKE_EDIT_ACTIVE: False,
            KEY_INTAKE_DRAFT: {},
            KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
        }
        return IntakeOutcome(
            state_patch=state_patch,
            agent_card_patch=patch,
            finalize=True,
            editing=edit_active,
        )
    if text == CB_EDIT:
        state_patch = _base_state(edit_active=edit_active)
        state_patch.update({KEY_INTAKE_PHASE: PHASE_FIELD_PICK})
        return IntakeOutcome(
            messages=[_field_pick_message(steps, edit_active=edit_active)],
            state_patch=state_patch,
        )
    if text == CB_RESTART:
        return _restart(steps)
    if text == CB_CANCEL and edit_active:
        return _cancel_edit()
    # Непонятный ввод — переспросим подтверждение сводки.
    return IntakeOutcome(messages=[_form_confirm_message(steps, draft)])


def _handle_field_pick_phase(
    *,
    steps: Sequence[IntakeStep],
    text: str,
    draft: dict[str, Any],
    edit_active: bool,
) -> IntakeOutcome:
    if text.startswith(CB_FIELD_PREFIX):
        key = text[len(CB_FIELD_PREFIX) :]
        i = _field_index(steps, key)
        if i >= 0:
            state_patch = _base_state(edit_active=edit_active)
            state_patch.update(
                {
                    KEY_INTAKE_PHASE: PHASE_INPUT,
                    KEY_INTAKE_STEP_INDEX: i,
                    KEY_INTAKE_RETURN_SUMMARY: True,
                    KEY_INTAKE_PENDING: "",
                }
            )
            return IntakeOutcome(
                messages=[
                    _prompt_message(
                        steps[i],
                        edit_active=edit_active,
                        prefix=f"Новое значение — {_label_for(key)}. ",
                    )
                ],
                state_patch=state_patch,
            )
        return IntakeOutcome(messages=[_field_pick_message(steps, edit_active=edit_active)])
    if text == CB_REDO_ALL:
        state_patch = _base_state(edit_active=edit_active)
        state_patch.update(
            {
                KEY_INTAKE_PHASE: PHASE_INPUT,
                KEY_INTAKE_STEP_INDEX: 0,
                KEY_INTAKE_RETURN_SUMMARY: False,
                KEY_INTAKE_PENDING: "",
            }
        )
        return IntakeOutcome(
            messages=[
                _prompt_message(steps[0], edit_active=edit_active, prefix="Пройдём анкету заново. ")
            ],
            state_patch=state_patch,
        )
    if text == CB_CANCEL and edit_active:
        return _cancel_edit()
    if text == CB_RESTART:
        return _restart(steps)
    return IntakeOutcome(messages=[_field_pick_message(steps, edit_active=edit_active)])


def is_intake_callback(text: str | None) -> bool:
    """``True``, если текст — callback-код анкеты/правки (обёртке нужно вести флоу)."""
    if not text:
        return False
    t = text.strip()
    return t == CB_PROFILE_EDIT or t.startswith("mdl:intake:")


__all__ = [
    "CB_PROFILE_EDIT",
    "INTAKE_SCHEMA_VERSION",
    "KEY_INTAKE_COMPLETE",
    "KEY_INTAKE_DRAFT",
    "KEY_INTAKE_EDIT_ACTIVE",
    "KEY_INTAKE_PENDING",
    "KEY_INTAKE_PHASE",
    "KEY_INTAKE_RETURN_SUMMARY",
    "KEY_INTAKE_SCHEMA_VERSION",
    "KEY_INTAKE_STEP_INDEX",
    "IntakeOutcome",
    "PlaceResolution",
    "PlaceResolveError",
    "ResolvePlace",
    "input_prompt_buttons",
    "is_intake_callback",
    "step_intake",
]
