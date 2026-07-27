"""Минимальный пайплайн «текст → LLM → ответ» с квотой и записью в ``messages`` (тикет 12).

До вызова этого модуля анкета вертикали обрабатывается в
``mandala.services.scenario_intake`` (тикет 13). Роутер «текст vs изображение» —
``mandala.domain.handler`` + ``mandala.services.intent_router`` / ``image_reply`` (тикет 14).

Память диалога (тикет 17): в запрос к модели попадают последние
:const:`TEXT_REPLY_CONTEXT_MESSAGES` строк из ``messages`` (после записи текущего
входа пользователя), в хронологическом порядке. Порядок сегментов контекста:
блок текущей даты/времени
(:func:`mandala.services.llm_time_context.build_llm_time_context_block`) →
системный промпт вертикали → блок KB (RAG, тикет 16) → опциональная сводка
``scenario_state["dialog_summary"]`` (в том же ``system``) → история ролей
``user``/``assistant`` с непустым ``content_text`` (последняя реплика — текущий
вход). Связка лимитов символов/токенов — см. ``README`` и ``docs/agent.md``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.astro.destiny_matrix import (
    compute_destiny_matrix,
    destiny_matrix_to_system_text,
)
from mandala.astro.natal_chart import (
    calculate_current_transits,
    current_transits_to_system_text,
    natal_chart_to_system_text,
)
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
from mandala.repositories.profiles import ProfileRepository
from mandala.services.llm_time_context import build_llm_time_context_block
from mandala.services.nav_protocol import assign_ids, extract_prose_nav, split_llm_nav_suffix
from mandala.services.quota import RESOURCE_TEXT_REPLY, QuotaService
from mandala.services.telegram_stars import build_premium_invoice_message
from mandala.verticals import get_vertical_system_prompt
from mandala.verticals.client_knowledge import (
    AGENT_CARD_ASTRO_SYSTEM,
    AGENT_CARD_NATAL_CHART_DATA,
    split_llm_agent_card_suffix,
)

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


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Потолок токенов ответа LLM. ``deepseek-v4-flash`` — reasoning-модель: reasoning-токены
# считаются в этот лимит. При слишком низком значении (был 1024) reasoning на длинном
# контексте съедает весь бюджет и ``content`` приходит пустым — пользователь видит
# MSG_LLM_UNAVAILABLE. Держим с запасом: это лишь потолок, платим только за реально
# сгенерированные токены. Переопределяется переменной окружения ``LLM_MAX_TOKENS``.
TEXT_REPLY_MAX_TOKENS = _env_positive_int("LLM_MAX_TOKENS", 8000)


def _close_client_if_any(client: object) -> None:
    """У :class:`OpenAICompatibleTextClient` и тестовых дублей может быть ``close``."""
    closer = getattr(client, "close", None)
    if callable(closer):
        closer()


_NATAL_NO_MATH_INSTRUCTION = (
    "ВНИМАНИЕ: рассчитанной натальной карты сейчас НЕТ. Не выдумывай, не вычисляй и не "
    "воспроизводи по памяти положения планет, дома или асцендент — это делает только "
    "математический движок (Swiss Ephemeris), не ты. Если пользователь просит натальную "
    "карту, честно скажи, что карту нужно рассчитать, и предложи проверить/уточнить дату, "
    "время и город рождения (кнопка «Натальная карта»). Общие темы (транзиты, сезонные "
    "ритмы, Карта судьбы) отвечать можно."
)


def build_natal_prompt_section(natal_data: object) -> str:
    """Секция system-промпта про натальную карту.

    Единственный источник карты — математика (Swiss Ephemeris). Если переданы
    рассчитанные данные (dict от :func:`mandala.astro.natal_chart.calculate_natal_chart`),
    возвращает блок РАССЧИТАННОЙ карты; иначе — явный запрет выдумывать/пересчитывать
    карту. НИКОГДА не подставляет сюда LLM-текст: раньше при отсутствии математики в
    промпт уходил сохранённый LLM-текст карты, что и порождало выдуманные позиции
    «между школами» (жалоба пользователя). Лучше честно не строить, чем выдать выдуманную.
    """
    if isinstance(natal_data, dict) and natal_data:
        try:
            return natal_chart_to_system_text(natal_data)
        except Exception:
            logger.warning("failed to format natal_chart_data for system prompt", exc_info=True)
    return _NATAL_NO_MATH_INSTRUCTION


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
    agent_card: Mapping[str, object] | None = None,
) -> list[OutboundMessage]:
    """Сохранить вход пользователя, проверить квоту, вызвать LLM, сохранить ответ, ``consume``.

    ``llm_client`` можно передать в тестах; иначе создаётся через
    :func:`mandala.llm.factory.create_text_client_for_vertical`.

    ``kb_search`` — опциональный поиск по KB (тикет 16); если ``None``, при включённом env
    используется :func:`mandala.rag.factory.create_kb_search_from_env`.

    ``agent_card`` — снимок ``client_profiles.agent_card`` для контекста (астрология:
    анкета и сохранённая натальная карта). После ответа LLM допускается слияние
    разрешённых полей из хвоста ``---mandala---`` + JSON (см. ``client_knowledge``).
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
        return [OutboundMessage(text=MSG_QUOTA_EXCEEDED), build_premium_invoice_message()]

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
    system_prompt = f"{build_llm_time_context_block()}\n\n{system_prompt}"
    card = dict(agent_card or {})
    if event.vertical_id.strip() == "astrology":
        lines: list[str] = []
        for key, label in (
            ("full_name", "Имя"),
            ("birth_date", "Дата рождения"),
            ("birth_place", "Место рождения"),
            ("birth_time", "Время рождения"),
        ):
            val = card.get(key)
            if isinstance(val, str) and val.strip():
                lines.append(f"- {label}: {val.strip()}")
        if lines:
            system_prompt = (
                f"{system_prompt}\n\nДанные клиента из анкеты (не переспрашивай без причины):\n"
                + "\n".join(lines)
            )
        # Приоритет 1: математически рассчитанные данные (точность Swiss Ephemeris)
        natal_data = card.get(AGENT_CARD_NATAL_CHART_DATA)
        # Активная школа: приоритет — система, в которой посчитана натальная карта
        # (иначе прогноз-транзиты подмешают тропическую сетку к сидерической карте
        # и получится «смешение школ», как в фидбеке Евгении). Затем — выбор из
        # анкеты (astro_system), затем дефолт western.
        astro_system = "western"
        if isinstance(natal_data, dict) and isinstance(natal_data.get("chart_system_key"), str):
            astro_system = natal_data["chart_system_key"] or "western"
        elif isinstance(card.get(AGENT_CARD_ASTRO_SYSTEM), str):
            astro_system = str(card[AGENT_CARD_ASTRO_SYSTEM]) or "western"
        system_label = (
            "ведическая (сидерическая, Lahiri)"
            if astro_system == "vedic"
            else "западная (тропическая)"
        )
        system_prompt = (
            f"{system_prompt}\n\nАКТИВНАЯ АСТРОЛОГИЧЕСКАЯ ШКОЛА: {system_label}. "
            "Интерпретируй строго в этой системе. Не смешивай западную (тропическую) и "
            "ведическую (сидерическую) традиции, не приводи позиции другой школы «для "
            "сравнения» и не выдумывай знаки/градусы — используй только рассчитанные "
            "значения из блоков натальной карты и транзитов ниже."
        )
        # Единственный источник натальной карты — математика (Swiss Ephemeris,
        # см. astro.natal_chart). Если рассчитанных данных нет — карту НЕ выдаём как
        # факт и НЕ даём модели её сочинить (раньше сюда подставлялся сохранённый
        # LLM-текст карты — путь, который порождал выдуманные позиции «между школами»,
        # ровно жалоба пользователя). Лучше честно не строить, чем выдать выдуманную.
        system_prompt = f"{system_prompt}\n\n{build_natal_prompt_section(natal_data)}"
        # Текущие транзиты — актуальные позиции планет для прогнозов
        try:
            now_utc = datetime.now(tz=UTC)
            transits = calculate_current_transits(
                now_utc.year,
                now_utc.month,
                now_utc.day,
                now_utc.hour,
                system=astro_system,
            )
            system_prompt = f"{system_prompt}\n\n{current_transits_to_system_text(transits)}"
        except Exception:
            logger.warning("failed to compute current transits for system prompt", exc_info=True)
        # Карта судьбы (Матрица Судьбы) — отдельная от астрологии система: чистая
        # нумерология даты рождения (без эфемерид/времени/места). Считаем на лету, если
        # есть дата, и подмешиваем как ДАННЫЕ — модель интерпретирует, но не считает.
        birth_date_raw = card.get("birth_date")
        if isinstance(birth_date_raw, str) and birth_date_raw.strip():
            try:
                dm = compute_destiny_matrix(birth_date_raw.strip())
                system_prompt = f"{system_prompt}\n\n{destiny_matrix_to_system_text(dm)}"
            except Exception:
                logger.warning("failed to compute destiny matrix for system prompt", exc_info=True)
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
        reply = client.complete(chat, max_tokens=TEXT_REPLY_MAX_TOKENS)
    except LlmProviderError as e:
        logger.warning(
            "funnel llm %s status=%s detail=%r",
            op_format(
                vertical_id=event.vertical_id,
                user_id=user_id,
                stage="text_llm",
                outcome="provider_error",
            ),
            e.status_code,
            getattr(e, "provider_detail", None),
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

    # Астрология: отделяем служебные хвосты. Сначала блок навигации (он последний),
    # затем agent-card блок — так каждый парсер видит только свой блок.
    nav_spec = None
    if event.vertical_id.strip() == "astrology":
        reply_wo_nav, nav_spec = split_llm_nav_suffix(reply)
        cleaned_reply, agent_patch = split_llm_agent_card_suffix(reply_wo_nav)
        # Модель не дала валидный nav-JSON, но могла написать пункты «куда дальше» прозой —
        # вытащим их в кнопки и уберём из видимого текста (переходы живут только в кнопках).
        if nav_spec is None:
            cleaned_reply, nav_spec = extract_prose_nav(cleaned_reply)
    else:
        cleaned_reply, agent_patch = reply, {}
    # Защита от пустого ответа: если после отделения хвостов ничего не осталось,
    # откатываемся на исходный reply, а в крайнем случае — на сообщение о недоступности.
    if not cleaned_reply.strip():
        cleaned_reply = reply.strip() or MSG_LLM_UNAVAILABLE
    if agent_patch:
        ProfileRepository(conn).merge_agent_card(user_id, agent_patch)

    messages.insert(
        user_id=user_id,
        vertical_id=event.vertical_id,
        role="assistant",
        content_text=cleaned_reply,
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

    # Навигация: динамические кнопки «следующий шаг» + кликабельные термины из ответа LLM.
    # nav_map (id → полный запрос) сохраняем в agent_card — при клике его достанет
    # resolve_nav_action (callback ≤64 байта / start-payload физически не вмещают текст).
    if nav_spec is not None:
        render = assign_ids(nav_spec)
        ProfileRepository(conn).merge_agent_card(user_id, {"nav_map": render.nav_map})
        return [
            OutboundMessage(
                text=cleaned_reply,
                buttons=render.buttons or None,
                term_links=render.term_links or None,
            )
        ]

    return [OutboundMessage(text=cleaned_reply)]
