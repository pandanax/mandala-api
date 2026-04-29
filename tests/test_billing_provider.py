"""Юнит-тесты ``PostgresBillingProvider`` с фейковым ``Connection`` (тикет 18)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from mandala.services.billing import PostgresBillingProvider


class _RowReturningId:
    """Имитация ``CursorResult`` после ``INSERT … RETURNING``."""

    def __init__(self, row_id: UUID | None) -> None:
        self._row_id = row_id

    def one_or_none(self) -> tuple[UUID] | None:
        if self._row_id is None:
            return None
        return (self._row_id,)


class _UpdateResult:
    """Имитация результата ``UPDATE``."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


@pytest.fixture
def plan_id() -> UUID:
    return uuid4()


def test_activate_plan_inserts_and_updates_user(
    user_id: UUID,
    plan_id: UUID,
) -> None:
    tx_id = uuid4()
    conn = MagicMock()
    conn.execute.side_effect = [
        _RowReturningId(tx_id),
        _UpdateResult(1),
    ]

    svc = PostgresBillingProvider(conn)
    out = svc.activate_plan(
        user_id=user_id,
        vertical_id="therapy",
        plan_id=plan_id,
        provider="telegram_stars",
        external_id="pay_1",
        amount=Decimal("100.00"),
        currency="XTR",
    )

    assert out.status == "activated"
    assert out.payment_transaction_id == tx_id
    assert conn.execute.call_count == 2


def test_activate_plan_duplicate_external_id_skips_second_effect(
    user_id: UUID,
    plan_id: UUID,
) -> None:
    existing_id = uuid4()
    conn = MagicMock()
    conn.execute.side_effect = [
        _RowReturningId(None),
        _RowReturningId(existing_id),
    ]

    svc = PostgresBillingProvider(conn)
    out = svc.activate_plan(
        user_id=user_id,
        vertical_id="therapy",
        plan_id=plan_id,
        provider="telegram_stars",
        external_id="pay_dup",
        amount=Decimal("10.00"),
        currency="XTR",
    )

    assert out.status == "duplicate_external_id"
    assert out.payment_transaction_id == existing_id
    assert conn.execute.call_count == 2


def test_activate_plan_user_mismatch_after_insert(
    user_id: UUID,
    plan_id: UUID,
) -> None:
    tx_id = uuid4()
    conn = MagicMock()
    conn.execute.side_effect = [
        _RowReturningId(tx_id),
        _UpdateResult(0),
    ]

    svc = PostgresBillingProvider(conn)
    out = svc.activate_plan(
        user_id=user_id,
        vertical_id="wrong_vertical",
        plan_id=plan_id,
        provider="stripe",
        external_id="ch_1",
        amount=Decimal("9.99"),
        currency="usd",
    )

    assert out.status == "user_mismatch"
    assert out.payment_transaction_id == tx_id
