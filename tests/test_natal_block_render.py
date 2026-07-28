"""Блочный рендер натальной карты + производные (оси MC/Ds/Ic, баланс стихий).

Проверяем ПОДАЧУ и сохранение производных, не пересчёт эфемерид (точность — в
``test_evgenia_natal_regression`` / ``test_natal_tz_and_no_fabrication``):

* ``calculate_natal_chart`` теперь сохраняет главные оси (Десцендент/MC/IC) и
  ``element_balance`` (геокодер замокан — офлайн);
* баланс стихий считается детерминированно из знаков планет;
* ``render_natal_chart_text`` выводит ВСЕ блоки эталонной подачи (оси, светила,
  ретро, планеты по знакам, планеты по домам, баланс стихий, аспекты, контраст);
* промпт шага ``birth_time`` явно просит МЕСТНОЕ время;
* эхо-подтверждение времени поясняет, что оно трактуется как местное.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import mandala.astro.natal_chart as nc
from mandala.astro.natal_chart import compute_element_balance, house_number
from mandala.services.chart_render import render_natal_chart_text
from mandala.verticals.intake_loader import bundled_intake_steps_path, load_intake_steps_registry

# Нерюнгри 18.02.1988 11:45 — сверено с эталоном «Астро Код» (Солнце Водолей / Луна
# Рыбы / Асцендент Близнецы). Пояс места — Asia/Yakutsk.
BIRTH_DATE = "18.02.1988"
BIRTH_TIME = "11:45"
BIRTH_PLACE = "Нерюнгри"
GEOCODE = (56.66, 124.72, "Asia/Yakutsk")


def _chart(system: str = "western") -> dict[str, Any]:
    with patch.object(nc, "_geocode_city", return_value=GEOCODE):
        return nc.calculate_natal_chart(BIRTH_DATE, BIRTH_TIME, BIRTH_PLACE, system=system)


# --- (а) блочный рендер содержит все блоки --------------------------------------


def test_render_contains_all_reference_blocks() -> None:
    text = render_natal_chart_text(_chart())
    for marker in (
        "Главные оси",
        "Асцендент",
        "Десцендент",
        "Середина Неба",
        "Дно Неба",
        "Ретроградные планеты",
        "Светила и маска",
        "Планеты в знаках",
        "Планеты в домах",
        "Баланс стихий",
        "Аспекты",
    ):
        assert marker in text, f"блок «{marker}» отсутствует в рендере"
    # Контраст «Снаружи <Асц>, внутри <Солнце>».
    assert "Снаружи Близнецы, внутри Водолей" in text


def test_aspect_points_translated_no_latin_names_leak() -> None:
    """В аспектах имена дополнительных точек переведены (нет сырых kerykeion-имён)."""
    chart = _chart()
    names: set[str] = set()
    for asp in chart["aspects"]:
        names.add(asp["planet1"])
        names.add(asp["planet2"])
    # Ни одно имя точки не приходит латиницей (MC/IC в скобках — часть русского перевода).
    for raw in (
        "Ascendant",
        "Descendant",
        "Chiron",
        "Imum_Coeli",
        "Medium_Coeli",
        "Mean_Lilith",
        "True_North_Lunar_Node",
    ):
        assert raw not in names, f"непереведённое имя точки протекло: {raw}"
    assert "Хирон" in names
    assert "Асцендент" in names


def test_render_house_shown_as_number_not_kerykeion_name() -> None:
    """Дома в рендере — числом (10 дом), а не сырым 'Tenth_House'."""
    text = render_natal_chart_text(_chart())
    assert "дом" in text
    assert "Tenth_House" not in text and "_House" not in text


# --- (б) calculate_natal_chart сохраняет оси MC/Ds/Ic ---------------------------


def test_calculate_saves_axes() -> None:
    chart = _chart()
    assert chart["ascendant"] == "Близнецы"
    assert chart["descendant"] == "Стрелец"  # ровно напротив асцендента
    assert chart["midheaven"]  # MC рассчитан
    assert chart["imum_coeli"]  # IC рассчитан


def test_axes_absent_when_time_unknown() -> None:
    with patch.object(nc, "_geocode_city", return_value=GEOCODE):
        chart = nc.calculate_natal_chart(BIRTH_DATE, "не знаю", BIRTH_PLACE)
    assert chart["ascendant"] is None
    assert chart["descendant"] is None
    assert chart["midheaven"] is None
    assert chart["imum_coeli"] is None


# --- (в) баланс стихий считается верно ------------------------------------------


def test_element_balance_counts_all_ten_planets() -> None:
    chart = _chart()
    balance = chart["element_balance"]
    assert set(balance) == {"Огонь", "Земля", "Воздух", "Вода"}
    assert sum(balance.values()) == 10  # 10 планет распределены по стихиям


def test_compute_element_balance_deterministic() -> None:
    planets = {
        "Солнце": {"sign": "Овен"},  # Огонь
        "Луна": {"sign": "Телец"},  # Земля
        "Меркурий": {"sign": "Близнецы"},  # Воздух
        "Венера": {"sign": "Рак"},  # Вода
        "Марс": {"sign": "Лев"},  # Огонь
    }
    assert compute_element_balance(planets) == {
        "Огонь": 2,
        "Земля": 1,
        "Воздух": 1,
        "Вода": 1,
    }


def test_house_number_helper() -> None:
    assert house_number("Tenth_House") == 10
    assert house_number("First_House") == 1
    assert house_number(7) == 7
    assert house_number(None) is None
    assert house_number("Not_A_House") is None


# --- рендер устойчив к старым сохранённым данным (без осей/стихий) ---------------


def test_render_tolerates_legacy_data_without_axes_and_elements() -> None:
    legacy = {
        "chart_system": "западная (тропическая)",
        "sun_sign": "Овен",
        "moon_sign": "Телец",
        "ascendant": "Лев",
        "planets": {
            "Солнце": {"sign": "Овен", "degree": 10.0, "house": "First_House"},
            "Луна": {"sign": "Телец", "degree": 5.0, "house": "Tenth_House"},
        },
        "aspects": [],
    }
    text = render_natal_chart_text(legacy)
    # Баланс стихий досчитан на лету, блок присутствует.
    assert "Баланс стихий" in text
    assert "Планеты в домах" in text


# --- (г) промпт birth_time явно про местное время -------------------------------


def test_birth_time_prompt_asks_for_local_time() -> None:
    reg = load_intake_steps_registry(path=bundled_intake_steps_path())
    step = next(s for s in reg["astrology"] if s.field_key == "birth_time")
    low = step.prompt.lower()
    assert "местн" in low  # «МЕСТНОЕ время»
    assert "часов" in low or "поясу" in low  # пояс города рождения


def test_birth_time_echo_marks_local() -> None:
    from mandala.services.intake_flow import _echo_line

    echo = _echo_line("birth_time", "11:45", None)
    assert "местн" in echo.lower()
    assert "11:45" in echo
