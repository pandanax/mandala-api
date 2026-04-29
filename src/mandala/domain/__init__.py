"""Доменные контракты и обработка входа (тикет 6, docs/channels.md)."""

from mandala.domain.contracts import InboundAttachment, InboundEvent, OutboundMessage
from mandala.domain.handler import handle_inbound

__all__ = [
    "InboundAttachment",
    "InboundEvent",
    "OutboundMessage",
    "handle_inbound",
]
