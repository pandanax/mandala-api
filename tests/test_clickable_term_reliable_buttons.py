"""Регресс: клик по термину раскрывает термин кнопкой, а НЕ вызывает /start.

БОЕВОЙ БАГ (был на проде, `7dd8b47`): по клику на кликабельный термин в тексте
(например «Луна во Льве» из обычного LLM-ответа, или термин из детерминированного
`/matrix`) срабатывала команда `/start` — мягкий рестарт анкеты, а не раскрытие термина.

Диагностика (разделяем три факта):
- **Триггер:** клик по термину, который был кликабелен ТОЛЬКО как inline deep-link
  ``t.me/<bot>?start=mdlnav_<id>`` (инлайн-текст в Telegram кликабельным иначе не сделать).
- **Маскирующее условие (причина B):** Telegram НЕ доставляет start-payload в уже
  открытый чат (returning users) — iOS на повторном тапе открывает чат без payload,
  Desktop показывает кнопку START, WebK payload не поддерживает вовсе
  (bugs.telegram.org/c/8830, tdesktop#27064, Telegram-iOS#1100). Тогда в бота приходит
  ГОЛЫЙ ``/start`` без payload.
- **Видимый симптом:** ``resolve_nav_action("/start", nav_map)`` не находит id →
  ``_extract_command("/start")`` → мягкий рестарт анкеты.

Фикс (решение капитана): полностью отказаться от term-as-link; выносить 2–5 самых
базовых/ключевых терминов НАДЁЖНЫМИ inline-callback кнопками ``mdl:nav:<id>``
(``callback_query`` доставляется всегда), остальные термины — обычный текст. Работает и
для LLM-ответов (:func:`assign_ids`), и для детерминированного ``/matrix``
(:func:`_matrix_message_with_clickable_terms`). Всё офлайн: без сети, БД и LLM.
"""

from __future__ import annotations

from uuid import uuid4

from mandala.services.nav_protocol import (
    NAV_CALLBACK_PREFIX,
    NAV_MARKER,
    assign_ids,
    build_term_buttons,
    resolve_nav_action,
    split_llm_nav_suffix,
)
from mandala.services.scenario_intake import (
    _extract_command,
    _matrix_message_with_clickable_terms,
)

# Обычный LLM-ответ астрологии с термином «Луна во Льве» в nav-блоке (точный кейс капитана).
_TERM_BLOCK = (
    "Луна во Льве даёт яркость и потребность в признании.\n"
    f"{NAV_MARKER}\n"
    '{"buttons":[{"label":"➡️ Продолжить","q":"Продолжи разбор Луны"}],'
    '"terms":[{"term":"Луна во Льве","q":"Что такое Луна во Льве в моей карте?"}]}'
)


class _FakeProfiles:
    """Минимальный дубль ``ProfileRepository`` — ловит ``merge_agent_card``."""

    def __init__(self) -> None:
        self.merged: dict[str, object] = {}

    def merge_agent_card(self, user_id: object, patch: dict[str, object]) -> None:
        self.merged.update(patch)


def _cells(buttons: list[list[dict[str, str]]] | None) -> list[dict[str, str]]:
    return [c for row in (buttons or []) for c in row]


# --- LLM-путь (астрология): термин → надёжная callback-кнопка -------------------------


def test_llm_term_is_reliable_callback_button_that_resolves() -> None:
    """«Луна во Льве» из LLM-ответа доступна НАДЁЖНОЙ callback-кнопкой, а не ссылкой."""
    _, spec = split_llm_nav_suffix(_TERM_BLOCK)
    assert spec is not None
    render = assign_ids(spec)

    # Термин доступен callback-кнопкой ``mdl:nav:t0`` c подписью «📖 <term>».
    term_buttons = [
        c for c in _cells(render.buttons) if c["callback_data"] == f"{NAV_CALLBACK_PREFIX}t0"
    ]
    assert len(term_buttons) == 1, "у термина должна быть надёжная callback-кнопка"
    assert term_buttons[0]["text"] == "📖 Луна во Льве"

    # Клик по кнопке резолвится в запрос-объяснение (обычный ход LLM), а НЕ в /start.
    query = resolve_nav_action(term_buttons[0]["callback_data"], render.nav_map)
    assert query is not None and "Луна во Льве" in query

    # Кнопка навигации «куда дальше» тоже на месте (не сломали существующее).
    assert f"{NAV_CALLBACK_PREFIX}n0" in [c["callback_data"] for c in _cells(render.buttons)]


