"""Точка входа доменной обработки входящих событий (тикеты 6, 8, 12–16)."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.llm import ImageGenerationClient, TextCompletionClient
from mandala.observability import op_format
from mandala.rag.protocol import KbSearchPort
from mandala.repositories import ProfileRepository
from mandala.services.image_reply import handle_inbound_image_generation
from mandala.services.intent_router import post_intake_intent
from mandala.services.scenario_intake import handle_intake_before_llm
from mandala.services.text_reply import handle_inbound_text_llm
from mandala.services.user_identity import UserIdentityService
from mandala.verticals.quick_actions import expand_inbound_quick_action

logger = logging.getLogger(__name__)


def handle_inbound(
    event: InboundEvent,
    conn: Connection,
    *,
    llm_client: TextCompletionClient | None = None,
    image_client: ImageGenerationClient | None = None,
    kb_search: KbSearchPort | None = None,
) -> list[OutboundMessage]:
    """Обработать входящее событие и вернуть исходящие сообщения.

    Тикет 8: резолвинг пользователя по ``(vertical_id, channel, external_user_id)``,
    план по умолчанию ``free``; загрузка строки ``client_profiles``.

    Тикет 13: пока анкета вертикали не завершена — вопросы и валидация по конфигу шагов,
    обновление ``scenario_state`` / ``agent_card`` без вызова LLM.

    Тикет 12: после анкеты — текст → квота ``text_reply`` → LLM → ``messages``.

    Тикет 17: в ``text_reply`` в контекст модели подмешиваются последние N сообщений
    из ``messages``; опционально ``scenario_state["dialog_summary"]``.

    Тикет 14: при намерении «картинка» — квота ``image_generation`` и
    :mod:`mandala.services.image_reply` (реальный image API или заглушка через env),
    запись в ``messages`` / ``generated_artifacts``, ``consume`` только после успеха.

    ``conn`` — открытое соединение SQLAlchemy в **активной транзакции** (например
    ``with engine.begin() as conn``), чтобы резолвинг и чтение профиля были согласованы.

    ``llm_client`` / ``image_client`` / ``kb_search`` — опциональные подмены
    (в основном для тестов).

    RAG (тикет 16): при ``kb_search=None`` в
    :func:`mandala.services.text_reply.handle_inbound_text_llm` подставляется клиент из env,
    если ``MANDALA_RAG_BACKEND=qdrant`` и задан ``QDRANT_URL``.
    """
    uid = UserIdentityService(conn).get_or_create_user(
        vertical_id=event.vertical_id,
        channel=event.channel,
        external_user_id=event.external_user_id,
        locale=event.locale,
    )
    logger.info(
        "funnel inbound %s",
        op_format(
            vertical_id=event.vertical_id,
            user_id=uid,
            channel=event.channel,
            stage="identity_ok",
        ),
    )
    profiles = ProfileRepository(conn)
    profiles.ensure_row(user_id=uid, vertical_id=event.vertical_id)
    profile = profiles.get_by_user_id(uid)
    if profile is None:
        msg = "client_profiles: ensure_row не создал строку"
        raise RuntimeError(msg)

    intake_out = handle_intake_before_llm(conn, event, uid, profile)
    if intake_out is not None:
        logger.info(
            "funnel inbound %s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=uid,
                channel=event.channel,
                stage="intake_reply",
                n_messages=len(intake_out),
                outcome="short_circuit",
            ),
        )
        return intake_out

    event_for_pipeline = event
    expanded = expand_inbound_quick_action(event.vertical_id, event.text)
    if expanded is not None and expanded != event.text:
        event_for_pipeline = event.model_copy(update={"text": expanded})

    if post_intake_intent(event_for_pipeline.text) == "image":
        logger.info(
            "funnel inbound %s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=uid,
                channel=event.channel,
                stage="route",
                intent="image",
            ),
        )
        return handle_inbound_image_generation(
            conn, event_for_pipeline, uid, image_client=image_client
        )
    logger.info(
        "funnel inbound %s",
        op_format(
            vertical_id=event.vertical_id,
            user_id=uid,
            channel=event.channel,
            stage="route",
            intent="text",
        ),
    )
    raw_summary = profile.scenario_state.get("dialog_summary")
    dialog_summary = raw_summary.strip() if isinstance(raw_summary, str) else None
    return handle_inbound_text_llm(
        conn,
        event_for_pipeline,
        uid,
        llm_client=llm_client,
        kb_search=kb_search,
        dialog_summary=dialog_summary,
        agent_card=profile.agent_card,
    )
