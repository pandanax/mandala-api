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
    # Первый шаг astrology — full_name (имя/фамилия), у therapy — main_concern (тема).
    assert "имя" in (oa[0].text or "").lower() or "фамил" in (oa[0].text or "").lower()
    assert "тем" in (ot[0].text or "").lower() or "описание" in (ot[0].text or "").lower()


def test_full_intake_then_messages_in_db(engine: Engine) -> None:
    """После всех валидных шагов астрологии — анкета закрыта, в ``messages`` есть ответы."""
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

    # full_name -> birth_date -> birth_place -> birth_time
    out_name = run("Иван Иванов")
    name_text = (out_name[0].text or "").lower()
    assert "дат" in name_text or "дд.мм" in name_text
    out_date = run("17.03.1992")
    date_text = (out_date[0].text or "").lower()
    assert "город" in date_text or "место" in date_text
    out_place = run("Москва")
    place_text = (out_place[0].text or "").lower()
    assert "время" in place_text or "чч:мм" in place_text
    out_time = run("14:05")
    time_text = (out_time[0].text or "").lower()
    assert "анкета" in time_text or "сохран" in time_text or "кнопк" in time_text
    assert out_time[0].buttons

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
    assert prof.agent_card.get("full_name") == "Иван Иванов"
    assert prof.agent_card.get("birth_date") == "17.03.1992"
    assert prof.agent_card.get("birth_place") == "Москва"
    assert n_msg >= 4


def test_reset_command_clears_profile_and_messages(engine: Engine) -> None:
    """``/reset`` обнуляет ``agent_card``, ``scenario_state`` и удаляет историю сообщений."""
    ext = f"intake-reset-{uuid4()}"
    v = "astrology"

    def run(t: str) -> list[OutboundMessage]:
        ev = InboundEvent(vertical_id=v, channel="web", external_user_id=ext, text=t)
        with engine.begin() as conn:
            return handle_inbound(ev, conn, llm_client=_Stub())

    # Прогоняем анкету целиком
    run("Иван Иванов")
    run("17.03.1992")
    run("Москва")
    run("14:05")
    run("Свободный вопрос астрологу")

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=v, channel="web", external_user_id=ext
        )
        prof = ProfileRepository(conn).get_by_user_id(uid)
        n_msg_before = conn.execute(
            text("SELECT count(*)::int FROM messages WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar_one()
    assert prof is not None
    assert prof.scenario_state.get(KEY_INTAKE_COMPLETE) is True
    assert prof.agent_card.get("full_name") == "Иван Иванов"
    assert n_msg_before >= 4

    # Полный сброс
    out_reset = run("/reset")
    assert out_reset and out_reset[0].text
    text_reset = out_reset[0].text or ""
    assert "забыл" in text_reset.lower() or "чистого" in text_reset.lower()

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=v, channel="web", external_user_id=ext
        )
        prof_after = ProfileRepository(conn).get_by_user_id(uid)
        n_msg_after = conn.execute(
            text("SELECT count(*)::int FROM messages WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar_one()
    assert prof_after is not None
    assert prof_after.agent_card == {}
    assert prof_after.scenario_state == {}
    assert n_msg_after == 0


class _Stub:
    def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return "stub-llm"

    def close(self) -> None:
        pass
