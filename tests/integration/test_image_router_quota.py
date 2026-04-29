"""Интеграция: роутер картинка vs текст, квота ``image_generation``, артефакты (14–15)."""

from __future__ import annotations

import os
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, handle_inbound
from mandala.llm.image_generation import ImageGenerationResult
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


def _prime_intake_done(conn: Connection, *, uid: UUID, vertical: str) -> None:
    ProfileRepository(conn).ensure_row(user_id=uid, vertical_id=vertical)
    ProfileRepository(conn).merge_scenario_state(
        uid,
        {"intake_complete": True, "intake_step_index": 2},
    )


def test_image_intent_free_plan_does_not_call_image_client(engine: Engine) -> None:
    """У free в seed ``image_generation`` = 0 — ``generate`` заглушки не вызывается."""
    ext = f"img-router-free-{uuid4()}"
    vertical = "astrology"

    class _BoomImage:
        def generate(self, prompt: str, *, model: str | None = None) -> NoReturn:
            _ = model
            msg = "image client must not run when image_generation quota is unavailable"
            raise AssertionError(msg)

    ev_image = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="/image космос",
    )
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        _prime_intake_done(conn, uid=uid, vertical=vertical)

    with engine.begin() as conn:
        out = handle_inbound(ev_image, conn, llm_client=None, image_client=_BoomImage())

    assert len(out) == 1
    t = (out[0].text or "").lower()
    assert "недоступ" in t or "лимит" in t or "тариф" in t


def test_text_branch_unaffected_after_image_denied(engine: Engine) -> None:
    """Обычный текст после отказа по картинке — LLM вызывается (текстовая ветка жива)."""
    ext = f"img-then-text-{uuid4()}"
    vertical = "astrology"

    class _StubLlm:
        def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return "ок, текстовый ответ"

        def close(self) -> None:
            pass

    class _BoomImage:
        def generate(self, prompt: str, *, model: str | None = None) -> NoReturn:
            _ = (prompt, model)
            raise AssertionError("image path must not be taken for plain text")

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        _prime_intake_done(conn, uid=uid, vertical=vertical)

    ev_image = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="/image x",
    )
    ev_text = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="Как дела?",
    )
    with engine.begin() as conn:
        out_img = handle_inbound(ev_image, conn, llm_client=_StubLlm(), image_client=_BoomImage())
    with engine.begin() as conn:
        out_txt = handle_inbound(ev_text, conn, llm_client=_StubLlm(), image_client=_BoomImage())

    assert len(out_img) == 1
    assert (
        "недоступ" in (out_img[0].text or "").lower() or "лимит" in (out_img[0].text or "").lower()
    )

    assert len(out_txt) == 1
    assert "текстовый" in (out_txt[0].text or "")


def test_image_success_inserts_generated_artifact(engine: Engine) -> None:
    """Premium и фейк-клиент с URL: запись в ``generated_artifacts``, поле ``photo``."""
    ext = f"img-art-{uuid4()}"
    vertical = "astrology"

    class _FakeImg:
        def generate(self, prompt: str, *, model: str | None = None) -> ImageGenerationResult:
            _ = model
            return ImageGenerationResult(
                prompt_echo=prompt[:80],
                image_url="https://example.invalid/demo.png",
                stub_ref=None,
            )

    ev = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="/image звезды",
    )

    uid: UUID
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        _prime_intake_done(conn, uid=uid, vertical=vertical)
        conn.execute(
            text(
                """
                UPDATE users
                SET current_plan_id = (SELECT id FROM plans WHERE name = 'premium' LIMIT 1)
                WHERE id = :uid
                """
            ),
            {"uid": uid},
        )

    with engine.begin() as conn:
        out = handle_inbound(ev, conn, llm_client=None, image_client=_FakeImg())

    assert len(out) == 1
    assert out[0].photo == "https://example.invalid/demo.png"

    with engine.begin() as conn:
        n = conn.execute(
            text(
                """
                SELECT count(*) FROM generated_artifacts
                WHERE user_id = :uid AND vertical_id = :vid AND kind = 'image'
                """
            ),
            {"uid": uid, "vid": vertical},
        ).scalar_one()
    assert n == 1
