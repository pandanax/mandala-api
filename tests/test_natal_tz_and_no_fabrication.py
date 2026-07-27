"""Регресс по реальной жалобе «Евгения»: натальная карта была неверной.

Первопричина (доказана сравнением сломанного и верного пути на данных пользователя,
28.02.1989 07:33, Вологда — восстановлены из её референсных карт, feedback/evgeniya1):

* **initiating trigger** — запрос натальной карты, когда часовой пояс места рождения
  не определился ИЛИ математических данных карты нет.
* **masking condition** — два тихих фолбэка компенсировали сбой правдоподобной чушью:
  (1) ``_geocode_city`` при недоступном/пустом ``timezonefinder`` МОЛЧА возвращал
  ``tz_str="UTC"`` → местное время читалось как UTC (+3 ч для Москвы); (2) при
  отсутствии рассчитанных данных в промпт как «карта клиента» уходил сохранённый
  ЛЛМ-текст → модель «сочиняла» позиции.
* **visible symptom** — асцендент Близнецы вместо Рыб, планеты не в тех знаках,
  «что-то между» западной и ведической картами (дословная жалоба).

Тесты падают без фикса и проходят с ним:
  1. неопределимый пояс → ОШИБКА (эскалация), а НЕ тихий UTC;
  2. местное время трактуется в поясе места (UTC-путь даёт именно тот неверный
     асцендент Близнецы, что был на скриншоте бота);
  3. западная карта совпадает с эталонным тропическим Placidus-референсом Евгении;
  4. при отсутствии математики LLM-путь НЕ подставляет выдуманную карту.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

import mandala.astro.natal_chart as nc
from mandala.services.text_reply import build_natal_prompt_section

# Данные Евгении (восстановлены из её референсных карт; сеть замокана — офлайн).
BIRTH_DATE = "28.02.1989"
BIRTH_TIME = "07:33"  # местное, Europe/Moscow (UTC+3) → UT 04:33
BIRTH_PLACE = "Вологда"
LAT, LNG = 59.46, 40.62


class _FakeNominatimResp:
    """Заглушка ответа Nominatim: возвращает координаты Вологды."""

    def raise_for_status(self) -> None:
        pass

    def json(self) -> list[dict[str, str]]:
        return [{"lat": str(LAT), "lon": str(LNG)}]


# --- (1) неопределимый пояс → ошибка, а не тихий UTC ------------------------------


def test_geocode_raises_when_timezone_none_instead_of_silent_utc() -> None:
    """timezonefinder вернул None → ValueError (эскалация), а НЕ tz_str='UTC'."""
    fake_tf = type("TF", (), {"timezone_at": lambda self, *, lat, lng: None})
    with (
        patch("mandala.astro.natal_chart.httpx.get", return_value=_FakeNominatimResp()),
        patch("timezonefinder.TimezoneFinder", fake_tf),
    ):
        with pytest.raises(ValueError, match="Timezone"):
            nc._geocode_city(BIRTH_PLACE)


def test_geocode_raises_when_timezonefinder_unavailable_instead_of_silent_utc() -> None:
    """timezonefinder бросил (не установлен/сломан) → ValueError, а НЕ тихий UTC."""

    def _boom(self: Any, *, lat: float, lng: float) -> str:
        raise RuntimeError("timezonefinder unavailable")

    fake_tf = type("TF", (), {"timezone_at": _boom})
    with (
        patch("mandala.astro.natal_chart.httpx.get", return_value=_FakeNominatimResp()),
        patch("timezonefinder.TimezoneFinder", fake_tf),
    ):
        with pytest.raises(ValueError, match="Timezone"):
            nc._geocode_city(BIRTH_PLACE)


def test_geocode_returns_resolved_timezone_when_known() -> None:
    """Штатный путь: пояс определён — возвращаем именно его (не UTC)."""
    fake_tf = type("TF", (), {"timezone_at": lambda self, *, lat, lng: "Europe/Moscow"})
    with (
        patch("mandala.astro.natal_chart.httpx.get", return_value=_FakeNominatimResp()),
        patch("timezonefinder.TimezoneFinder", fake_tf),
    ):
        lat, lng, tz = nc._geocode_city(BIRTH_PLACE)
    assert (round(lat, 2), round(lng, 2), tz) == (LAT, LNG, "Europe/Moscow")


# --- (2) местное время трактуется в поясе места (симптом Близнецов) ---------------


def _chart(tz: str) -> dict[str, Any]:
    with patch.object(nc, "_geocode_city", return_value=(LAT, LNG, tz)):
        return nc.calculate_natal_chart(BIRTH_DATE, BIRTH_TIME, BIRTH_PLACE, system="western")


def test_birth_time_is_local_utc_path_reproduces_gemini_bug() -> None:
    """Пояс места → ASC Рыбы (эталон). Тот же wall-clock как UTC → ASC Близнецы (баг).

    Именно асцендент Близнецы был на скриншоте бота — прямое следствие того, что
    местное время (07:33) было прочитано как UTC вместо Europe/Moscow.
    """
    assert _chart("Europe/Moscow")["ascendant"] == "Рыбы"
    assert _chart("UTC")["ascendant"] == "Близнецы"


# --- (3) западная карта = эталонный тропический Placidus-референс ------------------

# Планета → (знак, градус, дом Placidus) из западной карты Евгении (photo_1).
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
_HOUSE_NUM = {
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


def test_western_matches_reference_placidus_chart() -> None:
    """Знаки, градусы и дома (Placidus) совпадают с референсом (защищает явный 'P')."""
    chart = _chart("Europe/Moscow")
    assert chart["ascendant"] == "Рыбы"
    for planet, (sign, degree, house) in WESTERN_REF.items():
        data = chart["planets"][planet]
        assert data["sign"] == sign, f"{planet}: знак {data['sign']} != {sign}"
        assert abs(data["degree"] - degree) <= 0.2, (
            f"{planet}: градус {data['degree']} далёк от референса {degree}"
        )
        assert _HOUSE_NUM.get(data["house"]) == house, f"{planet}: дом {data['house']} != {house}"


# --- (4) без математики LLM НЕ подсовывает выдуманную карту ------------------------


def test_natal_section_injects_computed_block_when_data_present() -> None:
    """Есть рассчитанные данные → в промпт идёт блок РАССЧИТАННОЙ карты."""
    chart = _chart("Europe/Moscow")
    section = build_natal_prompt_section(chart)
    assert "РАССЧИТАННАЯ НАТАЛЬНАЯ КАРТА" in section
    assert "Рыбы" in section  # реальные посчитанные знаки


def test_natal_section_forbids_fabrication_when_no_math() -> None:
    """Нет математики → запрет выдумывать; НИКОГДА не подставляем LLM-текст карты."""
    fabricated = "Солнце в Близнецах, Асцендент Близнецы — выдуманная ботом карта"
    cases: list[object] = [None, {}, "", fabricated]
    for empty in cases:
        section = build_natal_prompt_section(empty)
        assert "Swiss Ephemeris" in section
        assert "не выдумывай" in section.lower()
        assert "РАССЧИТАННАЯ НАТАЛЬНАЯ КАРТА" not in section
    # Ключевое: даже если передать «сохранённый» LLM-текст, он НЕ попадёт в промпт.
    assert fabricated not in build_natal_prompt_section(fabricated)
