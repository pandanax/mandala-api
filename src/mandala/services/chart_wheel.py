"""Детерминированный рендер КОЛЕСА натальной карты в PNG-байты (не LLM, не нейросеть).

Колесо рисуется чистой геометрией из посчитанных позиций: kerykeion строит
SVG-колесо (`KerykeionChartSVG.makeWheelOnlySVG`), затем SVG растеризуется в PNG
через ``cairosvg`` (нужен системный libcairo — см. ``Containerfile``).

**Грабли с цветами.** kerykeion задаёт ВСЕ цвета через CSS-переменные
``var(--kerykeion-*)``, объявленные в блоке ``:root`` внутри ``<style>`` самого SVG
(с вложенными ссылками, напр. ``--x: var(--y); --y: #ffbe00``). Многие SVG→PNG
конвертеры (в т.ч. cairosvg) ``var()`` НЕ резолвят → бесцветное/чёрное колесо. Поэтому
перед растеризацией мы разбираем ``:root``, рекурсивно резолвим вложенные ``var()`` до
конкретных ``#hex``/``rgb`` и подставляем их вместо каждого ``var(--…)`` в тексте SVG.
Так цвета гарантированы независимо от конвертера.

Геометрию/школу (тропическая vs сидерическая, дома) даёт общий
:func:`mandala.astro.natal_chart.build_astrological_subject` — тот же subject, что и у
расчёта карты, чтобы колесо и текст не расходились.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import tempfile
from contextlib import redirect_stdout
from io import StringIO

logger = logging.getLogger(__name__)

# Размер растра (квадрат — SVG-колесо квадратное). ~900px даёт чёткое читаемое колесо
# в Telegram при разумном весе файла.
_WHEEL_PX = 900

# var(--name) внутри значения и в теле SVG.
_VAR_REF_RE = re.compile(r"var\(\s*(--[\w-]+)\s*\)")
# Объявление переменной в :root: "--name: value;".
_VAR_DECL_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
_ROOT_BLOCK_RE = re.compile(r":root\s*\{([^}]*)\}", re.DOTALL)


def _resolve_svg_vars(svg: str) -> str:
    """Подставить конкретные цвета вместо всех ``var(--…)`` (см. модульный docstring).

    Разбирает ``:root``, рекурсивно резолвит вложенные ``var()`` до ``#hex``/``rgb`` и
    заменяет каждое вхождение ``var(--name)`` в SVG на значение. Неизвестные/циклические
    переменные схлопываются в ``#000000`` (безопасный дефолт — лучше чёрный штрих, чем
    сырой ``var()``, который конвертер не поймёт).
    """
    root = _ROOT_BLOCK_RE.search(svg)
    raw: dict[str, str] = {}
    if root:
        for name, value in _VAR_DECL_RE.findall(root.group(1)):
            raw[name.strip()] = value.strip()

    def deref(value: str, _seen: frozenset[str] = frozenset()) -> str:
        m = _VAR_REF_RE.fullmatch(value.strip())
        while m is not None:
            key = m.group(1)
            if key in _seen or key not in raw:
                return "#000000"
            _seen = _seen | {key}
            value = raw[key].strip()
            m = _VAR_REF_RE.fullmatch(value)
        return value

    resolved = {name: deref(value) for name, value in raw.items()}

    def repl(match: re.Match[str]) -> str:
        return resolved.get(match.group(1), "#000000")

    return _VAR_REF_RE.sub(repl, svg)


def render_natal_wheel_png(
    birth_date: str,
    birth_time: str,
    birth_place: str,
    system: str = "western",
    *,
    coords: tuple[float, float, str] | None = None,
) -> bytes:
    """Отрисовать колесо натальной карты и вернуть PNG-байты (детерминированно).

    Args:
        birth_date: 'DD.MM.YYYY'
        birth_time: 'HH:MM' или 'unknown'
        birth_place: город/населённый пункт (резолвится геокодером внутри subject'а)
        system: 'western' (тропическая) или 'vedic' (сидерическая Lahiri)
        coords: готовые ``(lat, lng, tz)`` из сохранённой карты — тогда геокодер НЕ
            вызывается (сеть не нужна, ``/natal`` офлайновый и быстрый).

    Returns:
        PNG-байты цветного колеса (сигнатура ``\\x89PNG``).

    Raises:
        ValueError: невалидная дата/время или нерезолвимый город/пояс (из
            ``build_astrological_subject`` — та же строгая семантика, что у расчёта карты).
        Любые ошибки kerykeion/cairosvg пробрасываются — вызывающий код (``/natal``)
            деградирует до текстового рендера без колеса.
    """
    import cairosvg  # type: ignore[import-untyped]  # без py.typed/стабов
    from kerykeion import KerykeionChartSVG

    from mandala.astro.natal_chart import build_astrological_subject

    subject, _time_known, _geo = build_astrological_subject(
        birth_date, birth_time, birth_place, system, coords=coords
    )

    with tempfile.TemporaryDirectory() as out_dir:
        chart = KerykeionChartSVG(subject, chart_type="Natal", new_output_directory=out_dir)
        # kerykeion печатает "SVG Generated Correctly in: …" в stdout — глушим, чтобы не
        # засорять логи/тесты (никакой полезной информации для нас в этом print нет).
        with redirect_stdout(StringIO()):
            chart.makeWheelOnlySVG()
        matches = glob.glob(os.path.join(out_dir, "*Wheel*.svg"))
        if not matches:
            # На всякий случай — любой .svg из каталога.
            matches = glob.glob(os.path.join(out_dir, "*.svg"))
        if not matches:
            raise RuntimeError("kerykeion did not produce a wheel SVG")
        with open(matches[0], encoding="utf-8") as fh:
            svg = fh.read()

    svg = _resolve_svg_vars(svg)
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=_WHEEL_PX,
        output_height=_WHEEL_PX,
    )
    if not isinstance(png, bytes):  # cairosvg возвращает bytes при bytestring-входе
        raise RuntimeError("cairosvg did not return PNG bytes")
    return png


__all__ = ["render_natal_wheel_png"]
