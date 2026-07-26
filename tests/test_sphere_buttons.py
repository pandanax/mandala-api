"""Тесты кодов тем ``mdl:th_*`` (используются в пост-intake офферах).

Статические кнопки-сферы под каждым LLM-ответом убраны: под ответами теперь только
контекстная навигация модели «куда дальше» (см. ``nav_protocol`` / ``prompts``), а
не общий набор сфер. Здесь проверяем лишь, что коды тем разворачиваются в полные
запросы к LLM (их всё ещё показывает экран после анкеты).
"""

from __future__ import annotations

from mandala.verticals.quick_actions import expand_inbound_quick_action


def test_new_sphere_codes_expand_correctly() -> None:
    """Коды mdl:th_* разворачиваются в полный промпт для LLM."""
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
