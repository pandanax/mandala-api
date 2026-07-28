"""Мгновенный человекочитаемый рендер натальной карты и Матрицы Судьбы из БД.

Команды ``/natal`` и ``/matrix`` должны отвечать ПОЧТИ МГНОВЕННО — это
детерминированный рендер уже сохранённых в ``agent_card`` данных, БЕЗ прогона через
LLM (иначе не «мгновенно») и без пересчёта. Математику считает и сохраняет
``services.scenario_intake`` при сохранении профиля (Swiss Ephemeris для карты,
модуль Матрицы Судьбы для матрицы) — здесь только форматирование.

Кнопки под рендером — инлайн-навигация «что дальше»: углублённый разбор через LLM
(``mdl:natal`` / ``mdl:matrix``), переход между картой и матрицей (``/natal`` /
``/matrix`` как callback-команды), прогноз и профиль.
"""

from __future__ import annotations

from typing import Any

from mandala.astro.natal_chart import (
    compute_element_balance,
    house_number,
    is_display_safe_name,
)
from mandala.domain.contracts import OutboundMessage

# Короткие СТАТИЧЕСКИЕ подписи (не LLM) к ядру карты — что означает каждая точка.
_SUN_MEANING = "ядро личности, воля, самовыражение"
_MOON_MEANING = "эмоции, внутренний мир, потребность в опоре"
_ASC_MEANING = "маска, первое впечатление, как вас видят"

# Классификация аспектов для блока (по русскому названию из natal_chart).
_HARMONIOUS_ASPECTS = frozenset({"трин", "секстиль"})
_TENSE_ASPECTS = frozenset({"квадрат", "оппозиция", "полуквадрат", "полутораквадрат"})


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def natal_nav_buttons() -> list[list[dict[str, str]]]:
    """Навигация под натальной картой."""
    return [
        [_btn("🔮 Углублённый разбор", "mdl:natal")],
        [_btn("🌌 Карта судьбы", "/matrix"), _btn("📊 Прогноз", "mdl:forecast_menu")],
        [_btn("👤 Профиль", "mdl:profile")],
    ]


def matrix_nav_buttons() -> list[list[dict[str, str]]]:
    """Навигация под Матрицей Судьбы."""
    return [
        [_btn("🔮 Разбор судьбы", "mdl:matrix")],
        [_btn("🪐 Натальная карта", "/natal"), _btn("📊 Прогноз", "mdl:forecast_menu")],
        [_btn("👤 Профиль", "mdl:profile")],
    ]


def _block_axes(natal_data: dict[str, Any], lines: list[str]) -> None:
    """🧭 Главные оси: Асцендент, Десцендент, Середина Неба (MC), Дно Неба (IC)."""
    asc = natal_data.get("ascendant")
    if not asc:
        if not natal_data.get("time_known", True):
            lines.append("")
            lines.append(
                "🧭 **Главные оси:** время рождения неизвестно — оси и дома не рассчитаны."
            )
        return
    lines.append("")
    lines.append("🧭 **Главные оси**")
    lines.append(f"• Асцендент (АСЦ): {asc}")
    if natal_data.get("descendant"):
        lines.append(f"• Десцендент (ДСЦ): {natal_data['descendant']}")
    if natal_data.get("midheaven"):
        lines.append(f"• Середина Неба (MC): {natal_data['midheaven']}")
    if natal_data.get("imum_coeli"):
        lines.append(f"• Дно Неба (IC): {natal_data['imum_coeli']}")


def _block_luminaries(natal_data: dict[str, Any], lines: list[str]) -> None:
    """☀️🌙⬆️ Солнце / Луна / Асцендент — ядро, эмоции, маска (статичные подписи)."""
    lines.append("")
    lines.append("☀️ **Светила и маска**")
    lines.append(f"• ☀️ Солнце — {natal_data.get('sun_sign', '?')}: {_SUN_MEANING}")
    lines.append(f"• 🌙 Луна — {natal_data.get('moon_sign', '?')}: {_MOON_MEANING}")
    asc = natal_data.get("ascendant")
    if asc:
        lines.append(f"• ⬆️ Асцендент — {asc}: {_ASC_MEANING}")


