"""Интеграционные тесты репозиториев (тикет 5; нужен ``DATABASE_URL`` и накат Alembic)."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.billing_period import current_billing_period
from mandala.db.engine import create_engine_from_env
from mandala.repositories import (
    ArtifactRepository,
    MessageRepository,
    PlanLimitsRepository,
    ProfileRepository,
    UsageRepository,
    UserChannelRepository,
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


def _insert_user(conn: Connection, *, vertical_id: str = "astrology") -> tuple[UUID, str]:
    """``users`` + ``channel_links``; возвращает ``user_id`` и ``external_user_id``."""
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
    return uid, ext


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def test_user_channel_find_and_profile_merge(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            uid, ext = _insert_user(conn, vertical_id="astrology")
            uc = UserChannelRepository(conn)
            assert (
                uc.find_user_id(vertical_id="astrology", channel="telegram", external_user_id=ext)
                == uid
            )
            assert (
                uc.find_user_id(vertical_id="therapy", channel="telegram", external_user_id=ext)
                is None
            )

            profiles = ProfileRepository(conn)
            profiles.ensure_row(user_id=uid, vertical_id="astrology")
            profiles.merge_scenario_state(uid, {"step": 1, "nested": {"a": 1}})
            profiles.merge_scenario_state(uid, {"nested": {"b": 2}})
            row = profiles.get_by_user_id(uid)
            assert row is not None
            assert row.scenario_state["step"] == 1
            assert row.scenario_state["nested"] == {"b": 2}
        finally:
            trans.rollback()


def test_messages_artifacts_and_plan_limits(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            uid, _ext = _insert_user(conn)
            profiles = ProfileRepository(conn)
            profiles.ensure_row(user_id=uid, vertical_id="astrology")

            msg = MessageRepository(conn)
            mid = msg.insert(
                user_id=uid,
                vertical_id="astrology",
                role="user",
                content_text="привет",
                content_kind="text",
            )
            mid2 = msg.insert(
                user_id=uid,
                vertical_id="astrology",
                role="assistant",
                content_text="ответ",
                content_kind="text",
            )
            art = ArtifactRepository(conn)
            aid = art.insert(
                user_id=uid,
                vertical_id="astrology",
                kind="text_recommendation",
                payload={"summary": "развёрнуто"},
                source_message_id=mid2,
            )
            assert isinstance(aid, UUID)

            recent = msg.list_recent(user_id=uid, vertical_id="astrology", limit=10)
            assert len(recent) == 2
            assert {m.id for m in recent} == {mid, mid2}
            keys = [(m.created_at, m.id) for m in recent]
            assert keys == sorted(keys, reverse=True)

            pid = _free_plan_id(conn)
            limits = PlanLimitsRepository(conn).list_for_plan(pid)
            resources = {x.resource: x.limit_per_period for x in limits}
            assert resources["text_reply"] == 20
            assert resources["image_generation"] == 0
        finally:
            trans.rollback()


def test_usage_try_increment_and_for_update(engine: Engine) -> None:
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            uid, _ext = _insert_user(conn)
            period = current_billing_period()
            usage = UsageRepository(conn)
            lim = 3
            assert usage.try_increment(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource="text_reply",
                limit=lim,
            )
            assert usage.try_increment(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource="text_reply",
                limit=lim,
            )
            assert usage.try_increment(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource="text_reply",
                limit=lim,
            )
            assert not usage.try_increment(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource="text_reply",
                limit=lim,
            )
            locked = usage.fetch_count_for_update(
                user_id=uid,
                vertical_id="astrology",
                billing_period=period,
                resource="text_reply",
            )
            assert locked == lim
        finally:
            trans.rollback()
