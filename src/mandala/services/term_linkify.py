"""Детерминированный линкификатор нумерологических терминов «Карты судьбы».

Механизм кликабельных терминов (см. :mod:`mandala.services.nav_protocol`) для обычных
астрологических ответов рождается из nav-блока LLM (``terms``). Но детерминированный
рендер ``/matrix`` (:mod:`mandala.services.chart_render`) НЕ проходит через LLM —
поэтому термины в нём сами по себе не кликабельны. Этот модуль закрывает пробел: он
проходит по известному глоссарию нумерологических терминов (22 Старших Аркана + позиции
октаграммы + линии + чакры — источник истины :mod:`mandala.astro.destiny_matrix`) и
превращает вхождение каждого термина в term-link, ПЕРЕИСПОЛЬЗУЯ ту же схему, что и
LLM-навигация: карта ``nav_map`` (``id -> запрос-объяснение``, сохраняется в
``agent_card``) + ``term_links`` (``{term, payload}``). Клик по термину → Telegram шлёт
``/start mdlnav_<id>`` → :func:`mandala.services.nav_protocol.resolve_nav_action`
достаёт запрос → обычный ход LLM объясняет термин и, как всегда, добавляет инлайн-навигацию.

Так «ВСЕ термины кликабельны» покрывается ДЕТЕРМИНИРОВАННО (гарантия, а не на усмотрение
модели), в дополнение к nav-блоку модели для прозаических ответов.

Матчинг осторожный и детерминированный: регистрозависимый, с кириллическими границами
слова (чтобы «Император» не подсветился внутри «Императрица», а короткий аркан «Суд» —
внутри «судьбы»), первое вхождение каждого термина, без пересечений (более длинный
термин побеждает). Пустой результат → безопасная деградация (термины остаются обычным
текстом, ничего не падает).
"""

from __future__ import annotations

import re

from mandala.astro.destiny_matrix import ARCANA_NAMES, CHAKRAS_TOP_DOWN
from mandala.services.nav_protocol import NAV_DEEPLINK_PREFIX

# Границы слова с учётом кириллицы: термин матчится, только если НЕ окружён буквой/цифрой
# (иначе «Император» подсветился бы внутри «Императрица»). Регистрозависимость + эти
# границы вместе гарантируют, что «Суд» не совпадёт внутри «судьбы».
_WORD_CHAR = r"0-9A-Za-zА-Яа-яЁё_"
_BOUNDARY_BEFORE = rf"(?<![{_WORD_CHAR}])"
_BOUNDARY_AFTER = rf"(?![{_WORD_CHAR}])"


def _concept_query(term: str) -> str:
    return (
        f"Объясни простыми словами, что такое «{term}» в моей Карте судьбы "
        "(Матрице Судьбы) и что это значит именно для меня."
    )


def _arcana_query(name: str) -> str:
    return (
        f"Что означает аркан «{name}» в моей Карте судьбы (Матрице Судьбы)? Объясни "
        "простыми словами, что это такое и как он проявляется у меня."
    )


def _chakra_query(name: str) -> str:
    return (
        f"Что означает чакра «{name}» в моей Карте судьбы (карте здоровья)? Объясни "
        "простыми словами, что это такое и на что влияет."
    )


# Позиции октаграммы, линии и каналы — термины, которые встречаются в рендере /matrix и в
# прозаических ответах про Матрицу Судьбы. Порядок не важен (пересечения разрешаются по
# длине), но более длинные/специфичные термины перечислены для наглядности первыми.
_CONCEPT_TERMS: tuple[str, ...] = (
    "Матрица Судьбы",
    "Личностный квадрат",
    "Родовой квадрат",
    "Зона комфорта",
    "Кармическая задача",
    "Кармический хвост",
    "Материальная карма",
    "Таланты рода",
    "Портрет",
    "Предназначение",
    "Родовые линии",
    "Мужская линия",
    "Женская линия",
    "Денежный канал",
    "Канал отношений",
    "Карта здоровья",
    "Деньги",
    "Отношения",
)


def _build_glossary() -> tuple[tuple[str, str], ...]:
    """Глоссарий ``(термин, запрос-объяснение)``: концепты + 22 аркана + 7 чакр."""
    items: list[tuple[str, str]] = [(t, _concept_query(t)) for t in _CONCEPT_TERMS]
    items.extend((name, _arcana_query(name)) for name in ARCANA_NAMES.values())
    items.extend((name, _chakra_query(name)) for name in CHAKRAS_TOP_DOWN)
    return tuple(items)


_GLOSSARY: tuple[tuple[str, str], ...] = _build_glossary()


def linkify_numerology_terms(text: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Найти нумерологические термины в ``text`` → ``(term_links, nav_map)``.

    ``term_links`` — ``[{"term": <точная подстрока текста>, "payload": "mdlnav_t<i>"}]``
    для канало-специфичной подсветки (Telegram — inline deep-link). ``nav_map`` —
    ``{"t<i>": <запрос-объяснение>}`` для сохранения в ``agent_card`` (при клике его
    достаёт :func:`resolve_nav_action`). Термины не пересекаются, берётся ПЕРВОЕ
    вхождение каждого (без визуального дублирования), при пересечении побеждает более
    длинный. Ничего не найдено → ``([], {})`` (безопасная деградация).
    """
    if not text:
        return [], {}

    # Первое вхождение каждого термина (регистрозависимо, по границам слова).
    spans: list[tuple[int, int, str, str]] = []
    for term, query in _GLOSSARY:
        m = re.search(_BOUNDARY_BEFORE + re.escape(term) + _BOUNDARY_AFTER, text)
        if m is not None:
            spans.append((m.start(), m.end(), m.group(0), query))

    # Разрешить пересечения: по позиции, при равной позиции — длиннее вперёд; жадно
    # выбираем непересекающиеся, идя слева направо.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    term_links: list[dict[str, str]] = []
    nav_map: dict[str, str] = {}
    last_end = -1
    for start, end, term, query in spans:
        if start < last_end:
            continue
        nav_id = f"t{len(term_links)}"
        nav_map[nav_id] = query
        term_links.append({"term": term, "payload": f"{NAV_DEEPLINK_PREFIX}{nav_id}"})
        last_end = end

    return term_links, nav_map


__all__ = ["linkify_numerology_terms"]
