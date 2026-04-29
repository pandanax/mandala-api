"""Интеграционные тесты резолвинга пользователя и handle_inbound (тикет 8)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, handle_inbound
from mandala.services.user_identity import UserIdentityService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL не задан — интеграционные тесты пропущены",
    ),
]


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def test_get_or_create_user_same_id_on_repeat(engine: Engine) -> None:
    """Повторный вызов не создаёт вторую строку ``channel_links``."""
    ext = "ext-ticket8-repeat-1"
    vertical = "astrology"
    channel = "telegram"

    with engine.begin() as conn:
        uid1 = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel=channel,
            external_user_id=ext,
        )

    with engine.begin() as conn:
        uid2 = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel=channel,
            external_user_id=ext,
        )

    assert uid1 == uid2

    with engine.begin() as conn:
        n_links = conn.execute(
            text(
                """
                SELECT count(*)::int
                FROM channel_links
                WHERE vertical_id = :v
                  AND channel = CAST(:c AS channel_type)
                  AND external_user_id = :e
                """
            ),
            {"v": vertical, "c": channel, "e": ext},
        ).scalar_one()
        n_users = conn.execute(
            text("SELECT count(*)::int FROM users WHERE id = :id"),
            {"id": uid1},
        ).scalar_one()

    assert n_links == 1
    assert n_users == 1


def test_handle_inbound_first_and_repeat(engine: Engine) -> None:
    """Первый и повторный заход: один ``user_id``, профиль доступен."""
    ext = "ext-ticket8-handle-1"
    event = InboundEvent(
        vertical_id="therapy",
        channel="web",
        external_user_id=ext,
        locale="ru",
    )

    with engine.begin() as conn:
        out1 = handle_inbound(event, conn)
    with engine.begin() as conn:
        out2 = handle_inbound(event, conn)

    assert len(out1) == 1
    assert len(out2) == 1
    assert "therapy" in (out1[0].text or "")
    assert "web" in (out1[0].text or "")
    assert "Пользователь зарезолвен" in (out1[0].text or "")
    assert "профиль загружен" in (out1[0].text or "")

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id="therapy",
            channel="web",
            external_user_id=ext,
        )
        plan_name = conn.execute(
            text(
                """
                SELECT p.name
                FROM users u
                JOIN plans p ON u.current_plan_id = p.id
                WHERE u.id = :id
                """
            ),
            {"id": uid},
        ).scalar_one()
        n = conn.execute(
            text(
                """
                SELECT count(*)::int
                FROM channel_links
                WHERE vertical_id = 'therapy'
                  AND channel = CAST('web' AS channel_type)
                  AND external_user_id = :e
                """
            ),
            {"e": ext},
        ).scalar_one()

    assert n == 1
    assert plan_name == "free"