def _block_retrograde(planets: dict[str, Any], lines: list[str]) -> None:
    """℞ Ретроградные планеты отдельным блоком."""
    retro = [
        planet
        for planet, data in planets.items()
        if isinstance(data, dict) and data.get("retrograde") and is_display_safe_name(planet)
    ]
    lines.append("")
    if retro:
        lines.append("℞ **Ретроградные планеты:** " + ", ".join(retro))
    else:
        lines.append("℞ **Ретроградные планеты:** нет")


def _block_planets_by_sign(planets: dict[str, Any], lines: list[str]) -> None:
    """🪐 Планеты по знакам (планета, знак, градус)."""
    lines.append("")
    lines.append("🪐 **Планеты в знаках**")
    for planet, data in planets.items():
        if not isinstance(data, dict) or not is_display_safe_name(planet):
            continue
        sign = data.get("sign", "?")
        deg = data.get("degree")
        retro = " ℞" if data.get("retrograde") else ""
        piece = f"• {planet}: {sign}"
        if isinstance(deg, (int, float)):
            piece += f", {deg}°"
        piece += retro
        lines.append(piece)


def _block_planets_by_house(planets: dict[str, Any], lines: list[str]) -> None:
    """🏠 Планеты по домам (планета → номер дома)."""
    housed = []
    for planet, data in planets.items():
        if not isinstance(data, dict) or not is_display_safe_name(planet):
            continue
        num = house_number(data.get("house"))
        if num is not None:
            housed.append((planet, num))
    if not housed:
        return
    lines.append("")
    lines.append("🏠 **Планеты в домах**")
    for planet, num in housed:
        lines.append(f"• {planet}: {num} дом")


def _block_elements(natal_data: dict[str, Any], planets: dict[str, Any], lines: list[str]) -> None:
    """⚖️ Баланс стихий (Огонь/Земля/Воздух/Вода) — детерминированно из знаков планет."""
    balance = natal_data.get("element_balance")
    if not (isinstance(balance, dict) and any(balance.values())):
        balance = compute_element_balance(planets)
    if not any(balance.values()):
        return
    lines.append("")
    lines.append("⚖️ **Баланс стихий**")
    emoji = {"Огонь": "🔥", "Земля": "🌍", "Воздух": "💨", "Вода": "💧"}
    for element in ("Огонь", "Земля", "Воздух", "Вода"):
        n = balance.get(element, 0)
        lines.append(f"• {emoji.get(element, '')} {element}: {n}")
    top = max(balance.values())
    dominant = [el for el in ("Огонь", "Земля", "Воздух", "Вода") if balance.get(el, 0) == top]
    if top and len(dominant) == 1:
        lines.append(f"**Преобладает: {dominant[0]}.**")


def _block_aspects(aspects: list[Any], lines: list[str]) -> None:
    """🔗 Аспекты, разделённые на гармоничные и напряжённые."""
    harmonious: list[str] = []
    tense: list[str] = []
    other: list[str] = []
    for asp in aspects:
        if not isinstance(asp, dict):
            continue
        p1 = asp.get("planet1", "?")
        p2 = asp.get("planet2", "?")
        name = str(asp.get("aspect", "?"))
        # Защитная сетка: не показываем аспект с непереводимым (латиница/_) именем точки.
        if not (is_display_safe_name(p1) and is_display_safe_name(p2)):
            continue
        orb = asp.get("orb")
        orb_s = f" (орб {orb}°)" if isinstance(orb, (int, float)) else ""
        line = f"• {p1} — {p2}: {name}{orb_s}"
        if name in _HARMONIOUS_ASPECTS:
            harmonious.append(line)
        elif name in _TENSE_ASPECTS:
            tense.append(line)
        else:
            other.append(line)
    if not (harmonious or tense or other):
        return
    lines.append("")
    lines.append("🔗 **Аспекты**")
    if harmonious:
        lines.append("**Гармоничные:**")
        lines.extend(harmonious[:6])
    if tense:
        lines.append("**Напряжённые:**")
        lines.extend(tense[:6])
    if other and not (harmonious or tense):
        lines.extend(other[:6])


