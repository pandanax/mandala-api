"""Telegram Stars (пакетная модель): счета на пакеты, pre_checkout, зачисление по оплате.

Три пакета сообщений — единый источник (payload ↔ цена ⭐ ↔ +сообщений) в
``mandala.services.message_packs``. Пикер (три кнопки) показывается при исчерпании баланса,
в ``/topup`` и в апселле; клик по кнопке пакета → соответствующий счёт Stars. Оплата
зачисляет сообщения на баланс кошелька **идемпотентно** (см. ``mandala.services.billing``).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy.engine import Connection

from mandala.domain.contracts import OutboundMessage, StarsInvoice
from mandala.repositories.wallet import WalletRepository
from mandala.services.billing import BILLING_PROVIDER_TELEGRAM_STARS, PostgresBillingProvider
from mandala.services.message_packs import all_packs, pack_by_id, pack_by_payload
from mandala.services.user_identity import UserIdentityService
from mandala.verticals.quick_actions import pack_callback

_CHANNEL: Final = "telegram"

# Лид-текст пикера пакетов при исчерпании баланса.
MSG_PACKS_LEAD: Final = (
    "Сообщения на балансе закончились 🙌 Выберите пакет — сообщения зачислятся на баланс "
    "и не сгорают:"
)


def build_pack_invoice_message(pack_id: str) -> OutboundMessage | None:
    """``OutboundMessage`` со счётом Stars на конкретный пакет (или ``None`` для неизвестного).

    ``payload`` = ``pack.payload`` — по нему pre_checkout/оплата находят пакет и грант.
    Счёт — терминальное сообщение (текст/кнопки игнорируются каналом).
    """
    pack = pack_by_id(pack_id)
    if pack is None:
        return None
    return OutboundMessage(
        requires_payment=True,
        invoice=StarsInvoice(
            title=pack.title,
            description=pack.description,
            payload=pack.payload,
            amount_stars=pack.price_stars,
        ),
    )


def _balance_line(*, balance: int | None, unlimited: bool) -> str | None:
    """Строка с текущим балансом сообщений для шапки пикера (или ``None``, если нечего показать)."""
    if unlimited:
        return "💬 У тебя сейчас: ∞ (безлимит)"
    if balance is not None:
        return f"💬 У тебя сейчас: {balance} сообщений"
    return None


def build_packs_picker_message(
    *,
    text: str | None = None,
    balance: int | None = None,
    unlimited: bool = False,
) -> OutboundMessage:
    """Сообщение с тремя инлайн-кнопками пакетов (клик → счёт соответствующего пакета).

    Если передан ``balance``/``unlimited`` и явный ``text`` не задан — над предложением
    выбрать пакет добавляется строка с текущим балансом сообщений («∞ (безлимит)» под промо).
    """
    buttons = [
        [{"text": pack.button_label, "callback_data": pack_callback(pack.pack_id)}]
        for pack in all_packs()
    ]
    if text is not None:
        body = text
    else:
        line = _balance_line(balance=balance, unlimited=unlimited)
        body = f"{line}\n\n{MSG_PACKS_LEAD}" if line is not None else MSG_PACKS_LEAD
    return OutboundMessage(text=body, buttons=buttons)


def build_packs_picker_with_balance(
    conn: Connection,
    *,
    user_id: Any,
    vertical_id: str,
) -> OutboundMessage:
    """Пикер пакетов с шапкой «текущий баланс сообщений» (под промо → «∞ (безлимит)»).

    Единый способ показать пользователю, сколько у него сейчас сообщений, при открытии
    «Купить сообщения» (и из ``/topup``, и из инлайн-кнопки ``mdl:packs``).
    """
    from mandala.services.promo import is_promo_active

    unlimited = is_promo_active(user_id=user_id, vertical_id=vertical_id, conn=conn)
    balance = (
        None
        if unlimited
        else WalletRepository(conn).get_balance(user_id=user_id, vertical_id=vertical_id)
    )
    return build_packs_picker_message(balance=balance, unlimited=unlimited)


def handle_pre_checkout_query(
    conn: Connection,
    *,
    vertical_id: str,
    query: dict[str, Any],
) -> tuple[bool, str | None]:
    """Проверить запрос: валюта XTR, payload → пакет; ensure user. Вернуть ``(ok, error)``."""
    currency = str(query.get("currency") or "")
    if currency != "XTR":
        return False, "Нужна оплата в Telegram Stars (XTR)."

    raw_payload = query.get("invoice_payload")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        return False, "Пустой счёт."

    if pack_by_payload(raw_payload) is None:
        return False, "Пакет не найден."

    from_user = query.get("from")
    if not isinstance(from_user, dict) or "id" not in from_user:
        return False, "Нет данных покупателя."

    ext = str(int(from_user["id"]))
    uis = UserIdentityService(conn)
    uis.get_or_create_user(vertical_id=vertical_id, channel=_CHANNEL, external_user_id=ext)
    return True, None


@dataclass(frozen=True)
class SuccessfulPaymentOutcome:
    """Итог обработки ``successful_payment`` для дружелюбного подтверждения пользователю."""

    credited_messages: int
    new_balance: int | None
    duplicate: bool


def handle_successful_payment(
    conn: Connection,
    *,
    vertical_id: str,
    message: dict[str, Any],
    billing: PostgresBillingProvider | None = None,
) -> SuccessfulPaymentOutcome:
    """Зачислить пакет на баланс **идемпотентно** по ``telegram_payment_charge_id``.

    Повтор той же оплаты не зачисляет второй раз (``duplicate=True``, баланс не меняется).
    """
    sp = message.get("successful_payment")
    if not isinstance(sp, dict):
        msg = "telegram_stars: нет successful_payment"
        raise ValueError(msg)

    payload = sp.get("invoice_payload")
    if not isinstance(payload, str) or not payload.strip():
        msg = "telegram_stars: пустой invoice_payload"
        raise ValueError(msg)

    pack = pack_by_payload(payload)
    if pack is None:
        msg = "telegram_stars: неизвестный пакет (payload)"
        raise ValueError(msg)

    charge = sp.get("telegram_payment_charge_id")
    if charge is None:
        msg = "telegram_stars: нет telegram_payment_charge_id"
        raise ValueError(msg)
    external_id = str(charge)

    from_user = message.get("from")
    if not isinstance(from_user, dict) or "id" not in from_user:
        msg = "telegram_stars: нет from"
        raise ValueError(msg)
    ext_user = str(int(from_user["id"]))

    uis = UserIdentityService(conn)
    user_id = uis.get_or_create_user(
        vertical_id=vertical_id,
        channel=_CHANNEL,
        external_user_id=ext_user,
    )

    currency = str(sp.get("currency") or "XTR")
    total = sp.get("total_amount")
    amount = Decimal(int(total)) if total is not None else Decimal(0)
    raw_payload: dict[str, Any] = {
        "currency": currency,
        "total_amount": total,
        "invoice_payload": payload,
        "pack_id": pack.pack_id,
        "messages": pack.messages,
    }

    prov = billing or PostgresBillingProvider(conn)
    result = prov.credit_pack(
        user_id=user_id,
        vertical_id=vertical_id,
        provider=BILLING_PROVIDER_TELEGRAM_STARS,
        external_id=external_id,
        amount=amount,
        currency=currency,
        messages=pack.messages,
        pack_id=pack.pack_id,
        raw_payload=raw_payload,
    )
    if result.status == "user_mismatch":
        msg = "telegram_stars: user_mismatch после оплаты"
        raise RuntimeError(msg)
    if result.status == "credited":
        return SuccessfulPaymentOutcome(
            credited_messages=pack.messages,
            new_balance=result.new_balance,
            duplicate=False,
        )
    # duplicate_external_id — повтор оплаты: не зачисляем, показываем текущий баланс.
    balance = WalletRepository(conn).get_balance(user_id=user_id, vertical_id=vertical_id)
    return SuccessfulPaymentOutcome(
        credited_messages=0,
        new_balance=balance,
        duplicate=True,
    )
