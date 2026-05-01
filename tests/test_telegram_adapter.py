"""Юнит-тесты адаптера Telegram (тикет 9)."""

from __future__ import annotations

from unittest.mock import MagicMock

from mandala.adapters.telegram.inbound_map import telegram_update_to_inbound_event
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.adapters.telegram.secrets import mask_bot_token
from mandala.domain import OutboundMessage


def test_mask_bot_token_short() -> None:
    assert mask_bot_token("") == "(empty)"
    assert "…" in mask_bot_token("1234567890:ABC-DEF")
    assert mask_bot_token("123456:ABC").startswith("123456:")


def test_telegram_map_private_text() -> None:
    upd = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": 42, "is_bot": False, "language_code": "ru"},
            "chat": {"id": 42, "type": "private"},
            "date": 1,
            "text": "Привет",
        },
    }
    ev = telegram_update_to_inbound_event(upd, vertical_id="astrology")
    assert ev is not None
    assert ev.vertical_id == "astrology"
    assert ev.channel == "telegram"
    assert ev.external_user_id == "42"
    assert ev.text == "Привет"
    assert ev.locale == "ru"
    assert ev.raw_ref is not None and ev.raw_ref["chat_id"] == 42


def test_telegram_map_photo_caption() -> None:
    upd = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "from": {"id": 7, "is_bot": False},
            "chat": {"id": 7, "type": "private"},
            "date": 1,
            "caption": "см. фото",
            "photo": [
                {"file_id": "small", "width": 1, "height": 1, "file_size": 10},
                {"file_id": "large", "width": 100, "height": 100, "file_size": 999},
            ],
        },
    }
    ev = telegram_update_to_inbound_event(upd, vertical_id="therapy")
    assert ev is not None
    assert ev.text == "см. фото"
    assert len(ev.attachments) == 1
    assert ev.attachments[0].kind == "photo"
    assert ev.attachments[0].file_id == "large"


def test_telegram_map_callback() -> None:
    upd = {
        "update_id": 3,
        "callback_query": {
            "id": "cq1",
            "from": {"id": 99, "is_bot": False},
            "message": {
                "message_id": 5,
                "chat": {"id": 100, "type": "group"},
                "date": 1,
                "text": "меню",
            },
            "data": "pay:1",
        },
    }
    ev = telegram_update_to_inbound_event(upd, vertical_id="astrology")
    assert ev is not None
    assert ev.external_user_id == "99"
    assert ev.callback_data == "pay:1"
    assert ev.text == "pay:1"
    assert ev.raw_ref is not None and ev.raw_ref["chat_id"] == 100


def test_telegram_map_callback_without_message_private_fallback() -> None:
    """Если ``message`` в callback нет — личка: подставляем chat_id = from.id."""
    upd = {
        "update_id": 4,
        "callback_query": {
            "id": "cq2",
            "from": {"id": 555, "is_bot": False},
            "data": "mdl:natal",
        },
    }
    ev = telegram_update_to_inbound_event(upd, vertical_id="astrology")
    assert ev is not None
    assert ev.text == "mdl:natal"
    assert ev.raw_ref is not None and ev.raw_ref["chat_id"] == 555


def test_telegram_map_skips_unknown() -> None:
    assert telegram_update_to_inbound_event({"update_id": 1, "poll": {}}, vertical_id="x") is None


def test_deliver_outbound_text_and_photo() -> None:
    api = MagicMock()
    msgs = [
        OutboundMessage(text="a"),
        OutboundMessage(text="cap", photo="file_xyz"),
    ]
    deliver_outbound_messages(api, chat_id=1, messages=msgs)
    assert api.send_message.call_count == 1
    assert api.send_photo.call_count == 1
    api.send_message.assert_called_with(chat_id=1, text="a", reply_markup=None)
    api.send_photo.assert_called_with(
        chat_id=1,
        photo="file_xyz",
        caption="cap",
        reply_markup=None,
    )


def test_deliver_inline_keyboard() -> None:
    api = MagicMock()
    deliver_outbound_messages(
        api,
        chat_id=2,
        messages=[
            OutboundMessage(
                text="t",
                buttons=[[{"text": "OK", "callback_data": "ok"}]],
            )
        ],
    )
    call = api.send_message.call_args
    assert call is not None
    markup = call.kwargs["reply_markup"]
    assert markup == {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
