"""Пакеты сообщений через Telegram Stars: билдеры счётов, пикер, точки показа кнопок.

Оффлайн (без БД/сети): sendInvoice через ``httpx.MockTransport``, остальные пути — моки.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest

from mandala.adapters.telegram.bot_api import TelegramBotApiClient
from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.domain import StarsInvoice
from mandala.domain.contracts import InboundEvent
from mandala.services import image_reply, text_reply
from mandala.services.message_packs import PACK_IDS, all_packs, pack_by_id
from mandala.services.scenario_intake import _handle_command
from mandala.services.telegram_stars import (
    build_pack_invoice_message,
    build_packs_picker_message,
)
from mandala.verticals.post_intake_offers import post_intake_completion_message
from mandala.verticals.quick_actions import (
    PACKS_MENU_CALLBACK,
    is_packs_menu,
    parse_pack_callback,
)

# --- билдер счёта пакета ----------------------------------------------------------


def test_pack_invoice_payload_and_price_match_config() -> None:
    for pack in all_packs():
        msg = build_pack_invoice_message(pack.pack_id)
        assert msg is not None
        assert msg.requires_payment is True
        assert msg.invoice is not None
        # payload = plans-независимый ключ пакета; по нему pre_checkout/оплата находят грант.
        assert msg.invoice.payload == pack.payload == f"mandala_pack_{pack.pack_id}"
        assert msg.invoice.amount_stars == pack.price_stars >= 1


def test_default_pack_prices_and_grants() -> None:
    assert PACK_IDS == ("100", "300", "1000")
    p100, p300, p1000 = (pack_by_id(i) for i in PACK_IDS)
    assert p100 is not None and (p100.price_stars, p100.messages) == (1, 100)
    assert p300 is not None and (p300.price_stars, p300.messages) == (2, 300)
    assert p1000 is not None and (p1000.price_stars, p1000.messages) == (5, 1000)


def test_pack_invoice_unknown_id_returns_none() -> None:
    assert build_pack_invoice_message("999") is None


def test_pack_price_and_grant_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_PACK_100_PRICE", "7")
    monkeypatch.setenv("MANDALA_PACK_100_MESSAGES", "150")
    pack = pack_by_id("100")
    assert pack is not None
    assert (pack.price_stars, pack.messages) == (7, 150)
    inv = build_pack_invoice_message("100")
    assert inv is not None and inv.invoice is not None
    assert inv.invoice.amount_stars == 7


def test_pack_price_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_PACK_300_PRICE", "not-a-number")
    pack = pack_by_id("300")
    assert pack is not None and pack.price_stars == 2
    monkeypatch.setenv("MANDALA_PACK_300_PRICE", "0")
    pack = pack_by_id("300")
    assert pack is not None and pack.price_stars == 2


def test_stars_invoice_rejects_zero_amount() -> None:
    with pytest.raises(ValueError):
        StarsInvoice(title="t", description="d", payload="p", amount_stars=0)


# --- пикер пакетов (три кнопки) ---------------------------------------------------


def test_packs_picker_has_three_pack_buttons() -> None:
    msg = build_packs_picker_message()
    flat = [c["callback_data"] for row in (msg.buttons or []) for c in row]
    assert flat == ["mdl:pack:100", "mdl:pack:300", "mdl:pack:1000"]
    # Каждая кнопка резолвится в свой pack_id.
    assert [parse_pack_callback(cb) for cb in flat] == ["100", "300", "1000"]


def test_packs_picker_lead_text_override() -> None:
    msg = build_packs_picker_message(text="Пополните баланс:")
    assert msg.text == "Пополните баланс:"


# --- sendInvoice в bot-api --------------------------------------------------------


def _tg_client(handler: object) -> TelegramBotApiClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return TelegramBotApiClient("token", client=httpx.Client(transport=transport))


def test_send_invoice_builds_xtr_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    with _tg_client(handler) as api:
        out = api.send_invoice(
            chat_id=42,
            title="Пакет сообщений · 100",
            description="100 сообщений",
            payload="mandala_pack_100",
            prices=[{"label": "Пакет сообщений · 100", "amount": 1}],
        )
    assert out == {"message_id": 5}
    assert str(seen["url"]).endswith("/sendInvoice")
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["currency"] == "XTR"
    assert body["provider_token"] == ""  # признак оплаты в звёздах
    assert body["payload"] == "mandala_pack_100"


# --- доставка OutboundMessage со счётом --------------------------------------------


def test_deliver_pack_invoice_calls_send_invoice() -> None:
    api = MagicMock()
    inv = build_pack_invoice_message("300")
    assert inv is not None
    deliver_outbound_messages(api, chat_id=7, messages=[inv])
    api.send_invoice.assert_called_once()
    kwargs = api.send_invoice.call_args.kwargs
    assert kwargs["chat_id"] == 7
    assert kwargs["currency"] == "XTR"
    assert kwargs["payload"] == "mandala_pack_300"
    assert kwargs["prices"][0]["amount"] == 2
    # счёт терминален — обычный sendMessage для него не вызывается
    api.send_message.assert_not_called()


# --- инициация покупки: /topup показывает пикер (не счёт) --------------------------


def test_topup_command_shows_packs_picker() -> None:
    ev = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="1",
        text="/topup",
    )
    out = _handle_command(
        conn=MagicMock(),
        event=ev,
        user_id=uuid4(),
        state={},
        steps=[],
        command="/topup",
        intake_complete=True,
    )
    assert len(out) == 1
    assert out[0].invoice is None  # это пикер (кнопки), а не счёт
    flat = [c["callback_data"] for row in (out[0].buttons or []) for c in row]
    assert flat == ["mdl:pack:100", "mdl:pack:300", "mdl:pack:1000"]


# --- апселл-кнопка в пост-intake офферах: «Купить сообщения» -----------------------


def test_post_intake_offers_have_buy_messages_button() -> None:
    for card in ({}, {"natal_chart_data": {"sun_sign": "Рыбы"}}):
        m = post_intake_completion_message("astrology", card)
        flat = [c.get("callback_data") for row in (m.buttons or []) for c in row]
        assert PACKS_MENU_CALLBACK in flat
    t = post_intake_completion_message("therapy", {})
    flat_t = [c.get("callback_data") for row in (t.buttons or []) for c in row]
    assert PACKS_MENU_CALLBACK in flat_t


def test_packs_menu_callback_and_legacy_premium_recognized() -> None:
    assert is_packs_menu(PACKS_MENU_CALLBACK)
    assert is_packs_menu("mdl:premium")  # легаси-кнопка → тоже открывает пикер
    assert not is_packs_menu("mdl:natal")


# --- инициация покупки: исчерпание баланса → пикер --------------------------------


class _DenyQuota:
    def __init__(self, _conn: object) -> None:  # noqa: D401
        pass

    def can_consume(self, **_kw: object) -> bool:
        return False


class _NoopMessages:
    def __init__(self, _conn: object) -> None:  # noqa: D401
        pass

    def insert(self, **_kw: object) -> None:
        return None


def test_text_balance_exhausted_shows_packs_picker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_reply, "QuotaService", _DenyQuota)
    monkeypatch.setattr(text_reply, "MessageRepository", _NoopMessages)
    ev = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="1",
        text="расскажи про мою карту",
    )
    out = text_reply.handle_inbound_text_llm(MagicMock(), ev, uuid4())
    assert len(out) == 1
    assert out[0].text == text_reply.MSG_QUOTA_EXCEEDED
    assert out[0].invoice is None
    flat = [c["callback_data"] for row in (out[0].buttons or []) for c in row]
    assert flat == ["mdl:pack:100", "mdl:pack:300", "mdl:pack:1000"]


def test_image_denied_is_neutral_without_invoice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_reply, "QuotaService", _DenyQuota)
    monkeypatch.setattr(image_reply, "MessageRepository", _NoopMessages)
    ev = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="1",
        text="нарисуй звёздное небо",
    )
    out = image_reply.handle_inbound_image_generation(MagicMock(), ev, uuid4())
    assert len(out) == 1
    assert out[0].text == image_reply.MSG_IMAGE_PLAN_OR_QUOTA
    # Картинки не тарифицируются кошельком → счёта/кнопок покупки здесь нет.
    assert out[0].invoice is None
