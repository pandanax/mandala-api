"""Юнит-тесты ``PostgresBillingProvider.credit_pack`` с фейковым ``Connection``.

Идемпотентное зачисление пакета сообщений: вставка ``payment_transactions`` (по ключу
``provider+external_id``) + атомарная прибавка баланса кошелька.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from mandala.services.billing import PostgresBillingProvider


class _OneOrNone:
    """Имитация ``CursorResult`` с методом ``one_or_none`` (INSERT/UPDATE … RETURNING)."""

    def __init__(self, row: tuple[object, ...] | None) -> None:
        self._row = row

    def one_or_none(self) -> tuple[object, ...] | None:
        return self._row


@pytest.fixture
def user_id() -> UUID:
    return uuid4()


def test_credit_pack_inserts_and_adds_balance(user_id: UUID) -> None:
    tx_id = uuid4()
    conn = MagicMock()
    # 1) payments.insert_completed_if_new → RETURNING id; 2) wallet.add_balance → RETURNING balance
    conn.execute.side_effect = [
        _OneOrNone((tx_id,)),
        _OneOrNone((120,)),
    ]

    out = PostgresBillingProvider(conn).credit_pack(
        user_id=user_id,
        vertical_id="astrology",
        provider="telegram_stars",
        external_id="charge_1",
        amount=Decimal("1"),
        currency="XTR",
        messages=100,
        pack_id="100",
        raw_payload={"pack_id": "100"},
    )

    assert out.status == "credited"
    assert out.payment_transaction_id == tx_id
    assert out.new_balance == 120
    assert conn.execute.call_count == 2


def test_credit_pack_duplicate_external_id_does_not_add_balance(user_id: UUID) -> None:
    existing_id = uuid4()
    conn = MagicMock()
    # insert → конфликт (None); затем fetch_id_by_provider_external → существующий id.
    conn.execute.side_effect = [
        _OneOrNone(None),
        _OneOrNone((existing_id,)),
    ]

    out = PostgresBillingProvider(conn).credit_pack(
        user_id=user_id,
        vertical_id="astrology",
        provider="telegram_stars",
        external_id="charge_dup",
        amount=Decimal("2"),
        currency="XTR",
        messages=300,
        pack_id="300",
    )

    assert out.status == "duplicate_external_id"
    assert out.payment_transaction_id == existing_id
    assert out.new_balance is None
    # Ровно 2 обращения: insert (конфликт) + fetch id. Прибавки баланса НЕ было.
    assert conn.execute.call_count == 2


def test_credit_pack_user_mismatch_after_insert(user_id: UUID) -> None:
    tx_id = uuid4()
    conn = MagicMock()
    # insert → id; wallet.add_balance → None (строки users нет / вертикаль не совпала).
    conn.execute.side_effect = [
        _OneOrNone((tx_id,)),
        _OneOrNone(None),
    ]

    out = PostgresBillingProvider(conn).credit_pack(
        user_id=user_id,
        vertical_id="wrong_vertical",
        provider="telegram_stars",
        external_id="charge_x",
        amount=Decimal("5"),
        currency="XTR",
        messages=1000,
        pack_id="1000",
    )

    assert out.status == "user_mismatch"
    assert out.payment_transaction_id == tx_id
    assert out.new_balance is None
