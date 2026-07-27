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
from mandala.services.intake_flow import (
    CB_CONFIRM,
    CB_SAVE,
    KEY_INTAKE_COMPLETE,
    KEY_INTAKE_STEP_INDEX,
)
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
    """Пофазовый сбор: ввод → подтверждение поля → сводка → сохранение; карта в БД."""
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

    # Каждое поле: ввод → эхо «… Верно?» → подтверждение продвигает к следующему.
    out_name = run("Иван Иванов")
    assert "верно" in (out_name[0].text or "").lower()
    assert out_name[0].buttons
    out_after_name = run(CB_CONFIRM)
    assert "дат" in (out_after_name[0].text or "").lower()

    run("17.03.1992")
    out_after_date = run(CB_CONFIRM)
    assert (
        "город" in (out_after_date[0].text or "").lower()
        or "мест" in (out_after_date[0].text or "").lower()
    )

    run("Москва")  # геокодер резолвит город на этапе валидации места
    out_after_place = run(CB_CONFIRM)
    assert (
        "время" in (out_after_place[0].text or "").lower()
        or "чч:мм" in (out_after_place[0].text or "").lower()
    )

    run("14:05")
    out_summary = run(CB_CONFIRM)  # последний confirm → сводка всей анкеты
    assert "проверьте" in (out_summary[0].text or "").lower()
    assert out_summary[0].buttons

    out_saved = run(CB_SAVE)
    assert "сохран" in (out_saved[0].text or "").lower()
    assert out_saved[0].buttons

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
    # Карта и Матрица Судьбы посчитаны и сохранены при сохранении профиля.
    assert isinstance(prof.agent_card.get("natal_chart_data"), dict)
    assert isinstance(prof.agent_card.get("destiny_matrix_data"), dict)
    assert n_msg >= 4


def test_reset_command_clears_profile_and_messages(engine: Engine) -> None:
    """``/reset`` обнуляет ``agent_card``, ``scenario_state`` и удаляет историю сообщений."""
    ext = f"intake-reset-{uuid4()}"
    v = "astrology"

    def run(t: str) -> list[OutboundMessage]:
        ev = InboundEvent(vertical_id=v, channel="web", external_user_id=ext, text=t)
        with engine.begin() as conn:
            return handle_inbound(ev, conn, llm_client=_Stub())

    # Прогоняем анкету целиком (ввод → подтверждение поля → … → сводка → сохранить).
    run("Иван Иванов")
    run(CB_CONFIRM)
    run("17.03.1992")
    run(CB_CONFIRM)
    run("Москва")
    run(CB_CONFIRM)
    run("14:05")
    run(CB_CONFIRM)
    run(CB_SAVE)

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
    # приветствие и первый вопрос анкеты — РАЗНЫМИ сообщениями (не склеены)
    assert len(out_reset) >= 2
    assert "имя" not in text_reset.lower() and "фамил" not in text_reset.lower()
    last_reset = (out_reset[-1].text or "").lower()
    assert "имя" in last_reset or "фамил" in last_reset

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


def test_start_splits_greeting_and_first_question(engine: Engine) -> None:
    """``/start``: приветствие и первый вопрос анкеты — РАЗНЫМИ сообщениями, не склеены."""
    ext = f"intake-start-split-{uuid4()}"
    v = "astrology"
    ev = InboundEvent(vertical_id=v, channel="telegram", external_user_id=ext, text="/start")
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=v, channel="telegram", external_user_id=ext
        )
        ProfileRepository(conn).ensure_row(user_id=uid, vertical_id=v)
    with engine.begin() as conn:
        out = handle_inbound(ev, conn)

    assert len(out) >= 2, "приветствие и вопрос анкеты должны быть разными сообщениями"
    greeting = (out[0].text or "").lower()
    question = (out[-1].text or "").lower()
    # приветствие — отдельным сообщением и БЕЗ вопроса про имя
    assert "mandala" in greeting or "здравствуйте" in greeting
    assert "имя" not in greeting and "фамил" not in greeting
    # первый вопрос анкеты («как обращаться / имя-фамилия») — отдельным сообщением
    assert "имя" in question or "фамил" in question


class _Stub:
    def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return "stub-llm"

    def close(self) -> None:
        pass
