"""Интеграция: Telegram Stars → пакеты сообщений, идемпотентное зачисление (нужен DATABASE_URL)."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.repositories import WalletRepository
from mandala.services.message_packs import pack_by_id
from mandala.services.telegram_stars import (
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
    balance: int,
    vertical_id: str = "astrology",
) -> UUID:
    uid = uuid4()
    pid = _plan_id(conn, "free")
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
        {"uid": uid, "vid": vertical_id, "ext": external_user_id},
    )
    return uid


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def test_pre_checkout_accepts_known_pack(engine: Engine) -> None:
    pack = pack_by_id("300")
    assert pack is not None
    with engine.begin() as conn:
        ok, err = handle_pre_checkout_query(
            conn,
            vertical_id="astrology",
            query={
                "id": "pc1",
                "from": {"id": 70001, "is_bot": False},
                "currency": "XTR",
                "total_amount": pack.price_stars,
                "invoice_payload": pack.payload,
            },
        )
    assert ok is True
    assert err is None


def test_pre_checkout_rejects_wrong_currency(engine: Engine) -> None:
    with engine.begin() as conn:
        ok, err = handle_pre_checkout_query(
            conn,
            vertical_id="astrology",
            query={
                "id": "pc2",
                "from": {"id": 70001, "is_bot": False},
                "currency": "USD",
                "total_amount": 1,
                "invoice_payload": "mandala_pack_100",
            },
        )
    assert ok is False
    assert err is not None


def test_pre_checkout_rejects_unknown_pack(engine: Engine) -> None:
    with engine.begin() as conn:
        ok, err = handle_pre_checkout_query(
            conn,
            vertical_id="astrology",
            query={
                "id": "pc3",
                "from": {"id": 70001, "is_bot": False},
                "currency": "XTR",
                "total_amount": 1,
                "invoice_payload": "mandala_pack_999",
            },
        )
    assert ok is False
    assert err is not None


@pytest.mark.parametrize("pack_id", ["100", "300", "1000"])
def test_successful_payment_credits_correct_amount_idempotent(engine: Engine, pack_id: str) -> None:
    pack = pack_by_id(pack_id)
    assert pack is not None
    chg = f"chg-{uuid4().hex}"
    # Уникальный tg_id на прогон: channel_links.external_user_id уникален, а БД может
    # переиспользоваться между запусками — фиксированный id ловил бы конфликт вставки.
    tg_id = 700_000_000 + (uuid4().int % 90_000_000)
    ext = str(tg_id)
    start_balance = 5
    with engine.begin() as conn:
        uid = _insert_user(conn, external_user_id=ext, balance=start_balance)

    msg = {
        "message_id": 1,
        "date": 1,
        "from": {"id": tg_id, "is_bot": False},
        "chat": {"id": tg_id, "type": "private"},
        "successful_payment": {
            "currency": "XTR",
            "total_amount": pack.price_stars,
            "invoice_payload": pack.payload,
            "telegram_payment_charge_id": chg,
        },
    }

    # Первая оплата: зачисление пакета.
    with engine.begin() as conn:
        out1 = handle_successful_payment(conn, vertical_id="astrology", message=msg)
    assert out1.duplicate is False
    assert out1.credited_messages == pack.messages
    assert out1.new_balance == start_balance + pack.messages
    with engine.begin() as conn:
        bal = WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology")
    assert bal == start_balance + pack.messages

    # Повтор той же оплаты (тот же charge id): НЕ зачисляем второй раз.
    with engine.begin() as conn:
        out2 = handle_successful_payment(conn, vertical_id="astrology", message=msg)
    assert out2.duplicate is True
    assert out2.credited_messages == 0
    with engine.begin() as conn:
        bal2 = WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology")
        n = conn.execute(
            text(
                """
                SELECT count(*)::int FROM payment_transactions
                WHERE provider = 'telegram_stars' AND external_id = :chg
                """
            ),
            {"chg": chg},
        ).scalar_one()
    assert bal2 == start_balance + pack.messages  # баланс не изменился на повторе
    assert n == 1  # ровно одна строка транзакции
