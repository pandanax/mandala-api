"""Тест: кнопки-сферы после LLM-ответов (P2) и новые коды тем (P2)."""

from __future__ import annotations

from mandala.domain.contracts import OutboundMessage
from mandala.domain.handler import _with_sphere_followup
from mandala.verticals.quick_actions import (
    expand_inbound_quick_action,
    sphere_followup_buttons,
)


def test_sphere_followup_buttons_structure() -> None:
    """sphere_followup_buttons возвращает 2 ряда по 3 кнопки."""
    buttons = sphere_followup_buttons()
    assert len(buttons) == 2
    assert all(len(row) == 3 for row in buttons)
    # Все callback_data известны системе
    all_callbacks = {btn["callback_data"] for row in buttons for btn in row}
    assert "mdl:th_personality" in all_callbacks
    assert "mdl:th_career" in all_callbacks
    assert "mdl:th_partner" in all_callbacks
    assert "mdl:th_rel" in all_callbacks
    assert "mdl:th_fin" in all_callbacks
    assert "mdl:th_health" in all_callbacks


def test_sphere_followup_buttons_each_has_text_and_callback() -> None:
    buttons = sphere_followup_buttons()
    for row in buttons:
        for btn in row:
            assert "text" in btn and btn["text"]
            assert "callback_data" in btn and btn["callback_data"]


def test_with_sphere_followup_adds_buttons_to_text_reply() -> None:
    """_with_sphere_followup добавляет inline-кнопки если их ещё нет."""
    messages = [OutboundMessage(text="Разбор карты: Солнце в Рыбах...")]
    result = _with_sphere_followup(messages, "astrology")
    assert result[-1].buttons is not None
    assert len(result[-1].buttons) == 2


def test_with_sphere_followup_does_not_override_existing_buttons() -> None:
    """_with_sphere_followup не трогает сообщение, у которого уже есть buttons."""
    existing_buttons = [[{"text": "A", "callback_data": "x"}]]
    messages = [OutboundMessage(text="Ответ", buttons=existing_buttons)]
    result = _with_sphere_followup(messages, "astrology")
    assert result[-1].buttons == existing_buttons


def test_with_sphere_followup_skips_empty_text() -> None:
    """Если текст пустой — кнопки не добавляем."""
    messages = [OutboundMessage(text=None)]
    result = _with_sphere_followup(messages, "astrology")
    assert result[-1].buttons is None


def test_with_sphere_followup_skips_non_astrology() -> None:
    """Для вертикали therapy кнопки сфер не добавляются."""
    messages = [OutboundMessage(text="Разбор")]
    result = _with_sphere_followup(messages, "therapy")
    assert result[-1].buttons is None


def test_new_sphere_codes_expand_correctly() -> None:
    """Новые коды mdl:th_* разворачиваются в полный промпт для LLM."""
    for code in ("mdl:th_personality", "mdl:th_career", "mdl:th_partner"):
        expanded = expand_inbound_quick_action("astrology", code)
        assert expanded is not None
        assert expanded != code
        assert len(expanded) > 20, f"Промпт для {code} слишком короткий"


def test_sphere_codes_mention_natal_chart() -> None:
    """Промпты сфер упоминают натальную карту — они опираются на сохранённые данные."""
    for code in ("mdl:th_personality", "mdl:th_career", "mdl:th_partner"):
        expanded = expand_inbound_quick_action("astrology", code)
        assert expanded is not None
        assert "карт" in expanded.lower(), f"Промпт для {code} не упоминает карту"
