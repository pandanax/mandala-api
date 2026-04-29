"""Адаптер канала Web (HTTP, тикет 21)."""

from mandala.adapters.web.inbound_map import (
    WEB_CHANNEL,
    inbound_event_from_web,
    resolve_web_vertical_id,
)

__all__ = [
    "WEB_CHANNEL",
    "inbound_event_from_web",
    "resolve_web_vertical_id",
]
