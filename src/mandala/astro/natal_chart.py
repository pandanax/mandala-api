"""Математический расчёт натальной карты через Swiss Ephemeris (kerykeion).

LLM получает только готовые структурированные данные для интерпретации — не считает сам.
Поддерживаются системы: 'western' (тропическая) и 'vedic' (сидерическая, Lahiri).
"""

from __future__ import annotations

import logging
import re
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

# Дополнительные точки/оси, которые kerykeion выдаёт в аспектах (не входят в 10 планет).
# Имена приходят в разных вариантах между версиями (Mean_/True_, Medium_Coeli/MC,
# *_Node / *_Lunar_Node) — покрываем все, чтобы к пользователю НИКОГДА не протекла
# латиница. Ключи в нижнем регистре — translate_point_name нормализует имя
# (`.strip().lower()`), поэтому регистр kerykeion между версиями не важен.
_POINT_RU: dict[str, str] = {
    "ascendant": "Асцендент",
    "asc": "Асцендент",
    "descendant": "Десцендент",
    "dsc": "Десцендент",
    "medium_coeli": "Середина Неба (MC)",
    "mc": "Середина Неба (MC)",
    "imum_coeli": "Дно Неба (IC)",
    "ic": "Дно Неба (IC)",
    "chiron": "Хирон",
    "mean_lilith": "Чёрная Луна (Лилит)",
    "true_lilith": "Чёрная Луна (Лилит)",
    "lilith": "Чёрная Луна (Лилит)",
    "mean_node": "Северный узел",
    "true_node": "Северный узел",
    "north_node": "Северный узел",
    "mean_north_node": "Северный узел",
    "true_north_node": "Северный узел",
    "mean_north_lunar_node": "Северный узел",
    "true_north_lunar_node": "Северный узел",
    "mean_south_node": "Южный узел",
    "true_south_node": "Южный узел",
    "south_node": "Южный узел",
    "mean_south_node_lunar": "Южный узел",
    "mean_south_lunar_node": "Южный узел",
    "true_south_lunar_node": "Южный узел",
}

# Подчёркивание или прогон ≥3 латинских букв подряд = непереведённое техническое имя
# kerykeion ('True_North_Lunar_Node', 'Chiron', 'First_House'). Такое имя пользователю
# показывать НЕЛЬЗЯ. Короткие латинские аббревиатуры в русских подписях («MC», «IC»,
# «Rx») — легитимны и разрешены (≤2 буквы подряд), поэтому и порог ≥3.
_TECH_NAME_RE = re.compile(r"_|[A-Za-z]{3,}")

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

    # Часовой пояс МЕСТА рождения обязателен: время из анкеты — местное (local
    # wall-clock), и kerykeion конвертит его local→UT именно по этому tz. Молчаливый
    # фолбэк в "UTC" читал бы местное время как UTC (сдвиг на весь оффсет пояса →
    # неверный асцендент/дома — ровно жалоба пользователя). Поэтому неопределимый
    # пояс — это ОШИБКА/эскалация, а не тихий UTC.
    try:
        from timezonefinder import TimezoneFinder

        tz_str = TimezoneFinder().timezone_at(lat=lat, lng=lng)
    except Exception as exc:
        raise ValueError(
            f"Timezone lookup failed for '{city}' ({lat:.4f},{lng:.4f}): {exc}"
        ) from exc
    if not tz_str:
        raise ValueError(
            f"Timezone not determined for '{city}' ({lat:.4f},{lng:.4f}); "
            "cannot treat birth time as local"
        )

    return lat, lng, tz_str


# Стихия каждого знака (детерминированно, по русскому названию знака). Баланс стихий
# считается из знаков планет — чистая арифметика, без эфемерид.
_ELEMENT_BY_SIGN: dict[str, str] = {
    "Овен": "Огонь",
    "Лев": "Огонь",
    "Стрелец": "Огонь",
    "Телец": "Земля",
    "Дева": "Земля",
    "Козерог": "Земля",
    "Близнецы": "Воздух",
    "Весы": "Воздух",
    "Водолей": "Воздух",
    "Рак": "Вода",
    "Скорпион": "Вода",
    "Рыбы": "Вода",
}
_ELEMENT_ORDER: tuple[str, ...] = ("Огонь", "Земля", "Воздух", "Вода")

