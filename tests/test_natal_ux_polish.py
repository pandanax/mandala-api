"""UX-полировка натальной карты (жалобы капитана):

* технические имена точек (``True_North_Lunar_Node``, ``Chiron``, дома ``First_House``…)
  НЕ протекают ни в детерминированный рендер, ни в system-текст для LLM — латиница/``_``
  в именах точек/планет/аспектов запрещена, непереводимое аккуратно опускается;
* сломанный италик ``_…_`` убран из user-facing рендера (Telegram его не рисует);
* подпись к колесу карты содержит ВРЕМЯ (когда известно) и не содержит имени.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

import mandala.astro.natal_chart as nc
from mandala.astro.natal_chart import (
    is_display_safe_name,
    natal_chart_to_system_text,
    translate_point_name,
)
from mandala.services.chart_render import render_destiny_matrix_text, render_natal_chart_text
from mandala.services.scenario_intake import _natal_wheel_caption

# Прогон ≥3 латинских букв ПОДРЯД или подчёркивание = техническое имя (не должно течь).
_LATIN_TECH = re.compile(r"[A-Za-z_]{3,}")

BIRTH_DATE = "07.01.1987"
BIRTH_TIME = "10:30"
BIRTH_PLACE = "Москва"
GEOCODE = (55.75, 37.62, "Europe/Moscow")


def _chart(system: str = "western") -> dict[str, Any]:
    with patch.object(nc, "_geocode_city", return_value=GEOCODE):
        return nc.calculate_natal_chart(BIRTH_DATE, BIRTH_TIME, BIRTH_PLACE, system=system)


# --- (1) технические имена точек → по-русски, латиница не протекает ----------------


def test_translate_point_name_covers_kerykeion_v5_names() -> None:
    assert translate_point_name("True_North_Lunar_Node") == "Северный узел"
    assert translate_point_name("True_South_Lunar_Node") == "Южный узел"
    assert translate_point_name("Mean_Lilith") == "Чёрная Луна (Лилит)"
    assert translate_point_name("Chiron") == "Хирон"
    assert translate_point_name("Medium_Coeli") == "Середина Неба (MC)"
    assert translate_point_name("Sun") == "Солнце"


def test_translate_drops_untranslatable_names() -> None:
    # Непереводимое имя → None (вызывающий обязан опустить точку/аспект).
    assert translate_point_name("Some_Future_Point") is None
    assert translate_point_name("") is None
    # Уже русское имя (легаси-данные) проходит как есть.
    assert translate_point_name("Северный узел") == "Северный узел"


def test_is_display_safe_name() -> None:
    assert is_display_safe_name("Солнце") is True
    assert is_display_safe_name("Чёрная Луна (Лилит)") is True
    assert is_display_safe_name("True_North_Lunar_Node") is False
    assert is_display_safe_name("Chiron") is False
    assert is_display_safe_name("First_House") is False
    assert is_display_safe_name("") is False


def test_render_has_no_latin_point_names() -> None:
    text = render_natal_chart_text(_chart())
    leaks = _LATIN_TECH.findall(text)
    assert not leaks, f"латиница в рендере: {leaks}"


def test_system_text_has_no_latin_point_or_house_names() -> None:
    # Именно этот блок LLM цитирует в «Углублённом разборе» — латиница здесь = жалоба
    # капитана про The_North_Lunar_Node / First_House.
    text = natal_chart_to_system_text(_chart())
    leaks = _LATIN_TECH.findall(text)
    assert not leaks, f"латиница в system-тексте: {leaks}"


def test_legacy_latin_data_is_defensively_dropped() -> None:
    # Старые сохранённые карты могли содержать непереведённые латинские имена — рендер и
    # system-текст обязаны их аккуратно опустить, а не показать пользователю.
    legacy = {
        "chart_system": "западная (тропическая)",
        "sun_sign": "Козерог",
        "moon_sign": "Рыбы",
        "planets": {
            "Солнце": {"sign": "Козерог", "degree": 16.0, "house": "First_House"},
            "True_North_Lunar_Node": {"sign": "Овен", "degree": 5.0},
        },
        "aspects": [
            {"planet1": "Солнце", "planet2": "Луна", "aspect": "квадрат", "orb": 2.0},
            {
                "planet1": "True_North_Lunar_Node",
                "planet2": "Солнце",
                "aspect": "соединение",
                "orb": 1.0,
            },
        ],
    }
    for text in (render_natal_chart_text(legacy), natal_chart_to_system_text(legacy)):
        assert not _LATIN_TECH.findall(text), f"легаси-латиница протекла: {text!r}"
        assert "Солнце" in text  # валидные данные остались


# --- (3) сломанный италик убран ---------------------------------------------------


def _italic_wrappers(text: str) -> list[str]:
    # ``_слово_`` без соседних ``_``/``*`` — именно такой италик Telegram не рисует.
    return re.findall(r"(?<![*_\w])_(?!_)[^_\n]+_(?![_\w])", text)


def test_natal_render_has_no_broken_italic() -> None:
    assert _italic_wrappers(render_natal_chart_text(_chart())) == []


def test_matrix_render_has_no_broken_italic() -> None:
    dm = {
        "birth_date": "07.01.1987",
        "day": {"n": 7, "name": "Колесница"},
        "month": {"n": 1, "name": "Маг"},
        "year": {"n": 8, "name": "Сила"},
        "karma": {"n": 3, "name": "Императрица"},
        "comfort_zone": {"n": 5, "name": "Иерофант"},
    }
    assert _italic_wrappers(render_destiny_matrix_text(dm)) == []


# --- (4) подпись к колесу: дата · [время] · место, без имени -----------------------


def test_wheel_caption_includes_time_when_known() -> None:
    cap = _natal_wheel_caption("07.01.1987", "10:30", "Москва")
    assert cap == "🪐 Натальная карта · 07.01.1987 · 10:30 · Москва"


def test_wheel_caption_omits_unknown_time_without_empty_separator() -> None:
    for unknown in ("unknown", "не знаю", "", "?"):
        cap = _natal_wheel_caption("07.01.1987", unknown, "Москва")
        assert cap == "🪐 Натальная карта · 07.01.1987 · Москва"
        assert "··" not in cap.replace(" ", "")
