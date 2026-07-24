"""Юнит-тесты маппинга Web → InboundEvent (тикет 21)."""

from __future__ import annotations

import pytest

from mandala.adapters.web.inbound_map import (
    WEB_CHANNEL,
    inbound_event_from_web,
    resolve_web_vertical_id,
)
from mandala.domain.contracts import InboundEvent


@pytest.mark.parametrize(
    ("body", "header", "expected"),
    [
        ("astrology", None, "astrology"),
        (None, "therapy", "therapy"),
        ("  astrology  ", "therapy", "astrology"),
        ("", "therapy", "therapy"),
        (None, None, None),
    ],
)
def test_resolve_web_vertical_id(
    body: str | None, header: str | None, expected: str | None
) -> None:
    assert resolve_web_vertical_id(vertical_id_body=body, vertical_id_header=header) == expected


def test_inbound_event_from_web_minimal() -> None:
    ev = inbound_event_from_web(vertical_id="astrology", external_user_id="demo-1", text="hello")
    assert isinstance(ev, InboundEvent)
    assert ev.vertical_id == "astrology"
    assert ev.channel == WEB_CHANNEL
    assert ev.external_user_id == "demo-1"
    assert ev.text == "hello"
    assert ev.raw_ref is None
    assert ev.attachments == []


def test_inbound_event_from_web_strips_and_optional_fields() -> None:
    ev = inbound_event_from_web(
        vertical_id=" therapy ",
        external_user_id="  u1 ",
        text="  ",
        locale="  ru  ",
        callback_data="  cb  ",
    )
    assert ev.vertical_id == "therapy"
    assert ev.external_user_id == "u1"
    assert ev.text == "cb"
    assert ev.locale == "ru"
    assert ev.callback_data == "cb"


def test_inbound_event_from_web_rejects_empty_external() -> None:
    with pytest.raises(ValueError, match="external_user_id"):
        inbound_event_from_web(vertical_id="astrology", external_user_id="   ")


def test_outbound_message_buttons_serialize_to_json() -> None:
    """buttons в OutboundMessage попадают в JSON-ответ без потерь (round-trip для Web-канала)."""
    from mandala.domain.contracts import OutboundMessage

    msg = OutboundMessage(
        text="Выберите действие",
        buttons=[
            [{"text": "Натальная карта", "callback_data": "mdl:natal"}],
            [{"text": "Сайт", "url": "https://mandala-app.online"}],
        ],
    )
    dumped = msg.model_dump()
    assert dumped["buttons"] == [
        [{"text": "Натальная карта", "callback_data": "mdl:natal"}],
        [{"text": "Сайт", "url": "https://mandala-app.online"}],
    ]
    assert dumped["text"] == "Выберите действие"
