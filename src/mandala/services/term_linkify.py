"""Детерминированные кнопки-термины «Карты судьбы» для рендера ``/matrix``.

Механизм кликабельных терминов (см. :mod:`mandala.services.nav_protocol`) для обычных
астрологических ответов рождается из nav-блока LLM (``terms``). Но детерминированный
рендер ``/matrix`` (:mod:`mandala.services.chart_render`) НЕ проходит через LLM —
поэтому термины в нём сами по себе не кликабельны. Этот модуль закрывает пробел: он
проходит по известному глоссарию нумерологических терминов (22 Старших Аркана + позиции
октаграммы + линии + чакры — источник истины :mod:`mandala.astro.destiny_matrix`) и
выносит НАЙДЕННЫЕ термины НАДЁЖНЫМИ inline-callback кнопками ``mdl:nav:<id>`` под
сообщением, ПЕРЕИСПОЛЬЗУЯ ту же схему, что и LLM-навигация (:func:`build_term_buttons`):
карта ``nav_map`` (``id -> запрос-объяснение``, сохраняется в ``agent_card``). Клик по
кнопке → :func:`mandala.services.nav_protocol.resolve_nav_action` достаёт запрос →
обычный ход LLM объясняет термин и добавляет инлайн-навигацию.

Инлайн-ТЕКСТ кликабельным в Telegram сделать нельзя (авто-линкуются только слэш-команды),
а Telegram-deep-link ``t.me/<bot>?start=<payload>`` НЕ доставляет start-payload в уже
открытый чат (returning users) — поэтому термины НЕ рендерятся ссылками в тексте, только
кнопками (решение капитана). Показываем лишь 2–5 самых базовых/ключевых терминов
(:func:`build_term_buttons` обрежет), остальные остаются обычным текстом.

Матчинг осторожный и детерминированный: регистрозависимый, с кириллическими границами
слова (чтобы «Император» не совпал внутри «Императрица», а короткий аркан «Суд» — внутри
«судьбы»), первое вхождение каждого термина, без пересечений (более длинный термин
побеждает). Пустой результат → безопасная деградация (термины остаются обычным текстом).
"""

from __future__ import annotations

import re

from mandala.astro.destiny_matrix import ARCANA_NAMES, CHAKRAS_TOP_DOWN
from mandala.services.nav_protocol import build_term_buttons

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
# прозаических ответах про Матрицу Судьбы. Порядок ВАЖЕН: при обрезке до 2–5 кнопок
# берутся первые найденные (см. build_term_buttons), поэтому самые базовые/ключевые
# термины перечислены первыми.
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


def numerology_term_buttons(text: str) -> tuple[list[list[dict[str, str]]], dict[str, str]]:
    """Найти нумерологические термины в ``text`` → ``(кнопки, nav_map)``.

    Возвращает ряды inline-callback кнопок «объясни термин» (``mdl:nav:<id>``, до 2–5
    самых первых/ключевых терминов) и ``nav_map`` (``{"t<i>": <запрос-объяснение>}``) для
    сохранения в ``agent_card`` — при клике его достанет
    :func:`mandala.services.nav_protocol.resolve_nav_action`. Термины НЕ рендерятся
    ссылками в тексте (инлайн-текст в Telegram кликать нельзя). Термины не пересекаются,
    берётся ПЕРВОЕ вхождение каждого (без дублей), при пересечении побеждает более длинный.
    Ничего не найдено → ``([], {})`` (безопасная деградация).
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
    # выбираем непересекающиеся, идя слева направо (первые термины текста — важнее).
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    ordered: list[tuple[str, str]] = []
    last_end = -1
    for start, end, term, query in spans:
        if start < last_end:
            continue
        ordered.append((term, query))
        last_end = end

    # Кнопки строит общий билдер (кап 2–5, id t<i>, callback mdl:nav:<id>).
    return build_term_buttons(ordered)


__all__ = ["numerology_term_buttons"]
