"""Именованные валидаторы шагов анкеты (JSON задаёт ``kind`` и параметры)."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

Validator = Callable[[str], str | None]

_TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")


def _validate_min_len(min_len: int, label: str) -> Validator:
    def _v(raw: str) -> str | None:
        t = raw.strip()
        if len(t) < min_len:
            return f"нужно не короче {min_len} символов ({label})"
        return None

    return _v


def _validate_birth_time(raw: str) -> str | None:
    t = raw.strip().lower()
    if t in ("не знаю", "неизвестно", "-", "нет"):
        return None
    m = _TIME_RE.match(raw.strip())
    if not m:
        return "укажите время рождения как ЧЧ:ММ (например 14:30) или напишите «не знаю»"
    h, mm = int(m.group(1)), int(m.group(2))
    if h > 23 or mm > 59:
        return "некорректное время: часы 0–23, минуты 0–59"
    return None


def _validate_birth_place(raw: str) -> str | None:
    t = raw.strip()
    if t.startswith("/"):
        return "команды вида /start не подходят — напишите название города текстом"
    return _validate_min_len(2, "место рождения")(raw)


def validator_from_spec(spec: object) -> Validator:
    """Собрать валидатор из объекта JSON ``validator``."""
    if spec is None:
        msg = "validator is required"
        raise ValueError(msg)
    if not isinstance(spec, Mapping):
        msg = "validator must be an object"
        raise TypeError(msg)
    raw_kind = spec.get("kind")
    if not isinstance(raw_kind, str) or not raw_kind.strip():
        msg = "validator.kind must be a non-empty string"
        raise ValueError(msg)
    kind = raw_kind.strip()

    if kind == "min_len":
        raw_min = spec.get("min_len")
        if not isinstance(raw_min, int) or isinstance(raw_min, bool):
            msg = "validator.min_len must be an integer"
            raise ValueError(msg)
        if raw_min < 0:
            msg = "validator.min_len must be >= 0"
            raise ValueError(msg)
        label_raw = spec.get("label", "")
        label = label_raw if isinstance(label_raw, str) else str(label_raw)
        return _validate_min_len(raw_min, label or "поле")

    if kind == "birth_place":
        return _validate_birth_place

    if kind == "birth_time":
        return _validate_birth_time

    msg = f"unknown validator kind: {kind!r}"
    raise ValueError(msg)