def test_llm_terms_capped_at_five() -> None:
    """Из ответа с многими терминами в кнопки уходит не больше 5 (остальные — текст)."""
    terms = ",".join(f'{{"term":"Термин {i}","q":"объясни термин {i}"}}' for i in range(9))
    block = f'Текст.\n{NAV_MARKER}\n{{"terms":[{terms}]}}'
    _, spec = split_llm_nav_suffix(block)
    assert spec is not None
    render = assign_ids(spec)
    prefix = f"{NAV_CALLBACK_PREFIX}t"
    term_cells = [c for c in _cells(render.buttons) if c["callback_data"].startswith(prefix)]
    assert len(term_cells) == 5


# --- Детерминированный /matrix: термины → надёжные callback-кнопки ---------------------


def test_matrix_terms_are_reliable_callback_buttons_that_resolve() -> None:
    """Термины рендера ``/matrix`` кликабельны надёжными callback-кнопками и резолвятся."""
    from mandala.astro.destiny_matrix import compute_destiny_matrix

    dm = compute_destiny_matrix("29.01.1991")
    profiles = _FakeProfiles()
    msg = _matrix_message_with_clickable_terms(profiles, uuid4(), dm)  # type: ignore[arg-type]

    nav_map = profiles.merged.get("nav_map")
    assert isinstance(nav_map, dict) and nav_map

    term_callbacks = [
        c["callback_data"]
        for c in _cells(msg.buttons)
        if c["callback_data"].startswith(f"{NAV_CALLBACK_PREFIX}t")
    ]
    assert term_callbacks, "у терминов /matrix должны быть callback-кнопки"
    assert len(term_callbacks) <= 5  # кап 2–5
    for cb in term_callbacks:
        assert resolve_nav_action(cb, nav_map) is not None

    # Штатная навигация рендера сохранена ПОД кнопками-терминами (не потеряли переходы).
    assert "mdl:matrix" in [c["callback_data"] for c in _cells(msg.buttons)]


# --- Корневая причина (B) и контрфакт: голый /start vs надёжная кнопка ------------------


def test_dropped_start_payload_restarts_intake_but_callback_button_does_not() -> None:
    """Контрфакт: голый ``/start`` (Telegram потерял payload) рестартит анкету; кнопка — нет.

    Одно изменение переключает исход: тот же термин, доставленный как inline deep-link с
    ПОТЕРЯННЫМ payload (``/start``), уходит в рестарт; тот же термин, доставленный как
    callback-кнопка (``mdl:nav:t0``), раскрывается. Это и есть причина бага и её фикс.
    """
    nav_map = {"t0": "Что такое Луна во Льве в моей карте?"}

    # Сломанный путь: returning user, Telegram выкинул payload → пришёл голый /start.
    assert resolve_nav_action("/start", nav_map) is None  # deep-link не донёс id
    assert _extract_command("/start") == "/start"  # → мягкий рестарт анкеты (симптом)

    # Надёжный путь: та же семантика через callback-кнопку — раскрытие, без рестарта.
    assert resolve_nav_action("mdl:nav:t0", nav_map) == "Что такое Луна во Льве в моей карте?"
    assert _extract_command("mdl:nav:t0") is None  # это не команда → не рестарт


def test_build_term_buttons_shape_and_degradation() -> None:
    """Единый билдер: (термин, запрос) → callback ``mdl:nav:t<i>`` c подписью «📖 …»."""
    rows, nav_map = build_term_buttons(
        [("Луна во Льве", "объясни Луну"), ("Асцендент", "объясни Асцендент")]
    )
    flat = [c for row in rows for c in row]
    assert [c["callback_data"] for c in flat] == [
        f"{NAV_CALLBACK_PREFIX}t0",
        f"{NAV_CALLBACK_PREFIX}t1",
    ]
    assert all(c["text"].startswith("📖 ") for c in flat)
    assert nav_map == {"t0": "объясни Луну", "t1": "объясни Асцендент"}
    # Пустой термин/запрос молча пропускается; пустой вход → пусто.
    assert build_term_buttons([("", "q"), ("term", "")]) == ([], {})
    assert build_term_buttons([]) == ([], {})
