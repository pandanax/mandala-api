"""Разворот коротких callback-кодов в текст запроса."""

from __future__ import annotations

from mandala.verticals.quick_actions import expand_inbound_quick_action


def test_expand_astrology_natal() -> None:
    out = expand_inbound_quick_action("astrology", "mdl:natal")
    assert out is not None
    assert "натальн" in out.lower()
    assert out != "mdl:natal"


def test_expand_unknown_code_unchanged() -> None:
    assert expand_inbound_quick_action("astrology", "mdl:zzz") == "mdl:zzz"


def test_expand_therapy() -> None:
    out = expand_inbound_quick_action("therapy", "mdl_th:vent")
    assert out is not None
    assert "выговориться" in out.lower()
