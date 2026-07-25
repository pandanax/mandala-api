"""Структурированная навигация из ответа LLM: короткое сообщение + кнопки + термины.

Бот ведёт себя как навигатор-робот, а не собеседник: каждый ответ — короткое
сообщение и динамический набор переходов «следующий шаг», который генерирует сама
модель под текущий контекст. Формат вывода модели (в самом конце ответа, после
опционального agent-card блока ``---mandala---``):

    <короткое сообщение пользователю>
    ---mandala-nav---
    {"buttons":[{"label":"1️⃣ …","q":"…"}], "terms":[{"term":"…","q":"…"}]}

- ``buttons`` — inline-кнопки навигации «следующий шаг» (углубиться / сменить тему /
  вернуться назад). ``label`` — текст кнопки, ``q`` — полный запрос к LLM при нажатии.
- ``terms`` — сущности/термины, которые ДОСЛОВНО встречаются в тексте сообщения
  (например «Луна во Льве»). Канал делает их кликабельными (Telegram — inline
  deep-link ``t.me/<bot>?start=<payload>``); клик → объяснение термина + новая навигация.

Из-за лимита Telegram на ``callback_data`` (≤64 байта) и на start-payload
(≤64 символа ``A-Za-z0-9_-``) полный текст запроса ``q`` в кнопку не влезает. Поэтому
:func:`assign_ids` присваивает каждому переходу короткий id (``n0``/``t0``…) и строит
карту ``id -> q`` (``nav_map``), которую вызывающий код сохраняет в ``agent_card``.
Кнопки несут только ``mdl:nav:n0`` / ``mdlnav_t0``; на клике :func:`resolve_nav_action`
достаёт из ``nav_map`` полный запрос и запускает обычный ход LLM.

Парсер деградирует безопасно: при отсутствии маркера или битом/пустом JSON возвращает
``(текст, None)`` — сообщение показывается без навигации, ничего не падает.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from mandala.verticals.client_knowledge import MANDALA_AGENT_CARD_MARKER

# Маркер блока навигации в конце ответа модели (отдельная строка).
NAV_MARKER = "---mandala-nav---"

# Префикс callback_data для кнопок навигации (Telegram ≤64 байта).
NAV_CALLBACK_PREFIX = "mdl:nav:"

# Префикс start-payload для deep-link кликабельных терминов (Telegram ≤64 симв.).
NAV_DEEPLINK_PREFIX = "mdlnav_"

# Ограничения на размер: защищают от «полотна» и от переполнения лимитов Telegram.
_MAX_BUTTONS = 8
_MAX_TERMS = 8
_MAX_LABEL_CHARS = 48
_MAX_TERM_CHARS = 48
_MAX_QUERY_CHARS = 400
# Сколько кнопок навигации в одном ряду inline-клавиатуры.
_BUTTONS_PER_ROW = 2


@dataclass(frozen=True)
class NavOption:
    """Одна кнопка навигации «следующий шаг»."""

    label: str
    query: str


@dataclass(frozen=True)
class NavTerm:
    """Кликабельный термин в тексте сообщения (deep-link → объяснение)."""

    term: str
    query: str


@dataclass(frozen=True)
class NavSpec:
    """Разобранный блок навигации: кнопки + термины."""

    buttons: tuple[NavOption, ...]
    terms: tuple[NavTerm, ...]


@dataclass(frozen=True)
class NavRender:
    """Готовые к рендеру данные навигации.

    ``nav_map`` — карта ``id -> query`` для сохранения в ``agent_card`` (см. модульный
    docstring). ``buttons`` — ряды inline-клавиатуры. ``term_links`` — элементы
    ``{"term", "payload"}`` для канало-специфичной подсветки терминов.
    """

    nav_map: dict[str, str]
    buttons: list[list[dict[str, str]]]
    term_links: list[dict[str, str]]


def _coerce_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _query_of(item: Mapping[str, object]) -> str:
    """Запрос перехода: основной ключ ``q``, запасной — ``query``."""
    return (_coerce_str(item.get("q")) or _coerce_str(item.get("query")))[:_MAX_QUERY_CHARS]


def _parse_nav_json(tail: str) -> NavSpec | None:
    """Разобрать JSON-тело блока навигации; ``None`` при любой невалидности."""
    if not tail:
        return None
    try:
        parsed = json.loads(tail)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    buttons: list[NavOption] = []
    raw_buttons = parsed.get("buttons")
    if isinstance(raw_buttons, list):
        for item in raw_buttons:
            if len(buttons) >= _MAX_BUTTONS:
                break
            if not isinstance(item, Mapping):
                continue
            label = _coerce_str(item.get("label"))[:_MAX_LABEL_CHARS]
            query = _query_of(item)
            if label and query:
                buttons.append(NavOption(label=label, query=query))

    terms: list[NavTerm] = []
    raw_terms = parsed.get("terms")
    if isinstance(raw_terms, list):
        for item in raw_terms:
            if len(terms) >= _MAX_TERMS:
                break
            if not isinstance(item, Mapping):
                continue
            term = _coerce_str(item.get("term"))[:_MAX_TERM_CHARS]
            query = _query_of(item)
            if term and query:
                terms.append(NavTerm(term=term, query=query))

    if not buttons and not terms:
        return None
    return NavSpec(buttons=tuple(buttons), terms=tuple(terms))


def split_llm_nav_suffix(reply: str) -> tuple[str, NavSpec | None]:
    """Отделить хвост ``---mandala-nav---`` + JSON от текста для пользователя.

    Возвращает ``(текст_для_чата, NavSpec | None)``. При отсутствии маркера — исходный
    текст и ``None``. При наличии маркера блок всегда срезается из текста (даже если
    JSON битый), чтобы не показать пользователю сырой служебный блок.

    Защита от перепутанного порядка: если после nav-блока по ошибке идёт agent-card
    блок ``---mandala---``, он переносится обратно в head — чтобы его смог обработать
    :func:`mandala.verticals.client_knowledge.split_llm_agent_card_suffix`.
    """
    if not reply or NAV_MARKER not in reply:
        return reply, None
    idx = reply.rfind(NAV_MARKER)
    head = reply[:idx].rstrip()
    tail = reply[idx + len(NAV_MARKER) :].strip()

    carry = ""
    if MANDALA_AGENT_CARD_MARKER in tail:
        cut = tail.find(MANDALA_AGENT_CARD_MARKER)
        carry = tail[cut:]
        tail = tail[:cut].strip()

    spec = _parse_nav_json(tail)
    if carry:
        head = f"{head}\n{carry}".strip()
    cleaned = head if head else reply
    return cleaned, spec


def assign_ids(spec: NavSpec) -> NavRender:
    """Присвоить переходам короткие id и собрать ``nav_map`` / кнопки / term_links."""
    nav_map: dict[str, str] = {}
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for i, opt in enumerate(spec.buttons):
        nav_id = f"n{i}"
        nav_map[nav_id] = opt.query
        row.append({"text": opt.label, "callback_data": f"{NAV_CALLBACK_PREFIX}{nav_id}"})
        if len(row) >= _BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    term_links: list[dict[str, str]] = []
    for i, term in enumerate(spec.terms):
        nav_id = f"t{i}"
        nav_map[nav_id] = term.query
        term_links.append({"term": term.term, "payload": f"{NAV_DEEPLINK_PREFIX}{nav_id}"})

    return NavRender(nav_map=nav_map, buttons=rows, term_links=term_links)


def resolve_nav_action(text: str | None, nav_map: Mapping[str, str] | None) -> str | None:
    """Если ``text`` — клик по навигации, вернуть сохранённый запрос из ``nav_map``.

    Распознаёт два источника:
    - callback кнопки навигации: ``mdl:nav:<id>``;
    - deep-link кликабельного термина: ``/start mdlnav_<id>`` (Telegram присылает так
      после клика по ссылке ``t.me/<bot>?start=mdlnav_<id>``).

    Возвращает ``None``, если это не навигация или id отсутствует в карте (устаревшая
    ссылка после сброса) — вызывающий код обрабатывает такой ввод обычным путём.
    """
    if not text or not nav_map:
        return None
    raw = text.strip()

    nav_id: str | None = None
    if raw.startswith(NAV_CALLBACK_PREFIX):
        nav_id = raw[len(NAV_CALLBACK_PREFIX) :].strip()
    elif raw.startswith("/start"):
        parts = raw.split(maxsplit=1)
        if len(parts) == 2:
            payload = parts[1].strip()
            if "@" in payload:  # /start@botname payload — на всякий случай
                payload = payload.split("@", 1)[0]
            if payload.startswith(NAV_DEEPLINK_PREFIX):
                nav_id = payload[len(NAV_DEEPLINK_PREFIX) :].strip()

    if not nav_id:
        return None
    query = nav_map.get(nav_id)
    if isinstance(query, str) and query.strip():
        return query.strip()
    return None
