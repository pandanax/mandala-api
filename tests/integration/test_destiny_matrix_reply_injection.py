"""Интеграция: блок «Карты судьбы» (Матрица Судьбы) доходит до system-промпта LLM.

Доказывает сквозную проводку из :mod:`mandala.services.text_reply`: если в профиле есть
дата рождения, при обычном текстовом ходе астрологии в system-промпт подмешивается
рассчитанный блок Матрицы Судьбы (данные для интерпретации). Требует DATABASE_URL
(как остальные интеграционные тесты); без него пропускается.
"""

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
        return 'ответ-заглушка\n---mandala-nav---\n{"buttons":[]}'

    def close(self) -> None:
        pass


def test_destiny_matrix_block_injected_when_birth_date_present(engine: Engine) -> None:
    """С датой рождения в профиле в system-промпт попадает блок Матрицы Судьбы."""
    ext = f"dm-inject-{uuid4()}"
    vertical = "astrology"
    cap = _CaptureLlm()
    ev = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="разбери мою карту судьбы",
    )
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id=vertical)
        pr.merge_scenario_state(uid, {"intake_complete": True, "intake_step_index": 4})
        pr.merge_agent_card(uid, {"birth_date": "07.01.1987", "full_name": "Тест"})

    with engine.begin() as conn:
        handle_inbound(ev, conn, llm_client=cap, kb_search=None)

    assert cap.last_chat is not None
    sys0 = cap.last_chat[0].content
    # Маркер именно РАССЧИТАННОГО блока (с '===' и датой), а не упоминания в статичном
    # промпте: он появляется только при инжекции данных движком.
    assert "=== РАССЧИТАННАЯ КАРТА СУДЬБЫ (Матрица Судьбы, дата 07.01.1987)" in sys0
    assert "=== КОНЕЦ КАРТЫ СУДЬБЫ ===" in sys0
    # Аркан дня для 07.01.1987 = 7 (Колесница) — числа из движка, не выдуманные.
    assert "7 (Колесница)" in sys0
    # Отдельная система: явный запрет смешения с астрологией присутствует.
    assert "ОТДЕЛЬНАЯ от астрологии" in sys0


def test_no_destiny_block_without_birth_date(engine: Engine) -> None:
    """Без даты рождения блок Матрицы Судьбы не добавляется (мягкая деградация)."""
    ext = f"dm-nobd-{uuid4()}"
    vertical = "astrology"
    cap = _CaptureLlm()
    ev = InboundEvent(
        vertical_id=vertical,
        channel="telegram",
        external_user_id=ext,
        text="привет",
    )
    with engine.begin() as conn:
        uid = UserIdentityService(conn).get_or_create_user(
            vertical_id=vertical,
            channel="telegram",
            external_user_id=ext,
        )
        pr = ProfileRepository(conn)
        pr.ensure_row(user_id=uid, vertical_id=vertical)
        pr.merge_scenario_state(uid, {"intake_complete": True, "intake_step_index": 4})

    with engine.begin() as conn:
        handle_inbound(ev, conn, llm_client=cap, kb_search=None)

    assert cap.last_chat is not None
    # Рассчитанный блок (с '===') не добавляется; упоминание в статичном промпте не в счёт.
    assert "=== РАССЧИТАННАЯ КАРТА СУДЬБЫ" not in cap.last_chat[0].content
