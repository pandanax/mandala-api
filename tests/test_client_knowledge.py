"""Парсинг хвоста ``---mandala---`` для ``agent_card``."""

from __future__ import annotations

import json

from mandala.verticals.client_knowledge import (
    AGENT_CARD_NATAL_CHART_TEXT,
    MANDALA_AGENT_CARD_MARKER,
    split_llm_agent_card_suffix,
)


def test_split_removes_suffix_and_returns_patch() -> None:
    body = "Здравствуйте, ваша карта готова."
    payload = {AGENT_CARD_NATAL_CHART_TEXT: "Солнце в Рыбах…"}
    raw = f"{body}\n{MANDALA_AGENT_CARD_MARKER}\n{json.dumps(payload, ensure_ascii=False)}"
    cleaned, patch = split_llm_agent_card_suffix(raw)
    assert cleaned == body
    assert patch.get(AGENT_CARD_NATAL_CHART_TEXT) == "Солнце в Рыбах…"


def test_split_without_marker_returns_empty_patch() -> None:
    raw = "Просто текст без маркера."
    cleaned, patch = split_llm_agent_card_suffix(raw)
    assert cleaned == raw
    assert patch == {}
