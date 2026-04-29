"""Минимальный пайплайн «текст → LLM → ответ» с квотой и записью в ``messages`` (тикет 12).

До вызова этого модуля анкета вертикали обрабатывается в
``mandala.services.scenario_intake`` (тикет 13). Роутер «текст vs изображение» —
``mandala.domain.handler`` + ``mandala.services.intent_router`` / ``image_reply`` (тикет 14).

Память диалога (тикет 17): в запрос к модели попадают последние
:const:`TEXT_REPLY_CONTEXT_MESSAGES` строк из ``messages`` (после записи текущего
входа пользователя), в хронологическом порядке. Порядок сегментов контекста:
системный промпт вертикали → блок KB (RAG, тикет 16) → опциональная сводка
``scenario_state["dialog_summary"]`` (в том же ``system``) → история ролей
``user``/``assistant`` с непустым ``content_text`` (последняя реплика — текущий
вход). Связка лимитов символов/токенов — см. ``README`` и ``docs/agent.md``.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.llm import ChatMessage, TextCompletionClient
from mandala.llm.exceptions import LlmProviderError
from mandala.llm.factory import create_text_client_for_vertical
from mandala.llm.types import ChatRole
from mandala.observability import op_format
from mandala.rag.config import RagEnvSettings
from mandala.rag.factory import create_kb_search_from_env
from mandala.rag.prompt_injection import build_kb_context_block
from mandala.rag.protocol import KbSearchPort
from mandala.repositories.messages import MessageDTO, MessageRepository
from mandala.services.quota import RESOURCE_TEXT_REPLY, QuotaService
from mandala.verticals import get_vertical_system_prompt

logger = logging.getLogger(__name__)

MSG_NEED_TEXT = "Пока я отвечаю только на текстовые сообщения. Напишите, пожалуйста, текстом."
MSG_QUOTA_EXCEEDED = (
    "Лимит бесплатных текстовых ответов на этот месяц исчерпан. "
    "Попробуйте позже или перейдите на другой тариф."
)
MSG_LLM_UNAVAILABLE = "Сервис ответа временно недоступен. Попробуйте чуть позже."

# Сколько последних строк ``messages`` подмешивать в чат (включая текущий вход
# пользователя). Не путать с ``RAG_MAX_CONTEXT_CHARS`` (лимит символов на фрагменты KB)
# и с ``max_tokens`` ответа LLM — см. docs/agent.md.
TEXT_REPLY_CONTEXT_MESSAGES = 20


def _close_client_if_any(client: object) -> None:
    """У :class:`OpenAICompatibleTextClient` и тестовых дублей может быть ``close``."""
    closer = getattr(client, "close", None)
    if callable(closer):
        closer()


def _message_rows_to_chat_messages(rows_newest_first: list[MessageDTO]) -> list[ChatMessage]:
    """Перевести выборку ``ORDER BY created_at DESC`` в хронологию для Chat Completions."""
    out: list[ChatMessage] = []
    for dto in reversed(rows_newest_first):
        if dto.role not in ("user", "assistant"):
            continue
        body = (dto.content_text or "").strip()
        if not body:
            continue
        role: ChatRole = "user" if dto.role == "user" else "assistant"
        out.append(ChatMessage(role=role, content=body))
    return out


def handle_inbound_text_llm(
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    *,
    llm_client: TextCompletionClient | None = None,
    kb_search: KbSearchPort | None = None,
    dialog_summary: str | None = None,
) -> list[OutboundMessage]:
    """Сохранить вход пользователя, проверить квоту, вызвать LLM, сохранить ответ, ``consume``.

    ``llm_client`` можно передать в тестах; иначе создаётся через
    :func:`mandala.llm.factory.create_text_client_for_vertical`.

    ``kb_search`` — опциональный поиск по KB (тикет 16); если ``None``, при включённом env
    используется :func:`mandala.rag.factory.create_kb_search_from_env`.

    ``dialog_summary`` — опциональная сводка из ``client_profiles.scenario_state``;
    запись и обновление сводки — TODO (вне scope тикета 17; например фоновая нарезка по токенам).
    """
    user_text = (event.text or "").strip()
    if not user_text:
        return [OutboundMessage(text=MSG_NEED_TEXT)]

    messages = MessageRepository(conn)
    messages.insert(
        user_id=user_id,
        vertical_id=event.vertical_id,
        role="user",
        content_text=user_text,
        content_kind="text",
    )

    quota = QuotaService(conn)
    if not quota.can_consume(
        user_id=user_id,
        vertical_id=event.vertical_id,
        resource=RESOURCE_TEXT_REPLY,
    ):
        return [OutboundMessage(text=MSG_QUOTA_EXCEEDED)]

    logger.info(
        "funnel llm %s",
        op_format(
            vertical_id=event.vertical_id,
            user_id=user_id,
            stage="text_llm",
            resource=RESOURCE_TEXT_REPLY,
            outcome="call_start",
        ),
    )
    owned = llm_client is None
    client = llm_client or create_text_client_for_vertical(event.vertical_id)

    search_port = kb_search if kb_search is not None else create_kb_search_from_env()
    system_prompt = get_vertical_system_prompt(event.vertical_id)
    if search_port is not None:
        rag_cfg = RagEnvSettings.from_env()
        try:
            fragments = search_port.search(
                vertical_id=event.vertical_id,
                query=user_text,
                limit=rag_cfg.top_k,
            )
            block = build_kb_context_block(fragments, max_chars=rag_cfg.max_context_chars)
            if block:
                system_prompt = f"{system_prompt}\n\n{block}"
        except Exception:
            logger.warning(
                "funnel llm %s",
                op_format(
                    vertical_id=event.vertical_id,
                    user_id=user_id,
                    stage="text_kb",
                    outcome="retrieval_error",
                ),
                exc_info=True,
            )

    summary = (dialog_summary or "").strip()
    if summary:
        system_prompt = f"{system_prompt}\n\nРанее в беседе (сводка):\n{summary}"

    history_rows = messages.list_recent(
        user_id=user_id,
        vertical_id=event.vertical_id,
        limit=TEXT_REPLY_CONTEXT_MESSAGES,
    )
    history_chat = _message_rows_to_chat_messages(history_rows)
    chat: list[ChatMessage] = [ChatMessage(role="system", content=system_prompt), *history_chat]

    try:
        reply = client.complete(chat, max_tokens=1024)
    except LlmProviderError as e:
        logger.warning(
            "funnel llm %s status=%s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=user_id,
                stage="text_llm",
                outcome="provider_error",
            ),
            e.status_code,
        )
        if owned:
            _close_client_if_any(client)
        return [OutboundMessage(text=MSG_LLM_UNAVAILABLE)]

    if owned:
        _close_client_if_any(client)

    logger.info(
        "funnel llm %s",
        op_format(
            vertical_id=event.vertical_id,
            user_id=user_id,
            stage="text_llm",
            outcome="reply_ok",
            reply_chars=len(reply),
        ),
    )

    messages.insert(
        user_id=user_id,
        vertical_id=event.vertical_id,
        role="assistant",
        content_text=reply,
        content_kind="text",
    )

    consume_result = quota.consume(
        user_id=user_id,
        vertical_id=event.vertical_id,
        resource=RESOURCE_TEXT_REPLY,
    )
    if not consume_result.allowed:
        logger.warning(
            "funnel quota %s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=user_id,
                stage="consume_after_llm",
                resource=RESOURCE_TEXT_REPLY,
                outcome="deny",
                reason=consume_result.reason,
            ),
        )

    return [OutboundMessage(text=reply)]
