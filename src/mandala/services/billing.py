"""Биллинг кошелька: идемпотентное зачисление пакета сообщений (пакетная модель).

Покупка пакета Stars → **зачисление N сообщений** на баланс кошелька
(``mandala.repositories.wallet``). Деньги живые, поэтому зачисление **идемпотентно**:
повтор с тем же ``(provider, external_id)`` (Telegram ``telegram_payment_charge_id``) НЕ
зачисляет баланс второй раз — идемпотентность на уровне ``payment_transactions``
(``UNIQUE(provider, external_id)``): вставка транзакции и прибавка баланса происходят в одной
транзакции БД, поэтому дубликат апдейта не создаёт вторую строку и не двигает баланс.

История: раньше здесь были ``activate_plan`` + ``apply_plan_change`` (месячная подписка);
пакетная модель их заменяет на ``credit_pack``. Подписки/периодов больше нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.observability import op_format
from mandala.repositories.payments import PaymentTransactionsRepository
from mandala.repositories.wallet import WalletRepository

logger = logging.getLogger(__name__)

# Имя провайдера в ``payment_transactions.provider`` для Bot API (Stars).
BILLING_PROVIDER_TELEGRAM_STARS: str = "telegram_stars"

CreditPackStatus = Literal["credited", "duplicate_external_id", "user_mismatch"]


@dataclass(frozen=True)
class CreditPackResult:
    """Результат ``credit_pack``."""

    status: CreditPackStatus
    payment_transaction_id: UUID | None = None
    """Идентификатор строки ``payment_transactions`` (новой или существующей)."""
    new_balance: int | None = None
    """Баланс после зачисления (``None`` при дубликате/несовпадении пользователя)."""


class BillingProvider(Protocol):
    """Изоляция способа оплаты: зачисление пакета в кошелёк после подтверждённого платежа."""

    def credit_pack(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        provider: str,
        external_id: str,
        amount: Decimal,
        currency: str,
        messages: int,
        pack_id: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> CreditPackResult:
        """Идемпотентно зафиксировать оплату и зачислить ``messages`` на баланс."""


class PostgresBillingProvider:
    """``BillingProvider`` поверх ``Connection`` (одна транзакция с вызывающим кодом)."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def credit_pack(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        provider: str,
        external_id: str,
        amount: Decimal,
        currency: str,
        messages: int,
        pack_id: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> CreditPackResult:
        payments = PaymentTransactionsRepository(self._conn)
        new_id = payments.insert_completed_if_new(
            user_id=user_id,
            vertical_id=vertical_id,
            provider=provider,
            external_id=external_id,
            amount=amount,
            currency=currency,
            plan_id=None,
            raw_payload=raw_payload,
        )
        if new_id is not None:
            wallet = WalletRepository(self._conn)
            new_balance = wallet.add_balance(
                user_id=user_id,
                vertical_id=vertical_id,
                amount=messages,
            )
            if new_balance is None:
                logger.warning(
                    "funnel billing %s",
                    op_format(
                        vertical_id=vertical_id,
                        user_id=user_id,
                        stage="credit_pack",
                        outcome="user_mismatch",
                        provider=provider,
                        pack_id=pack_id,
                    ),
                )
                return CreditPackResult(
                    status="user_mismatch",
                    payment_transaction_id=new_id,
                )
            logger.info(
                "funnel billing %s",
                op_format(
                    vertical_id=vertical_id,
                    user_id=user_id,
                    stage="credit_pack",
                    outcome="credited",
                    provider=provider,
                    pack_id=pack_id,
                    messages=messages,
                    balance=new_balance,
                ),
            )
            return CreditPackResult(
                status="credited",
                payment_transaction_id=new_id,
                new_balance=new_balance,
            )

        existing_id = payments.fetch_id_by_provider_external(
            provider=provider,
            external_id=external_id,
        )
        logger.info(
            "funnel billing %s",
            op_format(
                vertical_id=vertical_id,
                user_id=user_id,
                stage="credit_pack",
                outcome="duplicate_external_id",
                provider=provider,
                pack_id=pack_id,
            ),
        )
        return CreditPackResult(
            status="duplicate_external_id",
            payment_transaction_id=existing_id,
        )