# Имена домов kerykeion → номер (obj.house возвращает 'First_House' и т.п.).
_HOUSE_NAME_TO_NUM: dict[str, int] = {
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


def house_number(house: object) -> int | None:
    """Номер дома из значения ``planet['house']`` (kerykeion-имя или уже число)."""
    if isinstance(house, int):
        return house if 1 <= house <= 12 else None
    if isinstance(house, str):
        return _HOUSE_NAME_TO_NUM.get(house)
    return None


def compute_element_balance(planets: dict[str, Any]) -> dict[str, int]:
    """Баланс стихий: сколько планет в знаках каждой стихии (детерминированно).

    Считаются 10 планет (Солнце…Плутон) по русскому знаку — светила и планеты, без
    осей/узлов, чтобы результат был однозначным и воспроизводимым.
    """
    balance: dict[str, int] = {el: 0 for el in _ELEMENT_ORDER}
    for data in planets.values():
        if not isinstance(data, dict):
            continue
        element = _ELEMENT_BY_SIGN.get(str(data.get("sign", "")))
        if element:
            balance[element] += 1
    return balance


def _sign_ru(sign: str) -> str:
    return _SIGN_RU.get(sign[:3], sign)


def is_display_safe_name(name: object) -> bool:
    """Имя точки/планеты/аспекта безопасно показывать пользователю.

    Безопасно = непустая строка без ``_`` и без прогона ≥3 латинских букв (короткие
    аббревиатуры «MC»/«IC» в русских подписях допустимы). Непереведённое техническое
    имя kerykeion (``True_North_Lunar_Node``, ``Chiron`` и т.п.) — небезопасно: его
    нельзя показывать ни в детерминированном рендере, ни в данных, которые цитирует LLM.
    """
    if not isinstance(name, str) or not name.strip():
        return False
    return _TECH_NAME_RE.search(name) is None


def translate_point_name(name: str) -> str | None:
    """Централизованный перевод EN-имени точки/планеты kerykeion → русское.

    Единая точка перевода для всех путей к пользователю (рендер и system-текст LLM).
    Возвращает ``None``, если имя перевести НЕ удалось и результат всё ещё содержит
    латиницу/``_`` — тогда вызывающий код обязан аккуратно ОПУСТИТЬ этот аспект/точку,
    чтобы технический мусор не протёк пользователю. Уже русское имя (напр. из старых
    сохранённых данных) проходит как есть.
    """
    if not name:
        return None
    ru = _PLANET_RU.get(name) or _POINT_RU.get(name.strip().lower())
    if ru is None:
        ru = name  # вдруг это уже русское имя (легаси-данные) — проверим ниже
    return ru if is_display_safe_name(ru) else None


def _aspect_ru(name: str) -> str:
    return _ASPECT_RU.get(name.lower(), name)


def build_astrological_subject(
    birth_date: str,
    birth_time: str,
    birth_place: str,
    system: str = "western",
    *,
    coords: tuple[float, float, str] | None = None,
) -> tuple[Any, bool, tuple[float, float, str]]:
    """Построить kerykeion ``AstrologicalSubject`` по данным рождения.

    Единая точка сборки subject'а: используется и расчётом карты
    (:func:`calculate_natal_chart`), и рендером колеса
    (:mod:`mandala.services.chart_wheel`) — так школа/дома/айянамша гарантированно
    совпадают, а не задаются в двух местах и не расходятся.

    Args:
        birth_date: 'DD.MM.YYYY'
        birth_time: 'HH:MM' или 'unknown'
        birth_place: название города/населённого пункта
        system: 'western' (тропическая) или 'vedic' (сидерическая Lahiri)
        coords: опционально готовые ``(lat, lng, tz_str)`` — тогда геокодер НЕ
            вызывается (сеть не нужна). Так рендер колеса переиспользует координаты,
            уже сохранённые при расчёте карты, и ``/natal`` остаётся офлайновым.

    Returns:
        (subject, time_known, (lat, lng, tz_str)). ``time_known`` — было ли время
        известно (при неизвестном берётся полдень; оси/дома тогда не осмысленны).
    """
    from kerykeion import AstrologicalSubject

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

    # --- геокодирование (или готовые координаты) ---
    lat, lng, tz_str = coords if coords is not None else _geocode_city(birth_place)

    # --- параметры системы ---
    # Zodiac: западная — тропический, ведическая — сидерический с айянамшей Lahiri.
    # ("Tropical", не устаревшее "Tropic" из kerykeion v4.)
    zodiac_type = "Sidereal" if system == "vedic" else "Tropical"
    sidereal_mode = "LAHIRI" if system == "vedic" else None
    # Дома: западная традиция — Placidus ('P'), совпадает с референсным тропическим
    # софтом (astrogoo и др.); задаём ЯВНО, а не полагаемся на дефолт kerykeion (он
    # сейчас Placidus, но дефолты меняются между версиями — ср. Tropic→Tropical).
    # Ведическая — целознаковая система (whole-sign, 'W'): каждый знак = один дом,
    # отсчёт от Лагны — так строят Rashi/Bhava (см. регресс-тест по карте Евгении).
    houses_system = "W" if system == "vedic" else "P"

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
    if houses_system:
        kwargs["houses_system_identifier"] = houses_system

    subject = AstrologicalSubject("person", **kwargs)
    return subject, time_known, (lat, lng, tz_str)


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
    subject, time_known, geo = build_astrological_subject(
        birth_date, birth_time, birth_place, system
    )

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
        name_ru = translate_point_name(name_en)
        if name_ru is None:
            continue  # непереводимое имя — не показываем технический мусор
        sign_abbr = getattr(obj, "sign", "")
        planets[name_ru] = {
            "sign": _sign_ru(sign_abbr),
            "sign_en": sign_abbr,
            "degree": round(float(getattr(obj, "position", 0)), 2),
            "house": getattr(obj, "house", None),
            "retrograde": bool(getattr(obj, "retrograde", False)),
        }

    # --- главные оси (только при известном времени: зависят от домов) ---
    # Асцендент (АСЦ) = куспид I дома; Десцендент (ДСЦ) = VII; Середина Неба (MC) = X;
    # Дно Неба (IC) = IV. Знак каждой оси — из соответствующего дома kerykeion.
    def _house_sign(attr: str) -> str | None:
        obj = getattr(subject, attr, None)
        if obj is None:
            return None
        return _sign_ru(getattr(obj, "sign", ""))

    ascendant: str | None = None
    descendant: str | None = None
    midheaven: str | None = None
    imum_coeli: str | None = None
    if time_known:
        ascendant = _house_sign("first_house")
        descendant = _house_sign("seventh_house")
        midheaven = _house_sign("tenth_house")
        imum_coeli = _house_sign("fourth_house")

    # --- аспекты ---
    aspects: list[dict[str, Any]] = []
    try:
        from kerykeion.aspects.aspects_factory import AspectsFactory

        result = AspectsFactory.single_chart_aspects(subject.model())
        for asp in result.aspects[:30]:  # первые 30 достаточно
            p1 = translate_point_name(asp.p1_name or "")
            p2 = translate_point_name(asp.p2_name or "")
            asp_name = _aspect_ru(asp.aspect or "")
            orb = round(float(asp.orbit), 2)
            # Опускаем аспект, если ЛЮБАЯ его часть непереводима (латиница/underscore) —
            # к пользователю не должно протечь техническое имя точки или аспекта.
            if p1 and p2 and is_display_safe_name(asp_name):
                aspects.append({"planet1": p1, "planet2": p2, "aspect": asp_name, "orb": orb})
    except Exception as exc:
        logger.warning("aspects calculation failed: %s", exc)

    sun_sign = planets.get("Солнце", {}).get("sign", "")
    moon_sign = planets.get("Луна", {}).get("sign", "")

    return {
        "sun_sign": sun_sign,
        "moon_sign": moon_sign,
        "ascendant": ascendant,
        "descendant": descendant,
        "midheaven": midheaven,
        "imum_coeli": imum_coeli,
        "planets": planets,
        "element_balance": compute_element_balance(planets),
        "aspects": aspects,
        "chart_system": "ведическая (Lahiri)" if system == "vedic" else "западная (тропическая)",
        "chart_system_key": system,
        "time_known": time_known,
        "birth_place_resolved": birth_place,
        # Координаты места (lat, lng, tz) — чтобы колесо карты (chart_wheel) строило
        # subject БЕЗ повторного геокодинга: /natal остаётся офлайновым и быстрым.
        "geo": {"lat": geo[0], "lng": geo[1], "tz": geo[2]},
        "calculated_at": datetime.now(tz=UTC).isoformat(),
    }


def calculate_current_transits(
    year: int,
    month: int,
    day: int,
    hour: int = 12,
    *,
    system: str = "western",
) -> dict[str, Any]:
    """Рассчитать текущие позиции планет (транзиты) для заданной даты.

    Транзиты ОБЯЗАНЫ считаться в той же системе (``system``), что и натальная
    карта — иначе прогноз подмешивает тропическую сетку к сидерической карте
    (и наоборот) и получается «смешение школ». Для сидерической используется та
    же айянамша Lahiri, что и в :func:`calculate_natal_chart`.

    Используется фиксированная геоточка (Гринвич): знаки зодиака не зависят от
    местонахождения наблюдателя, дома — зависят, но для прогнозного контекста
    достаточно только знаков и градусов.

    Args:
        system: 'western' (тропическая) или 'vedic' (сидерическая Lahiri).
    """
    from kerykeion import AstrologicalSubject

    zodiac_type = "Sidereal" if system == "vedic" else "Tropical"
    sidereal_mode = "LAHIRI" if system == "vedic" else None

    kwargs: dict[str, Any] = {
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": 0,
        "lng": 0.0,
        "lat": 51.48,  # Гринвич, для зодиакальных позиций местоположение не важно
        "tz_str": "UTC",
        "zodiac_type": zodiac_type,
        "online": False,
    }
    if sidereal_mode:
        kwargs["sidereal_mode"] = sidereal_mode

    subject = AstrologicalSubject("transits", **kwargs)

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
        name_ru = translate_point_name(name_en)
        if name_ru is None:
            continue
        sign_abbr = getattr(obj, "sign", "")
        planets[name_ru] = {
            "sign": _sign_ru(sign_abbr),
            "degree": round(float(getattr(obj, "position", 0)), 2),
            "retrograde": bool(getattr(obj, "retrograde", False)),
        }

    return {
        "date": f"{day:02d}.{month:02d}.{year}",
        "planets": planets,
        "system": system,
        "chart_system": "ведическая (Lahiri)" if system == "vedic" else "западная (тропическая)",
    }


def current_transits_to_system_text(transits: dict[str, Any]) -> str:
    """Сформировать текстовый блок текущих транзитов для инжекции в system-промпт."""
    date = transits.get("date", "")
    system_label = transits.get("chart_system", "")
    header = f"=== ТЕКУЩИЕ ТРАНЗИТЫ (позиции планет на {date}"
    if system_label:
        header += f"; система: {system_label}"
    header += ") ==="
    lines = [header]
    for planet, data in transits.get("planets", {}).items():
        retro = " (Rx)" if data.get("retrograde") else ""
        deg = data.get("degree", 0)
        lines.append(f"  {planet}: {data.get('sign', '?')}{retro}, {deg}°")
    lines.append("=== КОНЕЦ ТРАНЗИТОВ ===")
    lines.append(
        "Транзиты рассчитаны в ТОЙ ЖЕ системе, что и натальная карта — "
        "используй их строго в этой школе, не смешивай тропические и сидерические позиции. "
        "Используй эти актуальные позиции для прогнозов и транзитных аспектов к натальной карте. "
        "Не говори, что у тебя нет данных о текущих планетах — они выше."
    )
    return "\n".join(lines)


def natal_chart_to_system_text(chart: dict[str, Any]) -> str:
    """Сформировать текстовый блок для инжекции в system-промпт LLM."""
    lines: list[str] = [
        f"=== РАССЧИТАННАЯ НАТАЛЬНАЯ КАРТА ({chart.get('chart_system', '')}) ===",
        f"Солнце: {chart.get('sun_sign', '?')}",
        f"Луна: {chart.get('moon_sign', '?')}",
    ]
    if chart.get("ascendant"):
        lines.append(f"Асцендент (АСЦ): {chart['ascendant']}")
        if chart.get("descendant"):
            lines.append(f"Десцендент (ДСЦ): {chart['descendant']}")
        if chart.get("midheaven"):
            lines.append(f"Середина Неба (MC): {chart['midheaven']}")
        if chart.get("imum_coeli"):
            lines.append(f"Дно Неба (IC): {chart['imum_coeli']}")
    else:
        lines.append("Асцендент: время рождения неизвестно, не рассчитан")

    balance = chart.get("element_balance")
    if isinstance(balance, dict) and any(balance.values()):
        parts = [f"{el} {n}" for el, n in balance.items() if n]
        lines.append("Баланс стихий: " + ", ".join(parts))

    planets = chart.get("planets", {})
    if planets:
        # Защитная сетка: пропускаем планеты/точки с непереводимым (латиница/_) именем,
        # даже из старых сохранённых карт — LLM цитирует этот блок в «Углублённом разборе».
        safe_planets = [(p, d) for p, d in planets.items() if is_display_safe_name(p)]
        if safe_planets:
            lines.append("\nПланеты:")
            for planet, data in safe_planets:
                retro = " (Rx)" if data.get("retrograde") else ""
                # Дом — ЧИСЛОМ, а не сырым kerykeion-именем ('First_House' → 1): иначе
                # латиница протекает в system-текст, который LLM цитирует в разборе.
                num = house_number(data.get("house"))
                house = f", дом {num}" if num is not None else ""
                deg = data.get("degree", 0)
                lines.append(f"  {planet}: {data.get('sign', '?')}{house}{retro}, {deg}°")

    aspects = chart.get("aspects", [])
    if aspects:
        # То же: аспект с непереводимой точкой/именем не показываем (никакой латиницы).
        safe_aspects = [
            asp
            for asp in aspects
            if is_display_safe_name(asp.get("planet1"))
            and is_display_safe_name(asp.get("planet2"))
            and is_display_safe_name(asp.get("aspect"))
        ]
        if safe_aspects:
            lines.append("\nКлючевые аспекты:")
            for asp in safe_aspects[:10]:
                lines.append(
                    f"  {asp['planet1']} — {asp['planet2']}: {asp['aspect']} (орб {asp['orb']}°)"
                )

    lines.append("=== КОНЕЦ НАТАЛЬНОЙ КАРТЫ ===")
    system_label = chart.get("chart_system", "")
    if system_label:
        lines.append(f"Интерпретируй СТРОГО в рамках выбранной системы ({system_label}).")
    lines.append(
        "Не смешивай школы: не переводи эти позиции в другую систему и не приводи "
        "«для сравнения» знаки/градусы другой школы (тропической vs сидерической). "
        "Используй ТОЛЬКО эти рассчитанные данные для астрологической интерпретации. "
        "Не пересчитывай, не сдвигай и не предполагай позиции планет самостоятельно."
    )
    return "\n".join(lines)