def _block_contrast(natal_data: dict[str, Any], lines: list[str]) -> None:
    """🎭 Контраст «Снаружи <Асцендент>, внутри <Солнце>»."""
    asc = natal_data.get("ascendant")
    sun = natal_data.get("sun_sign")
    if not (asc and sun):
        return
    lines.append("")
    lines.append(f"🎭 **Снаружи {asc}, внутри {sun}**")
    lines.append(
        f"Мир видит вашу маску ({asc}: {_ASC_MEANING}), а движет вами суть ({sun}: {_SUN_MEANING})."
    )


def render_natal_chart_text(natal_data: dict[str, Any]) -> str:
    """Блочный человекочитаемый разбор сохранённой натальной карты (без LLM).

    Структура повторяет подачу референсного сервиса: оси → светила → ретро →
    планеты по знакам → планеты по домам → баланс стихий → аспекты → контраст.
    Устойчив к старым сохранённым данным (без осей/стихий): недостающие блоки
    просто пропускаются, баланс стихий при отсутствии считается на лету.
    """
    system = natal_data.get("chart_system", "")
    lines: list[str] = ["🪐 **Ваша натальная карта**"]
    if system:
        lines.append(f"**Система:** {system}")

    planets_raw = natal_data.get("planets")
    planets: dict[str, Any] = planets_raw if isinstance(planets_raw, dict) else {}

    _block_axes(natal_data, lines)
    _block_luminaries(natal_data, lines)
    if planets:
        _block_retrograde(planets, lines)
        _block_planets_by_sign(planets, lines)
        _block_planets_by_house(planets, lines)
        _block_elements(natal_data, planets, lines)

    aspects = natal_data.get("aspects")
    if isinstance(aspects, list) and aspects:
        _block_aspects(aspects, lines)

    _block_contrast(natal_data, lines)

    lines.append("")
    lines.append("Нажмите «Углублённый разбор», чтобы я растолковал карту подробно.")
    return "\n".join(lines)


def render_natal_chart_message(natal_data: dict[str, Any]) -> OutboundMessage:
    return OutboundMessage(text=render_natal_chart_text(natal_data), buttons=natal_nav_buttons())


def _arc_str(arc: object) -> str:
    if isinstance(arc, dict):
        n = arc.get("n")
        name = arc.get("name", "?")
        return f"{n} ({name})" if n is not None else str(name)
    return str(arc)


def render_destiny_matrix_text(dm: dict[str, Any]) -> str:
    """Человекочитаемый рендер сохранённой Матрицы Судьбы (без LLM).

    Показываем ядро (личностный квадрат), предназначение и денежный/любовный каналы —
    это КОДЫ (арканы), их толкование даёт углублённый разбор через ИИ (кнопка ниже).
    """
    lines: list[str] = ["🌌 **Ваша Карта судьбы** (Матрица Судьбы)"]
    bd = dm.get("birth_date")
    if bd:
        lines.append(f"**По дате рождения:** {bd}")
    lines.append("")
    lines.append("**Личностный квадрат:**")
    lines.append(f"• Портрет (день): {_arc_str(dm.get('day'))}")
    lines.append(f"• Таланты рода (месяц): {_arc_str(dm.get('month'))}")
    lines.append(f"• Материальная карма (год): {_arc_str(dm.get('year'))}")
    lines.append(f"• Кармическая задача: {_arc_str(dm.get('karma'))}")
    lines.append(f"• ⭐ Зона комфорта (центр): {_arc_str(dm.get('comfort_zone'))}")

    purpose = dm.get("purpose")
    if isinstance(purpose, dict):
        lines.append("")
        lines.append("**Предназначение:**")
        lines.append(f"• Личное (до ~40): {_arc_str(purpose.get('personal'))}")
        lines.append(f"• Социальное (~40–60): {_arc_str(purpose.get('social'))}")
        lines.append(f"• Духовное (60+): {_arc_str(purpose.get('spiritual'))}")

    money = dm.get("money_line")
    love = dm.get("relationship_line")
    if isinstance(money, dict) or isinstance(love, dict):
        lines.append("")
        lines.append("**Каналы:**")
        if isinstance(money, dict):
            lines.append(f"• 💰 Деньги: итог {_arc_str(money.get('total'))}")
        if isinstance(love, dict):
            lines.append(f"• ❤️ Отношения: итог {_arc_str(love.get('total'))}")

    lines.append("")
    lines.append("Нажмите «Разбор судьбы», чтобы я растолковал арканы подробно.")
    return "\n".join(lines)


