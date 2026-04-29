"""Загрузка настроек LLM из окружения и опциональные переопределения per ``vertical_id``."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_ENV_BASE_URL = "LLM_BASE_URL"
_ENV_API_KEY = "LLM_API_KEY"
_ENV_MODEL = "LLM_MODEL"
_ENV_OVERRIDES_PATH = "LLM_VERTICAL_OVERRIDES_PATH"


class LlmEnvSettings(BaseModel):
    """Глобальные дефолты из переменных окружения (OpenAI-compatible endpoint)."""

    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    default_model: str = Field(min_length=1)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LlmEnvSettings:
        env = dict(environ if environ is not None else os.environ)
        base = env.get(_ENV_BASE_URL, "").strip()
        key = env.get(_ENV_API_KEY, "").strip()
        model = env.get(_ENV_MODEL, "").strip()
        missing = [
            name
            for name, val in (
                (_ENV_BASE_URL, base),
                (_ENV_API_KEY, key),
                (_ENV_MODEL, model),
            )
            if not val
        ]
        if missing:
            msg = f"missing or empty environment variables: {', '.join(missing)}"
            raise ValueError(msg)
        return cls(base_url=base, api_key=key, default_model=model)


class VerticalLlmOverride(BaseModel):
    """Поля вертикали переопределяют только заданные значения (остальное из ``LlmEnvSettings``)."""

    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ResolvedLlmConfig(BaseModel):
    """Эффективные параметры для создания ``OpenAICompatibleTextClient``."""

    base_url: str
    api_key: str
    model: str


def bundled_overrides_path() -> Path:
    """Путь к JSON с дефолтными переопределениями в пакете."""
    return Path(__file__).resolve().parent / "vertical_overrides.json"


def load_vertical_overrides(
    *,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, VerticalLlmOverride]:
    """Читает JSON ``{ "vertical_slug": { "model": "...", ... } }``.

    Порядок: явный ``path``; иначе ``LLM_VERTICAL_OVERRIDES_PATH``
    (если задан — файл обязан существовать); иначе :func:`bundled_overrides_path`, если
    существует; иначе пустой словарь.
    """
    env = dict(environ if environ is not None else os.environ)
    explicit_env = False
    candidate: Path | None = path
    if candidate is None:
        raw = env.get(_ENV_OVERRIDES_PATH, "").strip()
        if raw:
            explicit_env = True
            candidate = Path(raw).expanduser()

    if candidate is not None:
        if not candidate.is_file():
            if explicit_env or path is not None:
                msg = f"LLM vertical overrides file not found: {candidate}"
                raise FileNotFoundError(msg)
            candidate = None

    if candidate is None:
        bundled = bundled_overrides_path()
        candidate = bundled if bundled.is_file() else None

    if candidate is None or not candidate.is_file():
        return {}

    raw_data = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(raw_data, dict):
        msg = "vertical overrides JSON root must be an object"
        raise ValueError(msg)

    out: dict[str, VerticalLlmOverride] = {}
    for key, val in raw_data.items():
        if not isinstance(key, str):
            continue
        slug = key.strip()
        if not slug:
            continue
        if not isinstance(val, dict):
            continue
        out[slug] = VerticalLlmOverride.model_validate(_coerce_override_dict(val))
    return out


def _coerce_override_dict(val: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in val.items() if k in ("model", "base_url", "api_key")}


class LlmConfigProvider:
    """Слияние env и переопределений по ``vertical_id`` (slug из seed / webhook)."""

    __slots__ = ("_env", "_overrides")

    def __init__(
        self,
        env: LlmEnvSettings,
        overrides: Mapping[str, VerticalLlmOverride] | None = None,
    ) -> None:
        self._env = env
        self._overrides = dict(overrides or {})

    def resolve(self, vertical_id: str) -> ResolvedLlmConfig:
        vid = vertical_id.strip()
        o = self._overrides.get(vid)
        base_url = o.base_url if o and o.base_url else self._env.base_url
        api_key = o.api_key if o and o.api_key else self._env.api_key
        model = o.model if o and o.model else self._env.default_model
        return ResolvedLlmConfig(
            base_url=base_url.strip(),
            api_key=api_key.strip(),
            model=model.strip(),
        )
