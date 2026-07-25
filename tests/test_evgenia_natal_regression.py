"""Регресс-тест точности двух астрологических школ по реальному кейсу (фидбек «Евгения»).

Живой фидбек: бот выдавал «что-то среднее» между западной (тропической) и ведической
(сидерической) картами. Пользователь Евгения прислала обе свои референсные карты из
стандартного астро-софта. Дата/место/время рождения восстановлены математически из этих
карт (позиции 10 планет однозначно задают UT, ASC/MC — координаты):

    28.02.1989, 07:33 по местному времени (Europe/Moscow, UTC+3 → UT 04:33),
    Вологодская обл. (~59.46° N, 40.62° E).

Проверяем, что :func:`calculate_natal_chart` воспроизводит ОБЕ референсные карты
математически точно и что школы отличаются РОВНО на айянамшу Lahiri (а не «нечто среднее»):

* западная (тропическая) — знаки, градусы и дома (Placidus) совпадают с референсом;
* ведическая (сидерическая Lahiri) — знаки, градусы и дома (whole-sign / Rashi) совпадают;
* сидерические долготы = тропические − айянамша Lahiri (~23.7° на 1989 г.) для ВСЕХ планет;
* транзиты считаются в той же школе, что и натальная карта;
* время трактуется в часовом поясе места рождения.

Приватные данные Евгении (feedback/) в репозиторий не попадают — здесь только
восстановленные из её референсных карт числа, зафиксированные как ожидания.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import mandala.astro.natal_chart as nc

# --- восстановленные данные рождения (из референсных карт, feedback/evgeniya1) ---
BIRTH_DATE = "28.02.1989"
BIRTH_TIME = "07:33"  # местное, Europe/Moscow (UTC+3) → UT 04:33
BIRTH_PLACE = "Вологда"
GEOCODE = (59.46, 40.62, "Europe/Moscow")  # (lat, lng, tz_str)

# Русские знаки → базовый градус эклиптики (для проверки айянамши).
_SIGN_BASE: dict[str, float] = {
    "Овен": 0,
    "Телец": 30,
    "Близнецы": 60,
    "Рак": 90,
    "Лев": 120,
    "Дева": 150,
    "Весы": 180,
    "Скорпион": 210,
    "Стрелец": 240,
    "Козерог": 270,
    "Водолей": 300,
    "Рыбы": 330,
}

# Имена домов kerykeion → номер.
_HOUSE_NUM: dict[str, int] = {
    "First_House": 1,
    "Second_House": 2,
    "Third_House": 3,
    "Fourth_House": 4,
    "Fifth_House": 5,
    "Sixth_House": 6,
    "Seventh_House": 7,
    "Eighth_House": 8,
    "Ninth_House": 9,
    "Tenth_House": 10,
    "Eleventh_House": 11,
    "Twelfth_House": 12,
}

# Референс из ЗАПАДНОЙ (тропической) карты Евгении: планета → (знак, градус, дом).
# Дома — Placidus (как в стандартном тропическом софте).
WESTERN_REF: dict[str, tuple[str, float, int]] = {
    "Солнце": ("Рыбы", 9.57, 12),
    "Луна": ("Стрелец", 2.10, 8),
    "Меркурий": ("Водолей", 14.79, 12),
    "Венера": ("Рыбы", 0.60, 12),
    "Марс": ("Телец", 23.23, 2),
    "Юпитер": ("Телец", 28.54, 2),
    "Сатурн": ("Козерог", 11.66, 11),
    "Уран": ("Козерог", 4.65, 10),
    "Нептун": ("Козерог", 11.84, 11),
    "Плутон": ("Скорпион", 15.15, 7),
}
WESTERN_ASC = "Рыбы"

# Референс из ВЕДИЧЕСКОЙ (сидерической Lahiri) карты Евгении.
# Дома — whole-sign (Rashi/Bhava): каждый знак = дом, отсчёт от Лагны (Водолей).
# Уран/Нептун/Плутон в референсной таблице отсутствовали — проверяются через айянамшу.
VEDIC_REF: dict[str, tuple[str, float, int]] = {
    "Солнце": ("Водолей", 15.86, 1),
    "Луна": ("Скорпион", 8.41, 10),
    "Меркурий": ("Козерог", 21.08, 12),
    "Венера": ("Водолей", 6.90, 1),
    "Марс": ("Овен", 29.53, 3),
    "Юпитер": ("Телец", 4.84, 4),
    "Сатурн": ("Стрелец", 17.95, 11),
}
VEDIC_ASC = "Водолей"

# Айянамша Lahiri на 28.02.1989 (тропическая долгота − сидерическая).
LAHIRI_1989 = 23.708
DEG_TOL = 0.2  # допуск чтения градусов с фото, °


def _chart(system: str) -> dict[str, Any]:
    """Посчитать натальную карту Евгении с замоканным геокодером (без сети)."""
    with patch.object(nc, "_geocode_city", return_value=GEOCODE):
        return nc.calculate_natal_chart(
            birth_date=BIRTH_DATE,
            birth_time=BIRTH_TIME,
            birth_place=BIRTH_PLACE,
            system=system,
        )


def _abs_longitude(sign: str, degree: float) -> float:
    return _SIGN_BASE[sign] + degree


def test_western_matches_reference_chart() -> None:
    """Западная (тропическая): знаки, градусы и дома (Placidus) совпадают с референсом."""
    chart = _chart("western")
    assert chart["chart_system_key"] == "western"
    assert chart["ascendant"] == WESTERN_ASC
    assert chart["sun_sign"] == "Рыбы"
    assert chart["moon_sign"] == "Стрелец"
    for planet, (sign, degree, house) in WESTERN_REF.items():
        data = chart["planets"][planet]
        assert data["sign"] == sign, f"{planet}: знак {data['sign']} != {sign}"
        assert abs(data["degree"] - degree) <= DEG_TOL, (
            f"{planet}: градус {data['degree']} далёк от референса {degree}"
        )
        assert _HOUSE_NUM.get(data["house"]) == house, f"{planet}: дом {data['house']} != {house}"


def test_vedic_matches_reference_chart() -> None:
    """Ведическая (сидерическая Lahiri): знаки, градусы и whole-sign дома совпадают."""
    chart = _chart("vedic")
    assert chart["chart_system_key"] == "vedic"
    assert chart["ascendant"] == VEDIC_ASC
    assert chart["sun_sign"] == "Водолей"
    assert chart["moon_sign"] == "Скорпион"
    for planet, (sign, degree, house) in VEDIC_REF.items():
        data = chart["planets"][planet]
        assert data["sign"] == sign, f"{planet}: знак {data['sign']} != {sign}"
        assert abs(data["degree"] - degree) <= DEG_TOL, (
            f"{planet}: градус {data['degree']} далёк от референса {degree}"
        )
        assert _HOUSE_NUM.get(data["house"]) == house, (
            f"{planet}: дом {data['house']} != {house} (ожидается whole-sign)"
        )


def test_two_schools_are_not_something_in_between() -> None:
    """Ключевая проверка фидбека: школы различаются РОВНО на айянамшу Lahiri.

    Для каждой планеты (тропическая долгота − сидерическая) == айянамша, одинаковая
    для всех планет. Это исключает «нечто среднее»: вывод строго соответствует одной
    из двух школ, а не их смеси.
    """
    west = _chart("western")["planets"]
    ved = _chart("vedic")["planets"]
    offsets = []
    for planet in WESTERN_REF:
        trop = _abs_longitude(west[planet]["sign"], west[planet]["degree"])
        sid = _abs_longitude(ved[planet]["sign"], ved[planet]["degree"])
        offset = (trop - sid) % 360
        offsets.append(offset)
        assert abs(offset - LAHIRI_1989) <= 0.2, (
            f"{planet}: сдвиг тропик−сидерик {offset:.3f}° != айянамша {LAHIRI_1989}°"
        )
    # Айянамша одинакова для всех планет (разброс < 0.05°) — единая, а не «плавающая».
    assert max(offsets) - min(offsets) <= 0.05


def test_sun_sign_differs_between_schools() -> None:
    """Наглядно: у одного человека Солнце в РАЗНЫХ знаках по разным школам (не «между»)."""
    assert _chart("western")["sun_sign"] == "Рыбы"
    assert _chart("vedic")["sun_sign"] == "Водолей"


def test_birth_time_interpreted_in_birthplace_timezone() -> None:
    """Время рождения трактуется в часовом поясе места рождения (не в UTC).

    При tz места (Europe/Moscow, +3) карта совпадает с референсом (ASC Рыбы). Тот же
    момент по «часам» в UTC даёт другой UT → другой асцендент. Значит tz реально
    применяется к времени рождения.
    """
    with patch.object(nc, "_geocode_city", return_value=(59.46, 40.62, "Europe/Moscow")):
        msk = nc.calculate_natal_chart(BIRTH_DATE, BIRTH_TIME, BIRTH_PLACE, system="western")
    with patch.object(nc, "_geocode_city", return_value=(59.46, 40.62, "UTC")):
        as_utc = nc.calculate_natal_chart(BIRTH_DATE, BIRTH_TIME, BIRTH_PLACE, system="western")

    assert msk["ascendant"] == WESTERN_ASC  # часовой пояс места → референсный асцендент
    assert as_utc["ascendant"] != msk["ascendant"]  # иной пояс → иной асцендент


def test_transits_honor_selected_system() -> None:
    """Транзиты считаются в выбранной школе, а не всегда тропически.

    Сидерические транзиты сдвинуты относительно тропических на айянамшу (~24° на 2026 г.):
    иначе прогноз подмешивал бы тропическую сетку к ведической карте («смешение школ»).
    """
    west = nc.calculate_current_transits(2026, 7, 25, 12, system="western")
    ved = nc.calculate_current_transits(2026, 7, 25, 12, system="vedic")

    assert west["system"] == "western" and ved["system"] == "vedic"
    assert "тропическая" in west["chart_system"]
    assert "Lahiri" in ved["chart_system"]

    sun_w = west["planets"]["Солнце"]
    sun_v = ved["planets"]["Солнце"]
    # Разные знаки (Лев vs Рак) — школы явно расходятся.
    assert sun_w["sign"] != sun_v["sign"]
    # Сдвиг ровно на айянамшу (Lahiri 2026 ≈ 24.2°).
    offset = (
        _abs_longitude(sun_w["sign"], sun_w["degree"])
        - _abs_longitude(sun_v["sign"], sun_v["degree"])
    ) % 360
    assert 23.5 <= offset <= 25.0, f"сдвиг транзитов {offset:.3f}° не равен айянамше 2026"


def test_default_transits_are_tropical() -> None:
    """Без указания system транзиты остаются тропическими (обратная совместимость)."""
    default = nc.calculate_current_transits(2026, 7, 25, 12)
    western = nc.calculate_current_transits(2026, 7, 25, 12, system="western")
    assert default["system"] == "western"
    assert default["planets"]["Солнце"]["sign"] == western["planets"]["Солнце"]["sign"]


def test_natal_prompt_block_names_school_and_forbids_mixing() -> None:
    """Блок натальной карты для LLM называет школу и запрещает смешение/выдумывание."""
    text = nc.natal_chart_to_system_text(_chart("vedic"))
    assert "Lahiri" in text  # школа явно названа
    assert "смешивай" in text.lower()  # запрет смешения школ
    assert "ТОЛЬКО эти рассчитанные" in text  # запрет выдумывать позиции


def test_transit_prompt_block_declares_school_and_forbids_mixing() -> None:
    """Блок транзитов для LLM объявляет школу и запрещает смешивать сетки."""
    text = nc.current_transits_to_system_text(
        nc.calculate_current_transits(2026, 7, 25, 12, system="vedic")
    )
    assert "Lahiri" in text  # школа объявлена в заголовке транзитов
    assert "смешивай" in text.lower()
