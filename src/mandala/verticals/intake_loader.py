"""Загрузка цепочек шагов анкеты из JSON (аналог ``load_vertical_overrides`` для LLM)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from mandala.verticals.intake_validators import Validator, validator_from_spec

_ENV_INTAKE_PATH = "MANDALA_INTAKE_STEPS_PATH"


@dataclass(frozen=True, slots=True)
class IntakeStep:
    """Один шаг анкеты: ключ в ``agent_card`` и валидатор текста ответа."""

    field_key: str
    prompt: str
    validate: Validator


def bundled_intake_steps_path() -> Path:
    """Путь к JSON с цепочками по умолчанию в пакете."""
    return Path(__file__).resolve().parent / "intake_steps.json"


def resolve_intake_steps_json_path(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Путь к JSON или ``None``, если файла нет (нет анкет в рантайме)."""
    env = dict(environ if environ is not None else os.environ)
    explicit_env = False
    candidate: Path | None = path
    if candidate is None:
        raw = env.get(_ENV_INTAKE_PATH, "").strip()
        if raw:
            explicit_env = True
            candidate = Path(raw).expanduser()

    if candidate is not None:
        if not candidate.is_file():
            if explicit_env or path is not None:
                msg = f"intake steps JSON file not found: {candidate}"
                raise FileNotFoundError(msg)
            candidate = None

    if candidate is None:
        bundled = bundled_intake_steps_path()
        candidate = bundled if bundled.is_file() else None

    if candidate is None or not candidate.is_file():
        return None
    return candidate


def load_intake_steps_registry(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, tuple[IntakeStep, ...]]:
    """Читает JSON ``{ "vertical_slug": [ { "field_key", "prompt", "validator" }, ... ] }``.

    Порядок разрешения пути: явный ``path``; иначе ``MANDALA_INTAKE_STEPS_PATH`` (если задан —
    файл обязан существовать); иначе :func:`bundled_intake_steps_path`, если существует;
    иначе пустой словарь.
    """
    candidate = resolve_intake_steps_json_path(path=path, environ=environ)
    if candidate is None:
        return {}

    raw_data = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        msg = "intake steps JSON root must be an object"
        raise ValueError(msg)

    out: dict[str, tuple[IntakeStep, ...]] = {}
    for key, val in raw_data.items():
        if not isinstance(key, str):
            continue
        slug = key.strip()
        if not slug:
            continue
        if not isinstance(val, list):
            msg = f"intake steps for {slug!r} must be an array"
            raise ValueError(msg)
        steps: list[IntakeStep] = []
        for i, item in enumerate(val):
            step = _parse_step(slug, i, item)
            steps.append(step)
        out[slug] = tuple(steps)
    return out


def _parse_step(vertical_slug: str, index: int, item: object) -> IntakeStep:
    if not isinstance(item, dict):
        msg = f"{vertical_slug}[{index}]: step must be an object"
        raise ValueError(msg)
    fk = item.get("field_key")
    if not isinstance(fk, str) or not fk.strip():
        msg = f"{vertical_slug}[{index}]: field_key is required"
        raise ValueError(msg)
    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        msg = f"{vertical_slug}[{index}]: prompt is required"
        raise ValueError(msg)
    v_spec = item.get("validator")
    validate = validator_from_spec(v_spec)
    return IntakeStep(field_key=fk.strip(), prompt=prompt.strip(), validate=validate)
