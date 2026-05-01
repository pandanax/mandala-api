"""Тесты блока даты/времени для LLM."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from mandala.services.llm_time_context import build_llm_time_context_block


def test_build_block_fixed_moscow() -> None:
    dt = datetime(2026, 5, 2, 14, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    s = build_llm_time_context_block(now=dt)
    assert "Europe/Moscow" in s
    assert "2026-05-02" in s
    assert "14:30" in s
    assert "2026-05-02T11:30:00Z" in s


def test_invalid_tz_falls_back_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_LLM_CONTEXT_TZ", "Not/A/Real__Zone")
    dt = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    s = build_llm_time_context_block(now=dt)
    assert "UTC" in s
    assert "2026-01-01T00:00:00Z" in s


def test_env_tz_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_LLM_CONTEXT_TZ", "UTC")
    dt = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    s = build_llm_time_context_block(now=dt)
    assert "UTC" in s
    assert "2026-06-15" in s
