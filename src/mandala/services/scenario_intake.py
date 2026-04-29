"""Сбор полей профиля по конфигу вертикали до перехода к LLM-диалогу (тикет 13).

Роутер «текст vs изображение» — тикет 14.
"""

from __future__ import annotations

import logging
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
    if bool(state.get(KEY_INTAKE_COMPLETE)):
        return None

    user_text = (event.text or "").strip()
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
