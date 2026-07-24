"""Математический расчёт натальной карты через Swiss Ephemeris (kerykeion).

LLM получает только готовые структурированные данные для интерпретации — не считает сам.
Поддерживаются системы: 'western' (тропическая) и 'vedic' (сидерическая, Lahiri).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_HEADERS = {"User-Agent": "mandala-astro/1.0 contact:admin@mandala-app.online"}

_SIGN_RU: dict[str, str] = {
    "Ari": "Овен",
    "Tau": "Телец",
    "Gem": "Близнецы",
    "Can": "Рак",
    "Leo": "Лев",
    "Vir": "Дева",
    "Lib": "Весы",
    "Sco": "Скорпион",
    "Sag": "Стрелец",
    "Cap": "Козерог",
    "Aqu": "Водолей",
    "Pis": "Рыбы",
}

_PLANET_RU: dict[str, str] = {
    "Sun": "Солнце",
    "Moon": "Луна",
    "Mercury": "Меркурий",
    "Venus": "Венера",
    "Mars": "Марс",
    "Jupiter": "Юпитер",
    "Saturn": "Сатурн",
    "Uranus": "Уран",
    "Neptune": "Нептун",
    "Pluto": "Плутон",
}

_ASPECT_RU: dict[str, str] = {
    "conjunction": "соединение",
    "opposition": "оппозиция",
    "trine": "трин",
    "square": "квадрат",
    "sextile": "секстиль",
    "quincunx": "квинконс",
    "semisextile": "полусекстиль",
    "semisquare": "полуквадрат",
    "sesquiquadrate": "полутораквадрат",
}


def _geocode_city(city: str) -> tuple[float, float, str]:
    """Возвращает (lat, lng, tz_str) для города через Nominatim + timezonefinder."""
    try:
        resp = httpx.get(
            _NOMINATIM_URL,
            params={"q": city, "format": "json", "limit": 1},
            headers=_NOMINATIM_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise ValueError(f"Geocoding failed for '{city}': {exc}") from exc

    if not data:
        raise ValueError(f"City not found: '{city}'")

    lat = float(data[0]["lat"])
    lng = float(data[0]["lon"])

    try:
        from timezonefinder import TimezoneFinder  # type: ignore[import-not-found]

        tf = TimezoneFinder()
        tz_str = tf.timezone_at(lat=lat, lng=lng) or "UTC"
    except Exception:
        tz_str = "UTC"

    return lat, lng, tz_str


def _sign_ru(sign: str) -> str:
    return _SIGN_RU.get(sign[:3], sign)


def _planet_ru(name: str) -> str:
    return _PLANET_RU.get(name, name)


def _aspect_ru(name: str) -> str:
    return _ASPECT_RU.get(name.lower(), name)


def calculate_natal_chart(
    birth_date: str,
    birth_time: str,
    birth_place: str,
    system: str = "western",
) -> dict[str, Any]:
    """Рассчитать натальную карту математически.

    Args:
        birth_date: 'DD.MM.YYYY'
        birth_time: 'HH:MM' или 'unknown'
        birth_place: название города/населённого пункта
        system: 'western' (тропическая) или 'vedic' (сидерическая Lahiri)

    Returns:
        dict с полями sun_sign, moon_sign, ascendant, planets, aspects,
        chart_system, calculated_at, birth_place_resolved.
    """
    from kerykeion import AstrologicalSubject  # type: ignore[import-not-found]

    # --- парсинг даты ---
    try:
        parts = birth_date.strip().split(".")
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    except Exception as exc:
        raise ValueError(f"Invalid birth_date format '{birth_date}', expected DD.MM.YYYY") from exc

    # --- парсинг времени ---
    time_known = birth_time.strip().lower() not in ("unknown", "не знаю", "незнаю", "", "?")
    if time_known:
        try:
            t_parts = birth_time.strip().split(":")
            hour, minute = int(t_parts[0]), int(t_parts[1])
        except Exception as exc:
            raise ValueError(f"Invalid birth_time format '{birth_time}', expected HH:MM") from exc
    else:
        hour, minute = 12, 0  # полдень при неизвестном времени

    # --- геокодирование ---
    lat, lng, tz_str = _geocode_city(birth_place)

    # --- параметры системы ---
    zodiac_type = "Sidereal" if system == "vedic" else "Tropic"
    sidereal_mode = "LAHIRI" if system == "vedic" else None

    # --- расчёт ---
    kwargs: dict[str, Any] = {
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "lng": lng,
        "lat": lat,
        "tz_str": tz_str,
        "zodiac_type": zodiac_type,
        "online": False,
    }
    if sidereal_mode:
        kwargs["sidereal_mode"] = sidereal_mode

    subject = AstrologicalSubject("person", **kwargs)

    # --- извлечение планет ---
    planet_attrs = [
        "sun",
        "moon",
        "mercury",
        "venus",
        "mars",
        "jupiter",
        "saturn",
        "uranus",
        "neptune",
        "pluto",
    ]
    planets: dict[str, dict[str, Any]] = {}
    for attr in planet_attrs:
        obj = getattr(subject, attr, None)
        if obj is None:
            continue
        name_en = getattr(obj, "name", attr.capitalize())
        sign_abbr = getattr(obj, "sign", "")
        planets[_planet_ru(name_en)] = {
            "sign": _sign_ru(sign_abbr),
            "sign_en": sign_abbr,
            "degree": round(float(getattr(obj, "position", 0)), 2),
            "house": getattr(obj, "house", None),
            "retrograde": bool(getattr(obj, "retrograde", False)),
        }

    # --- асцендент ---
    ascendant: str | None = None
    if time_known:
        first_house = getattr(subject, "first_house", None)
        if first_house is not None:
            sign_abbr = getattr(first_house, "sign", "")
            ascendant = _sign_ru(sign_abbr)

    # --- аспекты ---
    aspects: list[dict[str, Any]] = []
    try:
        from kerykeion.aspects import NatalAspects  # type: ignore[import-not-found]

        na = NatalAspects(subject, subject)
        raw_aspects = getattr(na, "all_aspects", [])
        for asp in raw_aspects[:30]:  # первые 30 достаточно
            p1 = _planet_ru(getattr(asp, "p1_name", "") or "")
            p2 = _planet_ru(getattr(asp, "p2_name", "") or "")
            asp_name = _aspect_ru(getattr(asp, "aspect", "") or "")
            orb = round(float(getattr(asp, "orbit", 0)), 2)
            if p1 and p2 and asp_name:
                aspects.append({"planet1": p1, "planet2": p2, "aspect": asp_name, "orb": orb})
    except Exception as exc:
        logger.warning("aspects calculation failed: %s", exc)

    sun_sign = planets.get("Солнце", {}).get("sign", "")
    moon_sign = planets.get("Луна", {}).get("sign", "")

    return {
        "sun_sign": sun_sign,
        "moon_sign": moon_sign,
        "ascendant": ascendant,
        "planets": planets,
        "aspects": aspects,
        "chart_system": "ведическая (Lahiri)" if system == "vedic" else "западная (тропическая)",
        "chart_system_key": system,
        "time_known": time_known,
        "birth_place_resolved": birth_place,
        "calculated_at": datetime.now(tz=UTC).isoformat(),
    }


def natal_chart_to_system_text(chart: dict[str, Any]) -> str:
    """Сформировать текстовый блок для инжекции в system-промпт LLM."""
    lines: list[str] = [
        f"=== РАССЧИТАННАЯ НАТАЛЬНАЯ КАРТА ({chart.get('chart_system', '')}) ===",
        f"Солнце: {chart.get('sun_sign', '?')}",
        f"Луна: {chart.get('moon_sign', '?')}",
    ]
    if chart.get("ascendant"):
        lines.append(f"Асцендент (АСЦ): {chart['ascendant']}")
    else:
        lines.append("Асцендент: время рождения неизвестно, не рассчитан")

    planets = chart.get("planets", {})
    if planets:
        lines.append("\nПланеты:")
        for planet, data in planets.items():
            retro = " (Rx)" if data.get("retrograde") else ""
            house = f", дом {data['house']}" if data.get("house") else ""
            deg = data.get("degree", 0)
            lines.append(f"  {planet}: {data.get('sign', '?')}{house}{retro}, {deg}°")

    aspects = chart.get("aspects", [])
    if aspects:
        lines.append("\nКлючевые аспекты:")
        for asp in aspects[:10]:
            lines.append(
                f"  {asp['planet1']} — {asp['planet2']}: {asp['aspect']} (орб {asp['orb']}°)"
            )

    lines.append("=== КОНЕЦ НАТАЛЬНОЙ КАРТЫ ===")
    lines.append(
        "Используй ТОЛЬКО эти рассчитанные данные для астрологической интерпретации. "
        "Не пересчитывай и не предполагай позиции планет самостоятельно."
    )
    return "\n".join(lines)
