"""Интеграция: кошельковый ``QuotaService`` — старт, списание, атомарность, промо, картинки.

Нужны ``DATABASE_URL`` и применённые миграции (Alembic). Пропускается без ``DATABASE_URL``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.repositories import ProfileRepository, WalletRepository
from mandala.services.message_packs import starting_balance
from mandala.services.quota import (
    RESOURCE_IMAGE_GENERATION,
    RESOURCE_TEXT_REPLY,
    QuotaService,
)
from mandala.services.user_identity import UserIdentityService

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
    ext = f"ext-{uid.hex[:12]}"
    conn.execute(
        text(
            """
            INSERT INTO channel_links (user_id, vertical_id, channel, external_user_id)
            VALUES (:uid, :vid, CAST('telegram' AS channel_type), :ext)
            """
        ),
        {"uid": uid, "vid": vertical_id, "ext": ext},
    )
    return uid


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def test_new_user_gets_starting_balance(engine: Engine) -> None:
    """Разовый стартовый грант при создании пользователя (пакетная модель)."""
    ext = f"start-{uuid4()}"
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id="astrology", channel="telegram", external_user_id=ext
        )
        bal = WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology")
    assert bal == starting_balance()


def test_consume_decrements_until_empty_then_denies(engine: Engine) -> None:
    with engine.begin() as conn:
        uid = _insert_user(conn, balance=3)
    for _ in range(3):
        with engine.begin() as conn:
            assert (
                QuotaService(conn)
                .consume(user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY)
                .allowed
            )
    with engine.begin() as conn:
        denied = QuotaService(conn).consume(
            user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY
        )
        assert denied.allowed is False
        assert WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology") == 0


def test_consume_parallel_does_not_go_negative(engine: Engine) -> None:
    """40 параллельных списаний при балансе 7 → ровно 7 успешных, баланс не уходит в минус."""
    balance = 7
    with engine.begin() as conn:
        uid = _insert_user(conn, balance=balance)

    def one_consume() -> bool:
        with engine.begin() as c:
            return (
                QuotaService(c)
                .consume(user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY)
                .allowed
            )

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(one_consume) for _ in range(40)]
        oks = sum(1 for f in as_completed(futures) if f.result())

    assert oks == balance
    with engine.begin() as conn:
        assert WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology") == 0


def test_image_generation_not_charged_and_denied_without_promo(engine: Engine) -> None:
    """Картинки кошельком не тарифицируются: отказ без промо, баланс не меняется."""
    with engine.begin() as conn:
        uid = _insert_user(conn, balance=5)
        r = QuotaService(conn).consume(
            user_id=uid, vertical_id="astrology", resource=RESOURCE_IMAGE_GENERATION
        )
        assert r.allowed is False
        assert WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology") == 5


def test_promo_is_unlimited_and_does_not_charge(engine: Engine) -> None:
    """Активное промо → безлимит: списаний нет, баланс не меняется (даже нулевой)."""
    with engine.begin() as conn:
        uid = _insert_user(conn, balance=0)
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id="astrology")
        pr.merge_agent_card(uid, {"activated_promo": "TESTME"})

    for _ in range(5):
        with engine.begin() as conn:
            assert (
                QuotaService(conn)
                .consume(user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY)
                .allowed
            )
    with engine.begin() as conn:
        assert WalletRepository(conn).get_balance(user_id=uid, vertical_id="astrology") == 0