def render_destiny_matrix_message(dm: dict[str, Any]) -> OutboundMessage:
    return OutboundMessage(text=render_destiny_matrix_text(dm), buttons=matrix_nav_buttons())


def numerology_nav_buttons() -> list[list[dict[str, str]]]:
    """Навигация под нумерологией."""
    return [
        [_btn("🔮 Разбор чисел", "mdl:numerology")],
        [_btn("🪐 Натальная карта", "/natal"), _btn("🌌 Карта судьбы", "/matrix")],
        [_btn("📊 Прогноз", "mdl:forecast_menu"), _btn("👤 Профиль", "mdl:profile")],
    ]


# Порядок и подписи чисел для рендера/профиля (роли, не трактовки — их даёт база знаний).
_NUMEROLOGY_ROWS: tuple[tuple[str, str], ...] = (
    ("life_path", "🛤️ Жизненный путь"),
    ("expression", "✨ Выражение (судьба)"),
    ("soul_urge", "💗 Число души"),
    ("personality", "🎭 Число личности"),
    ("birthday", "🎂 День рождения"),
    ("maturity", "🌟 Число зрелости"),
)


def _num_str(n: object) -> str:
    """'11 ✦ мастер-число' / '5' / '—' — число для человекочитаемого рендера."""
    if not isinstance(n, int):
        return "—"
    from mandala.astro.numerology import is_master

    return f"{n} ✦ мастер-число" if is_master(n) else str(n)


def render_numerology_text(data: dict[str, Any]) -> str:
    """Человекочитаемый рендер сохранённой нумерологии (без LLM).

    Показываем ЧИСЛА и их роли; их толкование (характер, задачи, смысл мастер-чисел)
    даёт углублённый разбор через ИИ (кнопка ниже). Устойчив к отсутствию имени —
    числа имени просто пропускаются.
    """
    lines: list[str] = ["🔢 **Ваша нумерология** (пифагорейская)"]
    name = data.get("full_name")
    bd = data.get("birth_date")
    meta = " · ".join(str(x) for x in (name, bd) if x)
    if meta:
        lines.append(f"**По имени и дате:** {meta}")
    raw_numbers = data.get("numbers")
    numbers: dict[str, Any] = raw_numbers if isinstance(raw_numbers, dict) else {}
    lines.append("")
    for key, label in _NUMEROLOGY_ROWS:
        value = numbers.get(key)
        if value is None:
            continue
        lines.append(f"• {label}: {_num_str(value)}")
    if not data.get("has_name"):
        lines.append("")
        lines.append(
            "ℹ️ Числа имени (выражение, душа, личность, зрелость) не рассчитаны — "
            "не указано имя. Добавьте имя в профиль для полного разбора."
        )
    lines.append("")
    lines.append("Нажмите «Разбор чисел», чтобы я растолковал числа подробно.")
    return "\n".join(lines)


def render_numerology_message(data: dict[str, Any]) -> OutboundMessage:
    return OutboundMessage(text=render_numerology_text(data), buttons=numerology_nav_buttons())


__all__ = [
    "matrix_nav_buttons",
    "natal_nav_buttons",
    "numerology_nav_buttons",
    "render_destiny_matrix_message",
    "render_destiny_matrix_text",
    "render_natal_chart_message",
    "render_natal_chart_text",
    "render_numerology_message",
    "render_numerology_text",
]
