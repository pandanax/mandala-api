"""Интеграционные тесты сервиса квот (тикет 7; нужны ``DATABASE_URL`` и Alembic)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.billing_period import current_billing_period
from mandala.db.engine import create_engine_from_env
from mandala.repositories import UsageRepository
from mandala.services.quota import (
    REASON_LIMIT_EXCEEDED,
    RESOURCE_IMAGE_GENERATION,
    RESOURCE_TEXT_REPLY,
    QuotaService,
)

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


def _insert_user(conn: Connection, *, vertical_id: str = "astrology") -> UUID:
    uid = uuid4()
    pid = _free_plan_id(conn)
    conn.execute(
        text(
            """
            INSERT INTO users (id, vertical_id, current_plan_id)
            VALUES (:id, :vid, :pid)
            """
        ),
        {"id": uid, "vid": vertical_id, "pid": pid},
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


def test_consume_parallel_does_not_exceed_monthly_limit(engine: Engine) -> None:
    """Несколько параллельных ``consume`` не превышают лимит ``plan_limits`` за период."""
    narrow_limit = 7
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE plan_limits
                SET limit_per_period = :lim
                WHERE plan_id = (SELECT id FROM plans WHERE name = 'free')
                  AND resource = :res
                  AND period = 'month'::plan_limit_period
                """
            ),
            {"lim": narrow_limit, "res": RESOURCE_TEXT_REPLY},
        )

    try:
        uid: UUID | None = None
        with engine.begin() as conn:
            uid = _insert_user(conn)
        assert uid is not None

        def one_consume() -> bool:
            with engine.begin() as c:
                return (
                    QuotaService(c)
                    .consume(
                        user_id=uid,
                        vertical_id="astrology",
                        resource=RESOURCE_TEXT_REPLY,
                    )
                    .allowed
                )

        attempts = 40
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(one_consume) for _ in range(attempts)]
            oks = sum(1 for f in as_completed(futures) if f.result())

        assert oks == narrow_limit

        period = current_billing_period()
        with engine.begin() as conn:
            locked = UsageRepository(conn).fetch_count_for_update(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource=RESOURCE_TEXT_REPLY,
            )
            assert locked == narrow_limit
    finally:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE plan_limits
                    SET limit_per_period = 20
                    WHERE plan_id = (SELECT id FROM plans WHERE name = 'free')
                      AND resource = :res
                      AND period = 'month'::plan_limit_period
                    """
                ),
                {"res": RESOURCE_TEXT_REPLY},
            )


def test_image_generation_limit_zero_denies_and_counter_stays_zero(engine: Engine) -> None:
    """Лимит 0 для ``image_generation`` на ``free``: отказ, счётчик не растёт."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            uid = _insert_user(conn)
            svc = QuotaService(conn)
            r = svc.consume(
                user_id=uid,
                vertical_id="astrology",
                resource=RESOURCE_IMAGE_GENERATION,
            )
            assert not r.allowed
            assert r.reason == REASON_LIMIT_EXCEEDED

            period = current_billing_period()
            cnt = UsageRepository(conn).fetch_count_for_update(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource=RESOURCE_IMAGE_GENERATION,
            )
            assert cnt == 0
        finally:
            trans.rollback()
