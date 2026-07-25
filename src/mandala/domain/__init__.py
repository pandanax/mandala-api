"""Доменные контракты и обработка входа (тикет 6, docs/channels.md)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mandala.domain.contracts import (
    InboundAttachment,
    InboundEvent,
    OutboundMessage,
    StarsInvoice,
)

if TYPE_CHECKING:
    # Реальная сигнатура для type-checker'а; в рантайме символ грузится лениво
    # через ``__getattr__`` (ниже), чтобы избежать цикла с ``services`` / ``verticals``.
    from mandala.domain.handler import handle_inbound as handle_inbound

__all__ = [
    "InboundAttachment",
    "InboundEvent",
    "OutboundMessage",
    "StarsInvoice",
    "handle_inbound",
]


def __getattr__(name: str) -> object:
    """Ленивая загрузка обработчика: избегаем цикла с ``services`` / ``verticals``."""
    if name == "handle_inbound":
        from mandala.domain.handler import handle_inbound

        return handle_inbound
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
