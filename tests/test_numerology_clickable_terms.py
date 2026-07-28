"""Детерминированные кнопки-термины «Карты судьбы».

Рендер ``/matrix`` не проходит через LLM, поэтому термины делает кликабельными
:mod:`mandala.services.term_linkify`. По решению капитана термины НЕ рендерятся ссылками
в тексте (инлайн-текст в Telegram кликать нельзя; deep-link не доходит до returning-users)
— вместо этого 2–5 ключевых терминов выносятся НАДЁЖНЫМИ inline-callback кнопками
``mdl:nav:<id>`` под сообщением. Проверяем: известные термины реального рендера попадают
в кнопки + ``nav_map`` с id ``t*``; клик по кнопке через
:func:`mandala.services.nav_protocol.resolve_nav_action` возвращает осмысленный
запрос-объяснение (обычный ход LLM). Всё офлайн, без сети и БД.
"""

from __future__ import annotations

from mandala.astro.destiny_matrix import compute_destiny_matrix
from mandala.services.chart_render import render_destiny_matrix_text
from mandala.services.nav_protocol import NAV_CALLBACK_PREFIX, resolve_nav_action
from mandala.services.term_linkify import numerology_term_buttons


def _cells(buttons: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    return [c for row in buttons for c in row]


def _labels(buttons: list[list[dict[str, str]]]) -> set[str]:
    # Подпись кнопки — «📖 <term>»; вырезаем префикс, чтобы сравнивать с терминами.
    return {c["text"].removeprefix("📖 ") for c in _cells(buttons)}


def test_matrix_render_terms_are_clickable_buttons_and_resolve() -> None:
    """Термины реального рендера /matrix — кнопки, резолвятся в запрос-объяснение."""
    dm = compute_destiny_matrix("07.01.1987")
    text = render_destiny_matrix_text(dm)
    buttons, nav_map = numerology_term_buttons(text)

    assert buttons, "в рендере Карты судьбы должны найтись кликабельные термины-кнопки"
    cells = _cells(buttons)
    # Кап 2–5 самых базовых/ключевых терминов (решение капитана).
    assert 1 <= len(cells) <= 5

    # Каждая кнопка — надёжный callback mdl:nav:t*, id есть в nav_map, резолвится в запрос.
    for c in cells:
        assert c["callback_data"].startswith(f"{NAV_CALLBACK_PREFIX}t")
        nav_id = c["callback_data"].removeprefix(NAV_CALLBACK_PREFIX)
        assert nav_id in nav_map
        q = resolve_nav_action(c["callback_data"], nav_map)
        assert q is not None and len(q) > 10 and "объясни" in q.lower()

    # Первые/ключевые позиционные термины рендера попали в кнопки.
    labels = _labels(buttons)
    assert labels & {"Личностный квадрат", "Зона комфорта", "Кармическая задача", "Предназначение"}


def test_no_terms_no_buttons() -> None:
    """Текст без нумерологических терминов → пустой результат (мягкая деградация)."""
    buttons, nav_map = numerology_term_buttons("Обычный текст без терминов.")
    assert buttons == []
    assert nav_map == {}
    assert numerology_term_buttons("") == ([], {})


def test_word_boundaries_avoid_false_matches() -> None:
    """Границы слова: короткий аркан «Суд» не подсвечивается внутри «судьбы»."""
    buttons, _ = numerology_term_buttons("Разбор Карты судьбы для тебя.")
    assert "Суд" not in _labels(buttons)


def test_first_occurrence_only_no_duplicate_buttons() -> None:
    """Повторяющийся термин даёт одну кнопку (без дублей)."""
    text = "Зона комфорта важна. Ещё раз про Зона комфорта в карте."
    buttons, _ = numerology_term_buttons(text)
    labels = [c["text"] for c in _cells(buttons)]
    assert labels.count("📖 Зона комфорта") == 1


def test_at_most_five_term_buttons() -> None:
    """Даже если терминов много — показываем не больше 5 кнопок (остальные — текст)."""
    text = (
        "Матрица Судьбы, Личностный квадрат, Родовой квадрат, Зона комфорта, "
        "Кармическая задача, Предназначение, Карта здоровья, Деньги, Отношения."
    )
    buttons, nav_map = numerology_term_buttons(text)
    assert len(_cells(buttons)) == 5
    assert len(nav_map) == 5


def test_instant_matrix_helper_attaches_term_buttons_and_persists_nav_map() -> None:
    """Хелпер /matrix вешает кнопки-термины на сообщение и сохраняет nav_map в agent_card."""
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

    # Термины НЕ рендерятся ссылками в тексте — только кнопками под сообщением.
    assert not hasattr(msg, "term_links") or getattr(msg, "term_links", None) is None
    assert msg.buttons, "у /matrix сообщения должны быть кнопки"
    term_cells = [
        c
        for row in msg.buttons
        for c in row
        if c["callback_data"].startswith(f"{NAV_CALLBACK_PREFIX}t")
    ]
    assert term_cells, "среди кнопок должны быть кнопки-термины mdl:nav:t*"
    nav_map = profiles.merged.get("nav_map")
    assert isinstance(nav_map, dict) and nav_map
    # Клик по каждой кнопке-термину резолвится через сохранённый nav_map.
    for c in term_cells:
        assert resolve_nav_action(c["callback_data"], nav_map) is not None


def test_arcana_name_resolves_to_arcana_explanation() -> None:
    """Клик по кнопке-аркану → запрос-объяснение именно про этот аркан."""
    text = "Твой центральный аркан — 3 (Императрица) — про изобилие."
    buttons, nav_map = numerology_term_buttons(text)
    by_label = {c["text"]: c["callback_data"] for c in _cells(buttons)}
    assert "📖 Императрица" in by_label
    q = resolve_nav_action(by_label["📖 Императрица"], nav_map)
    assert q is not None and "Императрица" in q
