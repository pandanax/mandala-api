"""Точка входа доменной обработки входящих событий (тикеты 6, 8, 12–16)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.llm import ImageGenerationClient, TextCompletionClient
from mandala.observability import op_format
from mandala.rag.protocol import KbSearchPort
from mandala.repositories import ProfileRepository, WalletRepository
from mandala.services.image_reply import handle_inbound_image_generation
from mandala.services.intent_router import post_intake_intent
from mandala.services.nav_guarantee import ensure_nav
from mandala.services.nav_protocol import resolve_nav_action
from mandala.services.profile_view import build_profile_message
from mandala.services.scenario_intake import handle_intake_before_llm
from mandala.services.telegram_stars import (
    build_pack_invoice_message,
    build_packs_picker_message,
)
from mandala.services.text_reply import handle_inbound_text_llm
from mandala.services.user_identity import UserIdentityService
from mandala.verticals.client_knowledge import AGENT_CARD_ASTRO_SYSTEM, AGENT_CARD_NATAL_CHART_DATA
from mandala.verticals.quick_actions import (
    expand_inbound_quick_action,
    is_forecast_menu,
    is_forecast_request,
    is_packs_menu,
    is_reset_button,
    is_show_profile,
    is_system_switch,
    parse_pack_callback,
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

    # Навигация: клик по динамической кнопке (mdl:nav:*) или deep-link кликабельного
    # термина (/start mdlnav_*) → достаём сохранённый запрос и идём прямым текстовым
    # ходом LLM (минуя анкету/quick-actions). Проверяем до анкеты, т.к. deep-link
    # приходит как «/start …» и иначе был бы съеден мягким рестартом анкеты.
    nav_query = resolve_nav_action(event.text, profile.agent_card.get("nav_map"))
    if nav_query is not None:
        logger.info(
            "funnel inbound %s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=uid,
                channel=event.channel,
                stage="route",
                intent="nav",
            ),
        )
        raw_summary_nav = profile.scenario_state.get("dialog_summary")
        dialog_summary_nav = raw_summary_nav.strip() if isinstance(raw_summary_nav, str) else None
        nav_event = event.model_copy(update={"text": nav_query})
        text_result = handle_inbound_text_llm(
            conn,
            nav_event,
            uid,
            llm_client=llm_client,
            kb_search=kb_search,
            dialog_summary=dialog_summary_nav,
            agent_card=profile.agent_card,
        )
        return ensure_nav(text_result, event.vertical_id)

    # Бургер-команда навигации astrology «Прогноз» (/forecast) → подменю периодов,
    # обрабатываем ДО анкеты. «Натальная карта» (/natal) и «Карта судьбы» (/matrix)
    # теперь мгновенный детерминированный рендер из БД — их обрабатывает
    # ``handle_intake_before_llm`` как служебные команды (без LLM).
    burger = _burger_nav_command(event.vertical_id, event.text)
    if burger == "forecast":
        return _handle_forecast_menu()

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

    # Покупка пакетов сообщений (пакетная монетизация): пикер и счёт конкретного пакета.
    # Проверяем сырой callback независимо от таблицы разворота quick_actions.
    packs_out = _route_message_packs(event.vertical_id, event.text, uid=uid, channel=event.channel)
    if packs_out is not None:
        return packs_out

    event_for_pipeline = event
    expanded = expand_inbound_quick_action(event.vertical_id, event.text)
    if expanded is not None and expanded != event.text:
        switched, new_system = is_system_switch(expanded)
        if switched:
            return ensure_nav(
                _handle_system_switch(conn, uid, event.vertical_id, new_system, profile.agent_card),
                event.vertical_id,
            )
        if is_show_profile(expanded):
            return ensure_nav(
                _handle_show_profile(conn, uid, event.vertical_id, profile.agent_card),
                event.vertical_id,
            )
        if is_forecast_menu(expanded):
            return _handle_forecast_menu()
        event_for_pipeline = event.model_copy(update={"text": expanded})

    # Свободный текст-интент «прогноз» без периода → сразу кнопки-периоды, а не LLM
    # (иначе модель просит уточнить период текстом).
    if event.vertical_id.strip() == "astrology" and is_forecast_request(event_for_pipeline.text):
        logger.info(
            "funnel inbound %s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=uid,
                channel=event.channel,
                stage="route",
                intent="forecast_menu",
            ),
        )
        return _handle_forecast_menu()

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
        image_result = handle_inbound_image_generation(
            conn, event_for_pipeline, uid, image_client=image_client
        )
        return ensure_nav(image_result, event.vertical_id)
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
    text_result = handle_inbound_text_llm(
        conn,
        event_for_pipeline,
        uid,
        llm_client=llm_client,
        kb_search=kb_search,
        dialog_summary=dialog_summary,
        agent_card=profile.agent_card,
    )
    return ensure_nav(text_result, event.vertical_id)


# Бургер-команда навигации (setMyCommands) → внутренний код. Только astrology.
# ``/natal`` / ``/matrix`` здесь НЕ обрабатываются — это мгновенный рендер из БД
# в ``scenario_intake`` (без LLM); тут остаётся только меню прогноза.
_BURGER_NAV_COMMANDS = {"/forecast": "forecast"}


def _burger_nav_command(vertical_id: str, text: str | None) -> str | None:
    """Распознать бургер-команду навигации ``/forecast`` (форма ``/cmd@bot`` тоже).

    Возвращает ``"forecast"`` для astrology, иначе ``None``.
    """
    if vertical_id.strip() != "astrology" or not text:
        return None
    head = text.strip().split(maxsplit=1)[0]
    if "@" in head:
        head = head.split("@", 1)[0]
    return _BURGER_NAV_COMMANDS.get(head.lower())


def _handle_forecast_menu() -> list[OutboundMessage]:
    """Показать inline-подменю выбора периода прогноза."""
    return [
        OutboundMessage(
            text=(
                "Выберите период прогноза или напишите свой "
                "(например «на выходные», «на 2026 год»):"
            ),
            buttons=[
                [
                    {"text": "📅 Сегодня", "callback_data": "mdl:fc_today"},
                    {"text": "📆 Неделя", "callback_data": "mdl:fc_week"},
                ],
                [
                    {"text": "🗓️ Месяц", "callback_data": "mdl:fc_month"},
                    {"text": "🔭 Год", "callback_data": "mdl:fc_year"},
                ],
            ],
        )
    ]


def _handle_show_profile(
    conn: Connection,
    user_id: UUID,
    vertical_id: str,
    agent_card: dict[str, Any],
) -> list[OutboundMessage]:
    """Показать пользователю всё, что мы знаем о нём (callback ``__show_profile__``).

    Баланс кошелька живёт в ``users`` (не в ``agent_card``) — подтягиваем его отдельно, чтобы
    в профиле была строка «Осталось сообщений: N» (∞ при промо).
    """
    balance = WalletRepository(conn).get_balance(user_id=user_id, vertical_id=vertical_id)
    return [build_profile_message(vertical_id, agent_card, message_balance=balance)]


def _route_message_packs(
    vertical_id: str,
    text: str | None,
    *,
    uid: UUID,
    channel: str,
) -> list[OutboundMessage] | None:
    """Роутинг покупки пакетов: пикер (``mdl:packs``) или счёт пакета (``mdl:pack:<id>``).

    Возвращает исходящие сообщения, если это pack-действие, иначе ``None`` (обработает
    обычный конвейер). Счёт — терминальное сообщение; пикер получает fallback-навигацию.
    """
    if is_packs_menu(text):
        logger.info(
            "funnel inbound %s",
            op_format(
                vertical_id=vertical_id,
                user_id=uid,
                channel=channel,
                stage="route",
                intent="packs_menu",
            ),
        )
        return ensure_nav([build_packs_picker_message()], vertical_id)
    pack_id = parse_pack_callback(text)
    if pack_id is None:
        return None
    invoice = build_pack_invoice_message(pack_id)
    if invoice is None:
        # Неизвестный pack_id — мягко показываем пикер вместо ошибки.
        return ensure_nav([build_packs_picker_message()], vertical_id)
    logger.info(
        "funnel inbound %s",
        op_format(
            vertical_id=vertical_id,
            user_id=uid,
            channel=channel,
            stage="route",
            intent="pack_invoice",
            pack_id=pack_id,
        ),
    )
    return [invoice]


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
