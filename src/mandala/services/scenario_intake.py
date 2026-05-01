"""Сбор полей профиля по конфигу вертикали до перехода к LLM-диалогу (тикет 13).

Роутер «текст vs изображение» — тикет 14.

UX: служебные команды (``/start``, ``/help``, ``/about``, ``/reset``) перехватываются
до валидации шага, не считаются «невалидным ответом» и возвращают приветствие
+ prompt текущего шага. Полноценные команды/inline-кнопки — отдельный тикет.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.repositories.messages import MessageRepository
from mandala.repositories.profiles import ClientProfileDTO, ProfileRepository
from mandala.services.text_reply import MSG_NEED_TEXT
from mandala.verticals.intake_config import IntakeStep, intake_steps_for_vertical

logger = logging.getLogger(__name__)

# Ключи в ``scenario_state`` (тикет 13; эволюция формата — отдельные версии/тикеты).
KEY_INTAKE_STEP_INDEX = "intake_step_index"
KEY_INTAKE_COMPLETE = "intake_complete"
KEY_INTAKE_SCHEMA_VERSION = "intake_schema_version"
INTAKE_SCHEMA_VERSION = 1

# Команды, которые мы трактуем как UX-навигацию, а не как ответ на шаг анкеты.
# ``/start``/``/restart`` — мягкий перезапуск анкеты (обнуляет шаг, сохраняет историю).
# ``/reset`` — полный сброс: чистит ``agent_card``, ``scenario_state`` и историю сообщений.
_SOFT_RESTART_COMMANDS = frozenset({"/start", "/restart"})
_HARD_RESET_COMMANDS = frozenset({"/reset"})
_INFO_COMMANDS = frozenset({"/help", "/about", "/info"})
_ALL_COMMANDS = _SOFT_RESTART_COMMANDS | _HARD_RESET_COMMANDS | _INFO_COMMANDS


def handle_intake_before_llm(
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    profile: ClientProfileDTO,
) -> list[OutboundMessage] | None:
    """Если нужна анкета и она не завершена — обработать ход и вернуть ответы.

    Возвращает ``None``, если нужно перейти к пайплайну LLM (тикет 12).
    """
    steps = intake_steps_for_vertical(event.vertical_id)
    if steps is None:
        return None

    state = profile.scenario_state
    user_text = (event.text or "").strip()
    intake_complete = bool(state.get(KEY_INTAKE_COMPLETE))

    # UX: служебные команды обрабатываем и до, и после завершения анкеты.
    cmd = _extract_command(user_text)
    if cmd is not None:
        return _handle_command(
            conn=conn,
            event=event,
            user_id=user_id,
            state=state,
            steps=steps,
            command=cmd,
            intake_complete=intake_complete,
        )

    if intake_complete:
        return None

    if not user_text:
        return [OutboundMessage(text=MSG_NEED_TEXT)]

    raw_idx = state.get(KEY_INTAKE_STEP_INDEX, 0)
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0:
        idx = 0

    if idx >= len(steps):
        # Починка рассинхрона без потери данных анкеты
        profiles = ProfileRepository(conn)
        profiles.merge_scenario_state(
            user_id,
            {
                KEY_INTAKE_COMPLETE: True,
                KEY_INTAKE_STEP_INDEX: len(steps),
                KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
            },
        )
        logger.warning(
            "intake step index out of range; marked complete vertical_id=%s user_id=%s",
            event.vertical_id,
            user_id,
        )
        return None

    step = steps[idx]
    err = step.validate(user_text)
    if err is not None:
        # Не меняем ``agent_card`` / прогресс шага; только подсказка пользователю.
        prefix = "Пока не могу зафиксировать ответ. "
        lead = _first_step_intro(event.vertical_id) if idx == 0 else ""
        return [OutboundMessage(text=f"{prefix}{err} {lead}{step.prompt}")]

    profiles = ProfileRepository(conn)
    profiles.merge_agent_card(user_id, {step.field_key: user_text})

    messages = MessageRepository(conn)
    messages.insert(
        user_id=user_id,
        vertical_id=event.vertical_id,
        role="user",
        content_text=user_text,
        content_kind="text",
        content_meta={"intake_field": step.field_key},
    )

    next_idx = idx + 1
    patch: dict[str, object] = {
        KEY_INTAKE_STEP_INDEX: next_idx,
        KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
    }
    if next_idx >= len(steps):
        patch[KEY_INTAKE_COMPLETE] = True
    profiles.merge_scenario_state(user_id, patch)

    if next_idx >= len(steps):
        return [
            OutboundMessage(
                text="Спасибо, анкета сохранена. Можете задать вопрос — отвечу в режиме диалога.",
            )
        ]

    nxt: IntakeStep = steps[next_idx]
    return [OutboundMessage(text=nxt.prompt)]


def _first_step_intro(vertical_id: str) -> str:
    v = vertical_id.strip()
    if v == "astrology":
        return "Здравствуйте! Для персонализации ответов сначала короткая анкета. "
    if v == "therapy":
        return "Здравствуйте! Перед разговором задам пару вводных вопросов. "
    return ""


def _vertical_greeting(vertical_id: str) -> str:
    """Полное приветствие: что это за бот и что он умеет (тикет UX-патча).

    Используется только в ответ на служебные команды (``/start``, ``/help`` и т.д.),
    чтобы не загромождать каждое сообщение анкеты.
    """
    v = vertical_id.strip()
    if v == "astrology":
        return (
            "Здравствуйте! Это Mandala — ассистент по астрологии.\n"
            "Я помогу с разбором натальной карты и отвечу на вопросы по астрологии "
            "(прогнозы, совместимость, транзиты).\n"
            "Сначала задам пару коротких вопросов о месте и времени рождения, "
            "затем перейдём к свободному диалогу.\n"
            f"{_COMMANDS_HELP}"
        )
    if v == "therapy":
        return (
            "Здравствуйте! Это Mandala — собеседник в формате поддерживающей беседы.\n"
            "Я не врач и не заменяю психотерапию, но помогу разложить мысли и посмотреть "
            "на ситуацию со стороны.\n"
            "Сначала пара вводных вопросов, затем перейдём к разговору.\n"
            f"{_COMMANDS_HELP}"
        )
    return (
        "Здравствуйте! Сначала задам пару вводных вопросов, затем перейдём к диалогу.\n"
        f"{_COMMANDS_HELP}"
    )


_COMMANDS_HELP = (
    "Команды:\n"
    "• /start — перезапустить анкету (история диалога сохраняется);\n"
    "• /reset — полное обнуление: удаляет анкету и всю историю сообщений;\n"
    "• /help — это сообщение."
)


def _extract_command(user_text: str) -> str | None:
    """Если текст начинается с известной служебной команды — вернуть её в нижнем регистре.

    Распознаются формы ``/cmd`` и ``/cmd@botname`` (Telegram), регистронезависимо.
    Текст с произвольным префиксом (``привет /start``) командой не считается.
    """
    if not user_text or not user_text.startswith("/"):
        return None
    head = user_text.split(maxsplit=1)[0]
    if "@" in head:
        head = head.split("@", 1)[0]
    head = head.lower()
    if head in _ALL_COMMANDS:
        return head
    return None


def _handle_command(
    *,
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    state: dict[str, Any],
    steps: Sequence[IntakeStep],
    command: str,
    intake_complete: bool,
) -> list[OutboundMessage]:
    """UX-обработка служебной команды.

    ``/start``/``/restart`` — мягкий рестарт анкеты к шагу 0 (история сообщений
    сохраняется, ``agent_card`` сохраняется). В ответе приветствие + prompt первого шага.

    ``/reset`` — полное обнуление: удаляются все сообщения пользователя в этой
    вертикали, ``agent_card`` и ``scenario_state`` сбрасываются к ``{}``. Бот «забывает»
    пользователя и анкета начинается с нуля.

    ``/help``/``/about``/``/info`` — без сброса. Если анкета пройдена, просто напоминаем,
    что можно задавать вопросы; иначе — приветствие + prompt текущего шага.
    """
    greeting = _vertical_greeting(event.vertical_id)

    if command in _HARD_RESET_COMMANDS:
        profiles = ProfileRepository(conn)
        profiles.reset_session(user_id)
        n_deleted = MessageRepository(conn).delete_for_user_vertical(
            user_id=user_id, vertical_id=event.vertical_id
        )
        logger.info(
            "intake hard reset vertical_id=%s user_id=%s deleted_messages=%d",
            event.vertical_id,
            user_id,
            n_deleted,
        )
        first_prompt = steps[0].prompt if steps else ""
        body = (
            f"Готово, я всё забыл — начинаем с чистого листа.\n\n{greeting}\n\n{first_prompt}"
        ).rstrip()
        return [OutboundMessage(text=body)]

    if command in _SOFT_RESTART_COMMANDS:
        ProfileRepository(conn).merge_scenario_state(
            user_id,
            {
                KEY_INTAKE_STEP_INDEX: 0,
                KEY_INTAKE_COMPLETE: False,
                KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
            },
        )
        first_prompt = steps[0].prompt if steps else ""
        body = f"{greeting}\n\n{first_prompt}".rstrip()
        return [OutboundMessage(text=body)]

    # info-команды: без побочных эффектов
    if intake_complete:
        return [
            OutboundMessage(
                text=f"{greeting}\n\nВы уже прошли анкету — задайте вопрос текстом, я отвечу.",
            )
        ]
    raw_idx = state.get(KEY_INTAKE_STEP_INDEX, 0)
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= len(steps):
        idx = 0
    cur_prompt = steps[idx].prompt if steps else ""
    body = f"{greeting}\n\n{cur_prompt}".rstrip()
    return [OutboundMessage(text=body)]
