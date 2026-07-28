"""Детерминированная кликабельность нумерологических терминов «Карты судьбы».

Рендер ``/matrix`` не проходит через LLM, поэтому термины делает кликабельными
:mod:`mandala.services.term_linkify`. Проверяем: известные термины (арканы + позиции
октаграммы) реального рендера попадают в ``term_links``/``nav_map`` с payload
``mdlnav_*``; клик по такому payload через
:func:`mandala.services.nav_protocol.resolve_nav_action` возвращает осмысленный
запрос-объяснение (обычный ход LLM). Всё офлайн, без сети и БД.
"""

from __future__ import annotations

from mandala.astro.destiny_matrix import compute_destiny_matrix
from mandala.services.chart_render import render_destiny_matrix_text
from mandala.services.nav_protocol import NAV_DEEPLINK_PREFIX, resolve_nav_action
from mandala.services.term_linkify import linkify_numerology_terms


def test_matrix_render_terms_are_clickable_and_resolve() -> None:
    """Термины реального рендера /matrix кликабельны и резолвятся в запрос-объяснение."""
    dm = compute_destiny_matrix("07.01.1987")
    text = render_destiny_matrix_text(dm)
    term_links, nav_map = linkify_numerology_terms(text)

    assert term_links, "в рендере Карты судьбы должны найтись кликабельные термины"

    # Позиционные термины рендера — обязательно кликабельны.
    linked_terms = {tl["term"] for tl in term_links}
    for expected in ("Личностный квадрат", "Зона комфорта", "Кармическая задача", "Предназначение"):
        assert expected in linked_terms, f"«{expected}» должен быть кликабельным термином"

    # Хотя бы один аркан из ядра карты попал в термины (напр. центр/зона комфорта).
    arcana_names = {dm[k]["name"] for k in ("day", "month", "year", "karma", "comfort_zone")}
    assert linked_terms & arcana_names, "названия арканов из карты должны быть кликабельны"

    # Каждый term — реальная подстрока текста, payload — deep-link mdlnav_*, id есть в nav_map.
    for tl in term_links:
        assert tl["term"] in text
        assert tl["payload"].startswith(NAV_DEEPLINK_PREFIX)
        nav_id = tl["payload"][len(NAV_DEEPLINK_PREFIX) :]
        assert nav_id in nav_map

    # Клик по deep-link термина резолвится в осмысленный follow-up запрос (ход LLM).
    first = term_links[0]
    q = resolve_nav_action(f"/start {first['payload']}", nav_map)
    assert q is not None and len(q) > 10 and "объясни" in q.lower()


def test_no_terms_no_links() -> None:
    """Текст без нумерологических терминов → пустой результат (мягкая деградация)."""
    term_links, nav_map = linkify_numerology_terms("Обычный текст без терминов.")
    assert term_links == []
    assert nav_map == {}
    assert linkify_numerology_terms("") == ([], {})


def test_word_boundaries_avoid_false_matches() -> None:
    """Границы слова: короткий аркан «Суд» не подсвечивается внутри «судьбы»."""
    term_links, _ = linkify_numerology_terms("Разбор Карты судьбы для тебя.")
    assert all(tl["term"] != "Суд" for tl in term_links)


def test_first_occurrence_only_no_duplicate_links() -> None:
    """Повторяющийся термин линкуется один раз (без визуального дублирования)."""
    text = "Зона комфорта важна. Ещё раз про Зона комфорта в карте."
    term_links, _ = linkify_numerology_terms(text)
    assert sum(1 for tl in term_links if tl["term"] == "Зона комфорта") == 1


def test_instant_matrix_helper_attaches_terms_and_persists_nav_map() -> None:
    """Хелпер /matrix вешает term_links на сообщение и сохраняет nav_map в agent_card."""
    from uuid import uuid4

    from mandala.services.scenario_intake import _matrix_message_with_clickable_terms

    class _FakeProfiles:
        def __init__(self) -> None:
            self.merged: dict[str, object] = {}

        def merge_agent_card(self, user_id: object, patch: dict[str, object]) -> None:
            self.merged.update(patch)

    dm = compute_destiny_matrix("29.01.1991")
    profiles = _FakeProfiles()
    uid = uuid4()
    msg = _matrix_message_with_clickable_terms(profiles, uid, dm)  # type: ignore[arg-type]

    assert msg.term_links, "у /matrix сообщения должны быть кликабельные термины"
    assert msg.buttons, "инлайн-навигация под рендером сохраняется"
    nav_map = profiles.merged.get("nav_map")
    assert isinstance(nav_map, dict) and nav_map, "nav_map должен сохраниться в agent_card"
    # Каждый payload разрешается в запрос через сохранённый nav_map (клик по термину).
    for tl in msg.term_links:
        assert resolve_nav_action(f"/start {tl['payload']}", nav_map) is not None


def test_arcana_name_resolves_to_arcana_explanation() -> None:
    """Клик по названию аркана → запрос-объяснение именно про этот аркан."""
    text = "Твой центральный аркан — 3 (Императрица) — про изобилие."
    term_links, nav_map = linkify_numerology_terms(text)
    by_term = {tl["term"]: tl["payload"] for tl in term_links}
    assert "Императрица" in by_term
    q = resolve_nav_action(f"/start {by_term['Императрица']}", nav_map)
    assert q is not None and "Императрица" in q
