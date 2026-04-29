"""Telegram Stars: pre_checkout и successful_payment → ``activate_plan`` (тикет 19)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Final

from sqlalchemy.engine import Connection

from mandala.repositories.plans import PlansRepository
from mandala.services.billing import (
    BILLING_PROVIDER_TELEGRAM_STARS,
    PostgresBillingProvider,
    apply_plan_change,
)
from mandala.services.user_identity import UserIdentityService

# Совпадает с seed/миграцией ``t19_01_telegram_stars`` (``plans.external_product_id``).
STARS_INVOICE_PAYLOAD_PREMIUM: Final = "mandala_premium_stars"

_CHANNEL: Final = "telegram"


def handle_pre_checkout_query(
    conn: Connection,
    *,
    vertical_id: str,
    query: dict[str, Any],
) -> tuple[bool, str | None]:
    """Проверить запрос: валюта XTR, payload → ``plans``; ensure user. Вернуть ``(ok, error)``."""
    currency = str(query.get("currency") or "")
    if currency != "XTR":
        return False, "Нужна оплата в Telegram Stars (XTR)."

    raw_payload = query.get("invoice_payload")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        return False, "Пустой счёт."

    pr = PlansRepository(conn)
    plan_id = pr.fetch_id_by_billing_product(
        billing_provider=BILLING_PROVIDER_TELEGRAM_STARS,
        external_product_id=raw_payload,
    )
    if plan_id is None:
        return False, "Тариф не найден."

    from_user = query.get("from")
    if not isinstance(from_user, dict) or "id" not in from_user:
        return False, "Нет данных покупателя."

    ext = str(int(from_user["id"]))
    uis = UserIdentityService(conn)
    uis.get_or_create_user(vertical_id=vertical_id, channel=_CHANNEL, external_user_id=ext)
    return True, None


def handle_successful_payment(
    conn: Connection,
    *,
    vertical_id: str,
    message: dict[str, Any],
    billing: PostgresBillingProvider | None = None,
) -> None:
    """Зафиксировать оплату идемпотентно и применить ``apply_plan_change`` при первом успехе."""
    sp = message.get("successful_payment")
    if not isinstance(sp, dict):
        msg = "telegram_stars: нет successful_payment"
        raise ValueError(msg)

    payload = sp.get("invoice_payload")
    if not isinstance(payload, str) or not payload.strip():
        msg = "telegram_stars: пустой invoice_payload"
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

    pr = PlansRepository(conn)
    plan_id = pr.fetch_id_by_billing_product(
        billing_provider=BILLING_PROVIDER_TELEGRAM_STARS,
        external_product_id=payload,
    )
    if plan_id is None:
        msg = "telegram_stars: неизвестный товар (payload)"
        raise ValueError(msg)

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
    }

    prov = billing or PostgresBillingProvider(conn)
    result = prov.activate_plan(
        user_id=user_id,
        vertical_id=vertical_id,
        plan_id=plan_id,
        provider=BILLING_PROVIDER_TELEGRAM_STARS,
        external_id=external_id,
        amount=amount,
        currency=currency,
        raw_payload=raw_payload,
    )
    if result.status == "user_mismatch":
        msg = "telegram_stars: user_mismatch после оплаты"
        raise RuntimeError(msg)
    if result.status == "activated":
        apply_plan_change(
            conn,
            user_id=user_id,
            vertical_id=vertical_id,
            reason="telegram_stars_purchase",
        )
