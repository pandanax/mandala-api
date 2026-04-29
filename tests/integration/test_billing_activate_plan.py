"""Интеграция: идемпотентная активация плана (тикет 18; нужен ``DATABASE_URL``)."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.services.billing import PostgresBillingProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL не задан — интеграционные тесты пропущены",
    ),
]


def _plan_id(conn: Connection, name: str) -> UUID:
    val = conn.execute(text("SELECT id FROM plans WHERE name = :n"), {"n": name}).scalar_one()
    assert isinstance(val, UUID)
    return val


def _insert_user(conn: Connection, *, vertical_id: str = "astrology") -> UUID:
    uid = uuid4()
    pid = _plan_id(conn, "free")
    conn.execute(
        text(
            """
            INSERT INTO users (id, vertical_id, current_plan_id)
            VALUES (:id, :vid, :pid)
            """
        ),
        {"id": uid, "vid": vertical_id, "pid": pid},
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


def test_activate_plan_twice_same_external_id(engine: Engine) -> None:
    ext_pay = f"idemp-{uuid4().hex}"
    with engine.begin() as conn:
        uid = _insert_user(conn)
        premium_id = _plan_id(conn, "premium")
        svc = PostgresBillingProvider(conn)

        r1 = svc.activate_plan(
            user_id=uid,
            vertical_id="astrology",
            plan_id=premium_id,
            provider="test_provider",
            external_id=ext_pay,
            amount=Decimal("1.00"),
            currency="XTR",
            raw_payload={"demo": True},
        )
        assert r1.status == "activated"
        assert r1.payment_transaction_id is not None

        r2 = svc.activate_plan(
            user_id=uid,
            vertical_id="astrology",
            plan_id=premium_id,
            provider="test_provider",
            external_id=ext_pay,
            amount=Decimal("1.00"),
            currency="XTR",
        )
        assert r2.status == "duplicate_external_id"
        assert r2.payment_transaction_id == r1.payment_transaction_id

        current = conn.execute(
            text("SELECT current_plan_id FROM users WHERE id = :id"),
            {"id": uid},
        ).scalar_one()
        assert current == premium_id

        n = conn.execute(
            text(
                """
                SELECT count(*)::int
                FROM payment_transactions
                WHERE provider = 'test_provider'
                  AND external_id = :eid
                """
            ),
            {"eid": ext_pay},
        ).scalar_one()
        assert n == 1
