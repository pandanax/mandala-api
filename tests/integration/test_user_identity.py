"""Интеграционные тесты резолвинга пользователя и handle_inbound (тикет 8)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, handle_inbound
from mandala.llm import ChatMessage
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


class _StubLLMForIdentity:
    """Фиктивный LLM: без HTTP и без переменных ``LLM_*``."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        return "Тест therapy/web: пользователь зарезолвен, профиль загружен (демо-ответ)."

    def close(self) -> None:
        pass


def test_handle_inbound_first_and_repeat(engine: Engine) -> None:
    """Первый и повторный заход: один ``user_id``; после анкеты — ответ через LLM-стаб."""
    ext = f"ext-ticket8-handle-{uuid4()}"
    stub = _StubLLMForIdentity()
    ev_short = InboundEvent(
        vertical_id="therapy",
        channel="web",
        external_user_id=ext,
        locale="ru",
        text="Привет",
    )
    ev_topic = InboundEvent(
        vertical_id="therapy",
        channel="web",
        external_user_id=ext,
        locale="ru",
        text="Хочу разобраться с тревогой на работе и не выгорать.",
    )
    ev_mood = InboundEvent(
        vertical_id="therapy",
        channel="web",
        external_user_id=ext,
        locale="ru",
        text="устал но спокоен",
    )
    ev_chat = InboundEvent(
        vertical_id="therapy",
        channel="web",
        external_user_id=ext,
        locale="ru",
        text="Как дела?",
    )

    with engine.begin() as conn:
        out0 = handle_inbound(ev_short, conn, llm_client=stub)
    with engine.begin() as conn:
        out1 = handle_inbound(ev_topic, conn, llm_client=stub)
    with engine.begin() as conn:
        out2 = handle_inbound(ev_mood, conn, llm_client=stub)
    with engine.begin() as conn:
        out3 = handle_inbound(ev_chat, conn, llm_client=stub)

    assert len(out0) == 1
    assert "тем" in (out0[0].text or "").lower() or "короче" in (out0[0].text or "").lower()
    assert "настроение" in (out1[0].text or "").lower()
    assert "анкета" in (out2[0].text or "").lower() or "сохран" in (out2[0].text or "").lower()
    assert len(out3) == 1
    assert "therapy" in (out3[0].text or "")
    assert "web" in (out3[0].text or "")
    assert "зарезолвен" in (out3[0].text or "").lower() or "профиль" in (out3[0].text or "").lower()

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
