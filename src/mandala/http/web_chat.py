"""HTTP-вход для канала ``web``: тот же ``handle_inbound``, ответ JSON (тикет 21)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mandala.adapters.web.inbound_map import inbound_event_from_web, resolve_web_vertical_id
from mandala.domain.contracts import OutboundMessage
from mandala.domain.handler import handle_inbound
from mandala.http.engine_access import get_engine
from mandala.observability import op_format

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web"])


class WebChatRequestBody(BaseModel):
    """Тело ``POST /webhooks/web`` (часть полей дублирует заголовки — см. приоритет в коде)."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, description="Текст сообщения пользователя.")
    vertical_id: str | None = Field(
        default=None,
        description="Slug вертикали; если не задан — обязателен заголовок X-Vertical-Id.",
    )
    locale: str | None = Field(default=None, description="Локаль пользователя, если есть.")
    callback_data: str | None = Field(
        default=None,
        description="Данные кнопки/колбэка в терминах продукта (аналог Telegram callback_data).",
    )


class WebChatResponse(BaseModel):
    """Список исходящих сообщений без внутреннего ``user_id`` (только поля ``OutboundMessage``)."""

    model_config = ConfigDict(extra="forbid")

    messages: list[OutboundMessage] = Field(
        ...,
        description="Ответы ассистента в порядке доставки (текст, фото, флаги).",
    )


@router.post(
    "/webhooks/web",
    response_model=WebChatResponse,
    summary="Входящее сообщение Web-канала",
    description=(
        "Тот же пайплайн, что у Telegram после идентификации: "
        "``InboundEvent`` с ``channel=web``, резолвинг по "
        "``(vertical_id, channel, external_user_id)``, затем ``handle_inbound``. "
        "MVP: стабильный идентификатор пользователя в канале — заголовок **X-External-User-Id** "
        "(см. ``docs/channels.md``). На INFO в логах не пишется текст сообщения."
    ),
)
async def web_inbound_chat(
    body: WebChatRequestBody,
    x_vertical_id: Annotated[str | None, Header(alias="X-Vertical-Id")] = None,
    x_external_user_id: Annotated[str | None, Header(alias="X-External-User-Id")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> WebChatResponse:
    # TODO: API-ключ → vertical_id (таблица/конфиг) — вне scope тикета 21
    _ = authorization
    vertical_id = resolve_web_vertical_id(
        vertical_id_body=body.vertical_id,
        vertical_id_header=x_vertical_id,
    )
    if not vertical_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "vertical_id required: set JSON field vertical_id and/or header X-Vertical-Id. "
                "Bearer → vertical_id mapping is not implemented (see TODO in code)."
            ),
        )
    if not x_external_user_id or not str(x_external_user_id).strip():
        raise HTTPException(
            status_code=422,
            detail="Header X-External-User-Id is required (MVP external user id for channel web).",
        )
    try:
        event = inbound_event_from_web(
            vertical_id=vertical_id,
            external_user_id=x_external_user_id,
            text=body.text,
            locale=body.locale,
            callback_data=body.callback_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    logger.info(
        "funnel web_inbound %s",
        op_format(
            vertical_id=event.vertical_id,
            channel=event.channel,
            stage="received",
            has_text=bool(event.text),
        ),
    )

    try:
        engine = get_engine()
        with engine.begin() as conn:
            outbound_messages = handle_inbound(event, conn)
    except Exception:
        logger.exception(
            "funnel web_inbound %s",
            op_format(vertical_id=event.vertical_id, channel=event.channel, stage="error"),
        )
        raise HTTPException(status_code=500, detail="Inbound processing failed") from None

    logger.info(
        "funnel web_inbound %s",
        op_format(
            vertical_id=event.vertical_id,
            channel=event.channel,
            stage="done",
            n_messages=len(outbound_messages),
        ),
    )
    return WebChatResponse(messages=outbound_messages)
