"""Интеграция: последние сообщения из ``messages`` в контексте LLM (тикет 17)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

from mandala.db.engine import create_engine_from_env
from mandala.domain import InboundEvent, handle_inbound
from mandala.llm import ChatMessage
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


class _CaptureLlm:
    """Сохраняет последний список сообщений в ``last_chat``."""

    last_chat: list[ChatMessage] | None

    def __init__(self) -> None:
        self.last_chat = None

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.last_chat = list(messages)
        return "stub-reply"

    def close(self) -> None:
        pass


def test_handle_inbound_passes_prior_messages_to_llm(engine: Engine) -> None:
    """После одного обмена второй запрос видит первую пару user/assistant в ``chat``."""
    ext = f"dlg-mem-{uuid4()}"
    vertical = "astrology"
    cap = _CaptureLlm()

    ev1 = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="первое сообщение",
    )
    ev2 = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="второе сообщение",
    )

    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id=vertical)
        pr.merge_scenario_state(
            uid,
            {"intake_complete": True, "intake_step_index": 2},
        )

    with engine.begin() as conn:
        handle_inbound(ev1, conn, llm_client=cap)

    assert cap.last_chat is not None
    assert len(cap.last_chat) == 2
    assert cap.last_chat[0].role == "system"
    assert cap.last_chat[1].role == "user"
    assert cap.last_chat[1].content == "первое сообщение"

    with engine.begin() as conn:
        handle_inbound(ev2, conn, llm_client=cap)

    assert cap.last_chat is not None
    assert len(cap.last_chat) == 4
    assert cap.last_chat[0].role == "system"
    assert [m.role for m in cap.last_chat[1:]] == ["user", "assistant", "user"]
    assert cap.last_chat[1].content == "первое сообщение"
    assert cap.last_chat[2].content == "stub-reply"
    assert cap.last_chat[3].content == "второе сообщение"


def test_dialog_summary_in_system_prompt(engine: Engine) -> None:
    """``scenario_state["dialog_summary"]`` попадает в первый system-сегмент."""
    ext = f"dlg-sum-{uuid4()}"
    vertical = "astrology"
    cap = _CaptureLlm()
    ev = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="вопрос",
    )
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id=vertical)
        pr.merge_scenario_state(
            uid,
            {
                "intake_complete": True,
                "intake_step_index": 2,
                "dialog_summary": "Пользователь интересуется карьерой.",
            },
        )

    with engine.begin() as conn:
        handle_inbound(ev, conn, llm_client=cap)

    assert cap.last_chat is not None
    sys0 = cap.last_chat[0].content
    assert "Пользователь интересуется карьерой" in sys0
    assert "Ранее в беседе (сводка):" in sys0
