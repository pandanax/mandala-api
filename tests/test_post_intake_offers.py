"""Сообщение после анкеты: кнопки и ветка «есть натальная карта»."""

from __future__ import annotations

from mandala.verticals.client_knowledge import AGENT_CARD_NATAL_CHART_TEXT
from mandala.verticals.post_intake_offers import post_intake_completion_message


def test_astrology_without_natal_has_core_buttons() -> None:
    m = post_intake_completion_message("astrology", {})
    assert m.buttons
    flat = [c.get("callback_data") for row in (m.buttons or []) for c in row]
    assert "mdl:natal" in flat
    assert "mdl:fc_today" in flat


def test_astrology_with_natal_shows_theme_row() -> None:
    m = post_intake_completion_message(
        "astrology",
        {AGENT_CARD_NATAL_CHART_TEXT: "краткая карта"},
    )
    flat = [c.get("callback_data") for row in (m.buttons or []) for c in row]
    assert "mdl:th_fin" in flat
    assert "mdl:th_rel" in flat
