"""Репозиторий: платежи (идемпотентность по ``(provider, external_id)``, тикет 18)."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class PaymentTransactionsRepository:
    """Запись и чтение ``payment_transactions``."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert_completed_if_new(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        provider: str,
        external_id: str,
        amount: Decimal,
        currency: str,
        plan_id: UUID,
        raw_payload: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Вставить завершённую транзакцию или вернуть ``None``, если ключ уже есть.

        Уникальность: ``uq_payment_provider_external_id``.
        """
        row = self._conn.execute(
            text(
                """
                INSERT INTO payment_transactions (
                    user_id,
                    vertical_id,
                    provider,
                    external_id,
                    amount,
                    currency,
                    plan_id,
                    status,
                    raw_payload
                )
                VALUES (
                    :user_id,
                    :vertical_id,
                    :provider,
                    :external_id,
                    :amount,
                    :currency,
                    :plan_id,
                    'completed',
                    CAST(:raw_payload AS jsonb)
                )
                ON CONFLICT ON CONSTRAINT uq_payment_provider_external_id
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "provider": provider,
                "external_id": external_id,
                "amount": amount,
                "currency": currency,
                "plan_id": plan_id,
                "raw_payload": json.dumps(raw_payload) if raw_payload is not None else None,
            },
        ).one_or_none()
        if row is None:
            return None
        tid = row[0]
        assert isinstance(tid, UUID)
        return tid

    def fetch_id_by_provider_external(self, *, provider: str, external_id: str) -> UUID | None:
        """Найти ``id`` строки по паре провайдера (после конфликта вставки)."""
        row = self._conn.execute(
            text(
                """
                SELECT id
                FROM payment_transactions
                WHERE provider = :provider
                  AND external_id = :external_id
                """
            ),
            {"provider": provider, "external_id": external_id},
        ).one_or_none()
        if row is None:
            return None
        tid = row[0]
        assert isinstance(tid, UUID)
        return tid
