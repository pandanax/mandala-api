"""Ветка «запрос картинки»: квота ``image_generation``, ``messages``, ``generated_artifacts``.

**Политика квоты (тикет 15):** ``QuotaService.consume`` только после успешного ответа
image API или заглушки; до вызова провайдера — лишь ``can_consume``.
При ``LlmProviderError`` инкремента нет.

**Асинхронность (MVP):** синхронная генерация в webhook/polling (без Redis/worker).
Таймаут HTTP чтения ~180 с. Очередь и ответ «генерирую…» — см. README и architecture.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.llm.exceptions import LlmProviderError
from mandala.llm.factory import create_image_client_for_vertical
from mandala.llm.image_generation import ImageGenerationClient, ImageGenerationResult
from mandala.observability import op_format
from mandala.repositories.artifacts import ArtifactRepository
from mandala.repositories.messages import MessageRepository
from mandala.services.intent_router import image_prompt_from_user_text
from mandala.services.quota import RESOURCE_IMAGE_GENERATION, QuotaService
from mandala.services.text_reply import MSG_NEED_TEXT

logger = logging.getLogger(__name__)

MSG_IMAGE_PLAN_OR_QUOTA = (
    "Генерация изображений на вашем тарифе сейчас недоступна "
    "(лимит исчерпан или не включён). Текстовые ответы работают как обычно."
)
MSG_IMAGE_FAILED = (
    "Не удалось сгенерировать изображение. Попробуйте позже или переформулируйте запрос."
)


def _close_client_if_any(client: object) -> None:
    closer = getattr(client, "close", None)
    if callable(closer):
        closer()


def _artifact_payload(result: ImageGenerationResult) -> dict[str, object]:
    provider = "stub" if result.stub_ref else "openai_compatible"
    out: dict[str, object] = {
        "provider": provider,
        "prompt_echo": result.prompt_echo,
    }
    if result.stub_ref is not None:
        out["stub_ref"] = result.stub_ref
    if result.image_url is not None:
        out["image_url"] = result.image_url
    return out


def handle_inbound_image_generation(
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    *,
    image_client: ImageGenerationClient | None = None,
) -> list[OutboundMessage]:
    """Сохранить реплику пользователя, проверить квоту, сгенерировать изображение, ``consume``.

    При ``limit_per_period == 0`` или исчерпании лимита **не вызывается** клиент генерации.

    При успехе: строка в ``messages`` (assistant, ``content_kind=image``), строка в
    ``generated_artifacts`` (``kind=image``, ``payload`` JSONB с URL/stub_ref), затем ``consume``.
    """
    user_text = (event.text or "").strip()
    if not user_text:
        return [OutboundMessage(text=MSG_NEED_TEXT)]

    prompt = image_prompt_from_user_text(user_text).strip()
    if not prompt:
        prompt = "описание не указано"

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
        resource=RESOURCE_IMAGE_GENERATION,
    ):
        return [OutboundMessage(text=MSG_IMAGE_PLAN_OR_QUOTA)]

    logger.info(
        "funnel llm %s",
        op_format(
            vertical_id=event.vertical_id,
            user_id=user_id,
            stage="image_gen",
            resource=RESOURCE_IMAGE_GENERATION,
            outcome="call_start",
        ),
    )
    owned = image_client is None
    client: ImageGenerationClient
    if image_client is not None:
        client = image_client
    else:
        client = create_image_client_for_vertical(event.vertical_id)

    try:
        result = client.generate(prompt)
    except LlmProviderError as e:
        logger.warning(
            "funnel llm %s status=%s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=user_id,
                stage="image_gen",
                outcome="provider_error",
            ),
            e.status_code,
        )
        if owned:
            _close_client_if_any(client)
        return [OutboundMessage(text=MSG_IMAGE_FAILED)]

    if owned:
        _close_client_if_any(client)

    logger.info(
        "funnel llm %s",
        op_format(
            vertical_id=event.vertical_id,
            user_id=user_id,
            stage="image_gen",
            outcome="generation_ok",
            has_image_url=result.image_url is not None,
        ),
    )

    if result.stub_ref:
        caption = (
            f"Изображение (демо-заглушка): {result.prompt_echo[:200]!r} — ref={result.stub_ref}"
        )
    elif len(result.prompt_echo) <= 1024:
        caption = result.prompt_echo
    else:
        caption = result.prompt_echo[:1021] + "…"
    meta: dict[str, object] = _artifact_payload(result)
    assistant_id = messages.insert(
        user_id=user_id,
        vertical_id=event.vertical_id,
        role="assistant",
        content_text=caption,
        content_kind="image",
        content_meta=meta,
    )

    artifacts = ArtifactRepository(conn)
    artifacts.insert(
        user_id=user_id,
        vertical_id=event.vertical_id,
        kind="image",
        payload=_artifact_payload(result),
        source_message_id=assistant_id,
    )

    consume_result = quota.consume(
        user_id=user_id,
        vertical_id=event.vertical_id,
        resource=RESOURCE_IMAGE_GENERATION,
    )
    if not consume_result.allowed:
        logger.warning(
            "funnel quota %s",
            op_format(
                vertical_id=event.vertical_id,
                user_id=user_id,
                stage="consume_after_image",
                resource=RESOURCE_IMAGE_GENERATION,
                outcome="deny",
                reason=consume_result.reason,
            ),
        )

    if result.image_url:
        return [
            OutboundMessage(text=caption, photo=result.image_url),
        ]
    return [OutboundMessage(text=caption)]
