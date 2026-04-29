"""Интеграционные тесты квоты текстового ответа (тикет 12)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mandala.billing_period import current_billing_period
from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, handle_inbound
from mandala.repositories import ProfileRepository
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


def test_quota_exhausted_skips_llm(engine: Engine) -> None:
    """При count == limit LLM не вызывается; пользователю — понятное сообщение."""
    ext = f"quota-exhaust-{uuid4()}"
    vertical = "astrology"
    event = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="Привет",
    )
    period = current_billing_period()

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        conn.execute(
            text(
                """
                INSERT INTO usage_counters (
                    user_id, vertical_id, billing_period, resource, count
                )
                VALUES (
                    :uid, :vid, :bp, 'text_reply', 20
                )
                ON CONFLICT (user_id, vertical_id, billing_period, resource)
                DO UPDATE SET count = EXCLUDED.count
                """
            ),
            {"uid": uid, "vid": vertical, "bp": period},
        )
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id=vertical)
        pr.merge_scenario_state(
            uid,
            {"intake_complete": True, "intake_step_index": 2},
        )

    class _BoomLlm:
        def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            msg = "LLM must not be called when quota is exhausted"
            raise AssertionError(msg)

        def close(self) -> None:
            pass

    with engine.begin() as conn:
        out = handle_inbound(event, conn, llm_client=_BoomLlm())

    assert len(out) == 1
    text_out = out[0].text or ""
    assert "исчерпан" in text_out.lower() or "лимит" in text_out.lower()
