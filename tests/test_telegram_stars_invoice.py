"""Выставление счёта Telegram Stars: билдер, sendInvoice, точки инициации покупки.

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
from mandala.services.scenario_intake import _handle_command
from mandala.services.telegram_stars import (
    STARS_INVOICE_PAYLOAD_PREMIUM,
    build_premium_invoice_message,
    premium_price_stars,
)
from mandala.verticals.post_intake_offers import post_intake_completion_message
from mandala.verticals.quick_actions import (
    PREMIUM_BUTTON_CALLBACK,
    expand_inbound_quick_action,
    is_premium_topup,
)

# --- билдер счёта -----------------------------------------------------------------


def test_build_premium_invoice_message_payload_matches_plan() -> None:
    msg = build_premium_invoice_message()
    assert msg.requires_payment is True
    assert msg.invoice is not None
    # payload должен совпадать с plans.external_product_id (миграция t19_01) —
    # иначе pre_checkout не найдёт план.
    assert msg.invoice.payload == STARS_INVOICE_PAYLOAD_PREMIUM
    assert msg.invoice.payload == "mandala_premium_stars"
    assert msg.invoice.amount_stars >= 1


def test_premium_price_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_STARS_PREMIUM_PRICE", "111")
    assert premium_price_stars() == 111
    assert build_premium_invoice_message().invoice.amount_stars == 111  # type: ignore[union-attr]


def test_premium_price_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_STARS_PREMIUM_PRICE", "not-a-number")
    assert premium_price_stars() == 250
    monkeypatch.setenv("MANDALA_STARS_PREMIUM_PRICE", "0")
    assert premium_price_stars() == 250


def test_stars_invoice_rejects_zero_amount() -> None:
    with pytest.raises(ValueError):
        StarsInvoice(title="t", description="d", payload="p", amount_stars=0)


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
            title="Mandala Premium",
            description="Расширенный доступ",
            payload=STARS_INVOICE_PAYLOAD_PREMIUM,
            prices=[{"label": "Mandala Premium", "amount": 250}],
        )
    assert out == {"message_id": 5}
    assert str(seen["url"]).endswith("/sendInvoice")
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["currency"] == "XTR"
    assert body["provider_token"] == ""  # признак оплаты в звёздах
    assert body["payload"] == STARS_INVOICE_PAYLOAD_PREMIUM
    assert body["prices"] == [{"label": "Mandala Premium", "amount": 250}]


# --- доставка OutboundMessage со счётом -------------------------------------------


def test_deliver_invoice_calls_send_invoice() -> None:
    api = MagicMock()
    deliver_outbound_messages(
        api,
        chat_id=7,
        messages=[build_premium_invoice_message()],
    )
    api.send_invoice.assert_called_once()
    kwargs = api.send_invoice.call_args.kwargs
    assert kwargs["chat_id"] == 7
    assert kwargs["currency"] == "XTR"
    assert kwargs["payload"] == STARS_INVOICE_PAYLOAD_PREMIUM
    assert kwargs["prices"][0]["amount"] == build_premium_invoice_message().invoice.amount_stars  # type: ignore[union-attr]
    # счёт терминален — обычный sendMessage для него не вызывается
    api.send_message.assert_not_called()


# --- инициация покупки: /topup ----------------------------------------------------


def test_topup_command_shows_invoice() -> None:
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
    invoices = [m for m in out if m.invoice is not None]
    assert len(invoices) == 1
    assert invoices[0].invoice.payload == STARS_INVOICE_PAYLOAD_PREMIUM  # type: ignore[union-attr]


# --- инициация покупки: апселл-кнопка в пост-intake офферах ------------------------


def test_post_intake_offers_have_premium_button() -> None:
    for card in ({}, {"natal_chart_text": "карта"}):
        m = post_intake_completion_message("astrology", card)
        flat = [c.get("callback_data") for row in (m.buttons or []) for c in row]
        assert PREMIUM_BUTTON_CALLBACK in flat
    t = post_intake_completion_message("therapy", {})
    flat_t = [c.get("callback_data") for row in (t.buttons or []) for c in row]
    assert PREMIUM_BUTTON_CALLBACK in flat_t


def test_premium_callback_expands_to_topup_code() -> None:
    for vertical in ("astrology", "therapy"):
        expanded = expand_inbound_quick_action(vertical, PREMIUM_BUTTON_CALLBACK)
        assert is_premium_topup(expanded)
    assert not is_premium_topup("mdl:natal")


# --- инициация покупки: исчерпание квоты ------------------------------------------


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


def test_text_quota_exceeded_shows_invoice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_reply, "QuotaService", _DenyQuota)
    monkeypatch.setattr(text_reply, "MessageRepository", _NoopMessages)
    ev = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="1",
        text="расскажи про мою карту",
    )
    out = text_reply.handle_inbound_text_llm(MagicMock(), ev, uuid4())
    assert out[0].text == text_reply.MSG_QUOTA_EXCEEDED
    invoices = [m for m in out if m.invoice is not None]
    assert len(invoices) == 1
    assert invoices[0].invoice.payload == STARS_INVOICE_PAYLOAD_PREMIUM  # type: ignore[union-attr]


def test_image_quota_exceeded_shows_invoice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(image_reply, "QuotaService", _DenyQuota)
    monkeypatch.setattr(image_reply, "MessageRepository", _NoopMessages)
    ev = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id="1",
        text="нарисуй звёздное небо",
    )
    out = image_reply.handle_inbound_image_generation(MagicMock(), ev, uuid4())
    assert out[0].text == image_reply.MSG_IMAGE_PLAN_OR_QUOTA
    invoices = [m for m in out if m.invoice is not None]
    assert len(invoices) == 1
    assert invoices[0].invoice.payload == STARS_INVOICE_PAYLOAD_PREMIUM  # type: ignore[union-attr]
