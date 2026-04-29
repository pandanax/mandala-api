"""Интеграционные тесты анкеты по вертикали (тикет 13)."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, OutboundMessage, handle_inbound
from mandala.repositories import ProfileRepository
from mandala.services.scenario_intake import KEY_INTAKE_COMPLETE, KEY_INTAKE_STEP_INDEX
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


def test_invalid_input_does_not_advance_state(engine: Engine) -> None:
    """Невалидный ответ не меняет ``scenario_state`` / ``agent_card``."""
    ext = f"intake-invalid-{uuid4()}"
    vertical = "astrology"
    event_bad = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="я",
    )
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        ProfileRepository(conn).ensure_row(user_id=uid, vertical_id=vertical)

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        out = handle_inbound(event_bad, conn, llm_client=None)
        prof = ProfileRepository(conn).get_by_user_id(uid)
    assert prof is not None
    assert prof.scenario_state.get(KEY_INTAKE_COMPLETE) is not True
    assert int(prof.scenario_state.get(KEY_INTAKE_STEP_INDEX, 0) or 0) == 0
    assert "birth_place" not in prof.agent_card
    assert len(out) == 1
    assert out[0].text


def test_therapy_and_astrology_first_prompts_differ(engine: Engine) -> None:
    """Две тестовые вертикали — разный первый шаг (текст подсказки)."""
    ext_a = f"intake-a-{uuid4()}"
    ext_t = f"intake-t-{uuid4()}"
    bad_short = InboundEvent(
        vertical_id="astrology",
        channel="telegram",
        external_user_id=ext_a,
        text="x",
    )
    bad_therapy = InboundEvent(
        vertical_id="therapy",
        channel="telegram",
        external_user_id=ext_t,
        text="коротко",
    )
    with engine.begin() as conn:
        for ev in (bad_short, bad_therapy):
            uid = UserIdentityService(conn).get_or_create_user(
                vertical_id=ev.vertical_id,
                channel="telegram",
                external_user_id=ev.external_user_id,
            )
            ProfileRepository(conn).ensure_row(user_id=uid, vertical_id=ev.vertical_id)

    with engine.begin() as conn:
        oa = handle_inbound(bad_short, conn)
        ot = handle_inbound(bad_therapy, conn)
    assert oa[0].text != ot[0].text
    assert "рождения" in (oa[0].text or "").lower() or "место" in (oa[0].text or "").lower()
    assert "тем" in (ot[0].text or "").lower() or "описание" in (ot[0].text or "").lower()


def test_full_intake_then_messages_in_db(engine: Engine) -> None:
    """После двух валидных шагов астрологии — анкета закрыта, в ``messages`` есть ответы."""
    ext = f"intake-full-{uuid4()}"
    v = "astrology"

    def run(text: str) -> list[OutboundMessage]:
        ev = InboundEvent(
            vertical_id=v,
            channel="web",
            external_user_id=ext,
            text=text,
        )
        with engine.begin() as conn:
            return handle_inbound(ev, conn, llm_client=_Stub())

    out1 = run("Москва")
    assert "время" in (out1[0].text or "").lower() or "чч:мм" in (out1[0].text or "").lower()
    out2 = run("14:05")
    assert "анкета" in (out2[0].text or "").lower() or "сохран" in (out2[0].text or "").lower()

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=v,
            channel="web",
            external_user_id=ext,
        )
        prof = ProfileRepository(conn).get_by_user_id(uid)
        n_msg = conn.execute(
            text(
                """
                SELECT count(*)::int FROM messages
                WHERE user_id = :uid AND vertical_id = :v
                """
            ),
            {"uid": uid, "v": v},
        ).scalar_one()

    assert prof is not None
    assert prof.scenario_state.get(KEY_INTAKE_COMPLETE) is True
    assert prof.agent_card.get("birth_place") == "Москва"
    assert n_msg >= 2


class _Stub:
    def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return "stub-llm"

    def close(self) -> None:
        pass
