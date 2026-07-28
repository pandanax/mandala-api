"""Интеграция: пустой баланс кошелька пропускает LLM и показывает пикер пакетов."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, handle_inbound
from mandala.repositories import ProfileRepository, WalletRepository
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


def test_empty_balance_skips_llm_and_shows_packs(engine: Engine) -> None:
    """Баланс 0 → LLM не вызывается; пользователю — пикер пакетов (кнопки покупки)."""
    ext = f"balance-empty-{uuid4()}"
    vertical = "astrology"
    event = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="Привет",
    )

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        # Обнуляем стартовый грант, чтобы смоделировать исчерпанный баланс.
        conn.execute(
            text("UPDATE users SET message_balance = 0 WHERE id = :uid"),
            {"uid": uid},
        )
        assert WalletRepository(conn).get_balance(user_id=uid, vertical_id=vertical) == 0
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id=vertical)
        pr.merge_scenario_state(uid, {"intake_complete": True, "intake_step_index": 2})

    class _BoomLlm:
        def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            msg = "LLM must not be called when balance is empty"
            raise AssertionError(msg)

        def close(self) -> None:
            pass

    with engine.begin() as conn:
        out = handle_inbound(event, conn, llm_client=_BoomLlm())

    assert len(out) == 1
    # Пикер пакетов: три кнопки покупки, счёта нет.
    flat = [c["callback_data"] for row in (out[0].buttons or []) for c in row]
    assert flat == ["mdl:pack:100", "mdl:pack:300", "mdl:pack:1000"]
