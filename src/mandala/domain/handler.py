"""Точка входа доменной обработки входящих событий (тикеты 6, 8, 12–16)."""

from __future__ import annotations

import logging
from typing import Any

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
from mandala.verticals.client_knowledge import AGENT_CARD_ASTRO_SYSTEM, AGENT_CARD_NATAL_CHART_DATA
from mandala.verticals.quick_actions import (
    expand_inbound_quick_action,
    is_reset_button,
    is_show_profile,
    is_system_switch,
)

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

    # Кнопка «Начать заново» — hard reset напрямую из reply keyboard
    if is_reset_button(event.text):
        event_for_pipeline = event.model_copy(update={"text": "/reset"})
        return handle_intake_before_llm(conn, event_for_pipeline, uid, profile) or []

    event_for_pipeline = event
    expanded = expand_inbound_quick_action(event.vertical_id, event.text)
    if expanded is not None and expanded != event.text:
        switched, new_system = is_system_switch(expanded)
        if switched:
            return _handle_system_switch(
                conn, uid, event.vertical_id, new_system, profile.agent_card
            )
        if is_show_profile(expanded):
            return _handle_show_profile(uid, event.vertical_id, profile.agent_card)
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


def _handle_show_profile(
    user_id: object,
    vertical_id: str,
    agent_card: dict[str, Any],
) -> list[OutboundMessage]:
    """Показать пользователю всё, что мы знаем о нём."""
    lines: list[str] = ["👤 <b>Ваш профиль</b>", ""]

    field_labels = [
        ("full_name", "Имя"),
        ("birth_date", "Дата рождения"),
        ("birth_place", "Место рождения"),
        ("birth_time", "Время рождения"),
    ]
    for key, label in field_labels:
        val = agent_card.get(key)
        if isinstance(val, str) and val.strip():
            lines.append(f"<b>{label}:</b> {val.strip()}")

    system = agent_card.get(AGENT_CARD_ASTRO_SYSTEM)
    if isinstance(system, str) and system.strip():
        label = "🕉️ Ведическая (Lahiri)" if system == "vedic" else "🌟 Западная (тропическая)"
        lines.append(f"<b>Система:</b> {label}")

    natal_data = agent_card.get(AGENT_CARD_NATAL_CHART_DATA)
    if isinstance(natal_data, dict) and natal_data:
        lines.append("")
        lines.append("🪐 <b>Рассчитанная натальная карта:</b>")
        sun = natal_data.get("sun_sign", "?")
        moon = natal_data.get("moon_sign", "?")
        asc = natal_data.get("ascendant")
        lines.append(f"  ☀️ Солнце: {sun}")
        lines.append(f"  🌙 Луна: {moon}")
        if asc:
            lines.append(f"  ⬆️ Асцендент: {asc}")
        calc_at = natal_data.get("calculated_at", "")
        if calc_at:
            lines.append(f"  📐 Рассчитано: {calc_at[:10]}")
    elif agent_card.get("natal_chart_text"):
        lines.append("")
        lines.append("📋 Натальная карта сохранена (текстовая версия).")

    promo = agent_card.get("activated_promo")
    if isinstance(promo, str) and promo.strip():
        lines.append("")
        lines.append(f"✅ Промо-код активирован: <code>{promo}</code> — подписка без ограничений")

    lines.append("")
    lines.append("Для сброса данных нажмите «🔄 Начать заново» или введите /reset.")

    return [OutboundMessage(text="\n".join(lines))]


def _handle_system_switch(
    conn: Connection,
    user_id: object,
    vertical_id: str,
    new_system: str,
    agent_card: dict[str, Any],
) -> list[OutboundMessage]:
    """Переключить астрологическую систему и пересчитать натальную карту."""
    from uuid import UUID

    from mandala.repositories.profiles import ProfileRepository
    from mandala.services.scenario_intake import _try_calculate_and_save_natal_chart

    uid = user_id if isinstance(user_id, UUID) else UUID(str(user_id))
    profiles = ProfileRepository(conn)
    profiles.merge_agent_card(uid, {AGENT_CARD_ASTRO_SYSTEM: new_system})
    updated_card = dict(agent_card)
    updated_card[AGENT_CARD_ASTRO_SYSTEM] = new_system
    updated_card.pop(AGENT_CARD_NATAL_CHART_DATA, None)
    _try_calculate_and_save_natal_chart(
        conn=conn, user_id=uid, agent_card=updated_card, profiles=profiles
    )
    label = (
        "🕉️ Ведическая (сидерическая, Lahiri)"
        if new_system == "vedic"
        else "🌟 Западная (тропическая)"
    )
    return [
        OutboundMessage(
            text=(
                f"✅ Переключено на {label} систему.\n"
                "Натальная карта пересчитана. Следующий ответ будет использовать новые позиции."
            )
        )
    ]
