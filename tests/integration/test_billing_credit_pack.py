"""Интеграция: идемпотентное зачисление пакета сообщений (нужен ``DATABASE_URL``).

Деньги живые → повтор с тем же ``(provider, external_id)`` не зачисляет баланс второй раз.
"""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.repositories import WalletRepository
from mandala.services.billing import BILLING_PROVIDER_TELEGRAM_STARS, PostgresBillingProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL не задан — интеграционные тесты пропущены",
    ),
]


def _free_plan_id(conn: Connection) -> UUID:
    val = conn.execute(text("SELECT id FROM plans WHERE name = 'free'")).scalar_one()
    assert isinstance(val, UUID)
    return val


def _insert_user(conn: Connection, *, balance: int, vertical_id: str = "astrology") -> UUID:
    uid = uuid4()
    pid = _free_plan_id(conn)
    conn.execute(
        text(
            """
            INSERT INTO users (id, vertical_id, current_plan_id, message_balance)
            VALUES (:id, :vid, :pid, :bal)
            """
        ),
        {"id": uid, "vid": vertical_id, "pid": pid, "bal": balance},
    )
    conn.execute(
        text(
            """
            INSERT INTO channel_links (user_id, vertical_id, channel, external_user_id)
            VALUES (:uid, :vid, CAST('telegram' AS channel_type), :ext)
            """
        ),
        {"uid": uid, "vid": vertical_id, "ext": f"b-{uid.hex[:12]}"},
    )
    return uid


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def test_credit_pack_twice_same_external_id_credits_once(engine: Engine) -> None:
    ext_pay = f"idemp-{uuid4().hex}"
    start = 10
    messages = 300
    with engine.begin() as conn:
        uid = _insert_user(conn, balance=start)
        svc = PostgresBillingProvider(conn)

        r1 = svc.credit_pack(
            user_id=uid,
            vertical_id="astrology",
            provider=BILLING_PROVIDER_TELEGRAM_STARS,
            external_id=ext_pay,
            amount=Decimal("2"),
            currency="XTR",
            messages=messages,
            pack_id="300",
            raw_payload={"pack_id": "300"},
        )
        assert r1.status == "credited"
        assert r1.payment_transaction_id is not None
        assert r1.new_balance == start + messages

        r2 = svc.credit_pack(
            user_id=uid,
            vertical_id="astrology",
            provider=BILLING_PROVIDER_TELEGRAM_STARS,
            external_id=ext_pay,
            amount=Decimal("2"),
            currency="XTR",
            messages=messages,
            pack_id="300",
        )
        assert r2.status == "duplicate_external_id"
        assert r2.payment_transaction_id == r1.payment_transaction_id

        # Баланс прибавился РОВНО один раз.
        bal = WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology")
        assert bal == start + messages

        n = conn.execute(
            text(
                """
                SELECT count(*)::int FROM payment_transactions
                WHERE provider = :p AND external_id = :eid
                """
            ),
            {"p": BILLING_PROVIDER_TELEGRAM_STARS, "eid": ext_pay},
        ).scalar_one()
        assert n == 1
