"""Сериализация и валидация ``InboundEvent`` / ``OutboundMessage`` (тикет 6)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mandala.domain import InboundAttachment, InboundEvent, OutboundMessage


def test_inbound_event_minimal_roundtrip() -> None:
    data = {
        "vertical_id": "astrology",
        "channel": "telegram",
        "external_user_id": "tg-42",
    }
    event = InboundEvent.model_validate(data)
    assert event.vertical_id == "astrology"
    assert event.attachments == []
    dumped = event.model_dump(mode="json")
    assert dumped == {
        "vertical_id": "astrology",
        "channel": "telegram",
        "external_user_id": "tg-42",
        "text": None,
        "attachments": [],
        "callback_data": None,
        "locale": None,
        "raw_ref": None,
    }
    assert InboundEvent.model_validate_json(json.dumps(dumped)) == event


def test_inbound_event_full_fields() -> None:
    event = InboundEvent(
        vertical_id="therapy",
        channel="web",
        external_user_id="sess-1",
        text="Привет",
        attachments=[InboundAttachment(kind="photo", file_id="AgACAgIAAxkBAAIB")],
        callback_data="action:pay",
        locale="ru-RU",
        raw_ref={"chat_id": 100500},
    )
    assert len(event.attachments) == 1
    assert event.attachments[0].kind == "photo"


def test_inbound_event_vertical_id_required() -> None:
    with pytest.raises(ValidationError) as exc:
        InboundEvent.model_validate(
            {"channel": "telegram", "external_user_id": "x"},
        )
    assert "vertical_id" in str(exc.value)


def test_inbound_event_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InboundEvent.model_validate(
            {
                "vertical_id": "v",
                "channel": "telegram",
                "external_user_id": "1",
                "unknown_field": 1,
            },
        )


def test_outbound_message_defaults_and_json() -> None:
    m = OutboundMessage(text="ok")
    assert m.requires_payment is False
    assert m.defer is False
    d = m.model_dump(mode="json", exclude_none=True)
    assert d["text"] == "ok"
    assert d["requires_payment"] is False
    assert d["defer"] is False
    compact = m.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    assert compact == {"text": "ok"}


def test_outbound_message_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        OutboundMessage.model_validate({"text": "a", "extra": 1})
