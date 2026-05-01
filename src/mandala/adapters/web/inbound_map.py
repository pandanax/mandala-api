"""Маппинг HTTP Web → :class:`~mandala.domain.contracts.InboundEvent` (тикет 21)."""

from __future__ import annotations

from mandala.domain.contracts import InboundEvent

# Идентификатор канала в ``channel_links`` и :func:`handle_inbound`.
WEB_CHANNEL = "web"

# Приоритет ``vertical_id``: тело запроса, затем заголовок (см. ``docs/channels.md``).
# Маппинг ``Authorization: Bearer`` → вертикаль — TODO до появления таблицы ключей.


def resolve_web_vertical_id(
    *,
    vertical_id_body: str | None,
    vertical_id_header: str | None,
) -> str | None:
    """Вернуть slug вертикали или ``None``, если нет непустого значения в теле и заголовке."""
    for raw in (vertical_id_body, vertical_id_header):
        if raw is None:
            continue
        s = raw.strip()
        if s:
            return s
    return None


def inbound_event_from_web(
    *,
    vertical_id: str,
    external_user_id: str,
    text: str | None = None,
    locale: str | None = None,
    callback_data: str | None = None,
) -> InboundEvent:
    """Собрать ``InboundEvent`` для канала ``web`` (без ``raw_ref``: ответ — JSON)."""
    ext = external_user_id.strip()
    if not ext:
        msg = "external_user_id must be non-empty"
        raise ValueError(msg)
    tid = text.strip() if isinstance(text, str) else None
    text_norm = tid if tid else None
    loc = locale.strip() if isinstance(locale, str) and locale.strip() else None
    cb = callback_data.strip() if isinstance(callback_data, str) and callback_data.strip() else None
    if text_norm is None and cb is not None:
        text_norm = cb
    return InboundEvent(
        vertical_id=vertical_id.strip(),
        channel=WEB_CHANNEL,
        external_user_id=ext,
        text=text_norm,
        locale=loc,
        callback_data=cb,
        attachments=[],
        raw_ref=None,
    )
