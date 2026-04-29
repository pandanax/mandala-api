"""Интеграция: Telegram Stars → план, идемпотентность (тикет 19, нужен ``DATABASE_URL``)."""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.billing_period import current_billing_period
from mandala.db.engine import create_engine_from_env
from mandala.services.billing import BILLING_PROVIDER_TELEGRAM_STARS, PostgresBillingProvider
from mandala.services.telegram_stars import (
    STARS_INVOICE_PAYLOAD_PREMIUM,
    handle_pre_checkout_query,
    handle_successful_payment,
)

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


def _insert_user(
    conn: Connection,
    *,
    external_user_id: str,
    vertical_id: str = "astrology",
) -> UUID:
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
        {"uid": uid, "vid": vertical_id, "ext": external_user_id},
    )
    return uid


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def test_stars_pre_checkout_accepts_premium_product(engine: Engine) -> None:
    with engine.begin() as conn:
        ok, err = handle_pre_checkout_query(
            conn,
            vertical_id="astrology",
            query={
                "id": "pc1",
                "from": {"id": 70001, "is_bot": False},
                "currency": "XTR",
                "total_amount": 100,
                "invoice_payload": STARS_INVOICE_PAYLOAD_PREMIUM,
            },
        )
    assert ok is True
    assert err is None


def test_stars_pre_checkout_rejects_wrong_currency(engine: Engine) -> None:
    with engine.begin() as conn:
        ok, err = handle_pre_checkout_query(
            conn,
            vertical_id="astrology",
            query={
                "id": "pc2",
                "from": {"id": 70001, "is_bot": False},
                "currency": "USD",
                "total_amount": 100,
                "invoice_payload": STARS_INVOICE_PAYLOAD_PREMIUM,
            },
        )
    assert ok is False
    assert err is not None


def test_successful_payment_activates_resets_usage_idempotent(engine: Engine) -> None:
    chg = f"chg-{uuid4().hex}"
    tg_id = 777_001_042
    ext = str(tg_id)
    p = current_billing_period()
    with engine.begin() as conn:
        uid = _insert_user(conn, external_user_id=ext, vertical_id="astrology")
        conn.execute(
            text(
                """
                INSERT INTO usage_counters (user_id, vertical_id, billing_period, resource, count)
                VALUES (:uid, 'astrology', :p, 'text_reply', 19)
                """
            ),
            {"uid": uid, "p": p},
        )

    msg = {
        "message_id": 1,
        "date": 1,
        "from": {"id": tg_id, "is_bot": False},
        "chat": {"id": tg_id, "type": "private"},
        "successful_payment": {
            "currency": "XTR",
            "total_amount": 1,
            "invoice_payload": STARS_INVOICE_PAYLOAD_PREMIUM,
            "telegram_payment_charge_id": chg,
        },
    }

    with engine.begin() as conn:
        pbp = PostgresBillingProvider(conn)
        handle_successful_payment(
            conn,
            vertical_id="astrology",
            message=msg,
            billing=pbp,
        )
        u_id = conn.execute(
            text("SELECT id FROM users WHERE id = :i"),
            {"i": uid},
        ).scalar_one()
        assert conn.execute(
            text("SELECT current_plan_id FROM users WHERE id = :i"),
            {"i": u_id},
        ).scalar_one() == _plan_id(conn, "premium")
        cnt = conn.execute(
            text(
                """
                SELECT count FROM usage_counters
                WHERE user_id = :uid
                  AND vertical_id = 'astrology'
                  AND billing_period = :p
                  AND resource = 'text_reply'
                """
            ),
            {"uid": u_id, "p": p},
        ).scalar_one()
        assert int(cnt) == 0
        end = conn.execute(
            text("SELECT subscription_period_end FROM users WHERE id = :id"),
            {"id": u_id},
        ).scalar_one()
        assert end is not None
        if isinstance(end, datetime):
            assert end.tzinfo is not None

    with engine.begin() as conn:
        r1 = PostgresBillingProvider(conn).activate_plan(
            user_id=uid,
            vertical_id="astrology",
            plan_id=_plan_id(conn, "premium"),
            provider=BILLING_PROVIDER_TELEGRAM_STARS,
            external_id=chg,
            amount=Decimal("1"),
            currency="XTR",
        )
        assert r1.status == "duplicate_external_id"
