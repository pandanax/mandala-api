"""Биллинг: контракт провайдера и активация плана в Postgres (тикет 18).

Семантика смены плана: единая функция ``apply_plan_change`` (тикет 19) —
см. ``docs/quotas-and-plans.md``.

Повтор с тем же ``(provider, external_id)`` не обновляет ``users`` повторно
(идемпотентность на уровне ``payment_transactions``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.billing_period import current_billing_period
from mandala.observability import op_format
from mandala.repositories.payments import PaymentTransactionsRepository
from mandala.repositories.usage import UsageRepository
from mandala.repositories.users import UsersRepository

logger = logging.getLogger(__name__)

# Имя провайдера в ``payment_transactions.provider`` для Bot API (Stars).
BILLING_PROVIDER_TELEGRAM_STARS: str = "telegram_stars"

# Граница «оплаченного» периода на пользователе (MVP: 30 суток от момента активации).
STARS_PLAN_SUBSCRIPTION_DAYS: int = 30

ActivatePlanStatus = Literal["activated", "duplicate_external_id", "user_mismatch"]


@dataclass(frozen=True)
class ActivatePlanResult:
    """Результат ``activate_plan``."""

    status: ActivatePlanStatus
    payment_transaction_id: UUID | None = None
    """Идентификатор строки ``payment_transactions`` (новой или существующей)."""


class BillingProvider(Protocol):
    """Изоляция способа оплаты: активация плана в OLTP после подтверждённого платежа.

    Реализации: Postgres (тикет 18), адаптеры под Stars/Stripe (тикет 19+).
    """

    def activate_plan(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        plan_id: UUID,
        provider: str,
        external_id: str,
        amount: Decimal,
        currency: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> ActivatePlanResult:
        """Идемпотентно зафиксировать оплату и выставить план пользователю."""


class PostgresBillingProvider:
    """``BillingProvider`` поверх ``Connection`` (одна транзакция с вызывающим кодом)."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def activate_plan(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        plan_id: UUID,
        provider: str,
        external_id: str,
        amount: Decimal,
        currency: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> ActivatePlanResult:
        payments = PaymentTransactionsRepository(self._conn)
        new_id = payments.insert_completed_if_new(
            user_id=user_id,
            vertical_id=vertical_id,
            provider=provider,
            external_id=external_id,
            amount=amount,
            currency=currency,
            plan_id=plan_id,
            raw_payload=raw_payload,
        )
        if new_id is not None:
            users = UsersRepository(self._conn)
            ok = users.update_current_plan(
                user_id=user_id,
                vertical_id=vertical_id,
                plan_id=plan_id,
            )
            if not ok:
                logger.warning(
                    "funnel billing %s",
                    op_format(
                        vertical_id=vertical_id,
                        user_id=user_id,
                        stage="activate_plan",
                        outcome="user_mismatch",
                        provider=provider,
                        plan_id=plan_id,
                    ),
                )
                return ActivatePlanResult(
                    status="user_mismatch",
                    payment_transaction_id=new_id,
                )
            logger.info(
                "funnel billing %s",
                op_format(
                    vertical_id=vertical_id,
                    user_id=user_id,
                    stage="activate_plan",
                    outcome="activated",
                    provider=provider,
                    plan_id=plan_id,
                ),
            )
            return ActivatePlanResult(
                status="activated",
                payment_transaction_id=new_id,
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
                stage="activate_plan",
                outcome="duplicate_external_id",
                provider=provider,
                plan_id=plan_id,
            ),
        )
        return ActivatePlanResult(
            status="duplicate_external_id",
            payment_transaction_id=existing_id,
        )


def apply_plan_change(
    conn: Connection,
    *,
    user_id: UUID,
    vertical_id: str,
    reason: str,
) -> None:
    """Политика продукта после **первой** успешной активации плана (тикет 19).

    Вызывать только если ``activate_plan`` вернул ``activated`` (не при ``duplicate``).

    - **Usage:** обнуляем счётчики за **текущий** календарный месяц (UTC, ``YYYY-MM``), чтобы
      лимиты платного плана применялись «с нуля» после оплаты в этом месяце.
    - **Период:** ``subscription_period_end = now (UTC) + STARS_PLAN_SUBSCRIPTION_DAYS``;
      старт периода уже проставлен в ``activate_plan`` (``update_current_plan``).

    ``reason`` — метка сценария (без PII) для операционных логов.
    """
    now = datetime.now(tz=UTC)
    period_key = current_billing_period(now)
    usage = UsageRepository(conn)
    usage.reset_counts_for_user_vertical_period(
        user_id=user_id,
        vertical_id=vertical_id,
        billing_period=period_key,
    )
    end = now + timedelta(days=STARS_PLAN_SUBSCRIPTION_DAYS)
    users = UsersRepository(conn)
    users.set_subscription_period_end(
        user_id=user_id,
        vertical_id=vertical_id,
        subscription_period_end=end,
    )
    logger.info(
        "funnel billing %s",
        op_format(
            vertical_id=vertical_id,
            user_id=user_id,
            stage="apply_plan_change",
            reason=reason,
            outcome="ok",
        ),
    )
