"""Настройки image API из окружения (тикет 15: OpenAI-compatible ``/images/generations``)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field

_PROVIDER_ENV = "IMAGE_GENERATION_PROVIDER"
_BASE_ENV = "IMAGE_BASE_URL"
_KEY_ENV = "IMAGE_API_KEY"
_MODEL_ENV = "IMAGE_MODEL"
_FALLBACK_BASE = "LLM_BASE_URL"
_FALLBACK_KEY = "LLM_API_KEY"

ImageProviderKind = Literal["stub", "openai_compatible"]


class ResolvedImageConfig(BaseModel):
    """Параметры HTTP-клиента изображений."""

    base_url: str
    api_key: str
    model: str


class ImageEnvSettings(BaseModel):
    """Читает ``IMAGE_*`` с запасным использованием ``LLM_*`` для URL и ключа."""

    provider: ImageProviderKind = Field(default="stub")
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model: str = Field(min_length=1)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ImageEnvSettings:
        env = dict(environ if environ is not None else os.environ)
        raw_p = (env.get(_PROVIDER_ENV) or "stub").strip().lower()
        if raw_p in ("", "stub", "none"):
            provider: ImageProviderKind = "stub"
        elif raw_p == "openai_compatible":
            provider = "openai_compatible"
        else:
            msg = f"{_PROVIDER_ENV} must be stub or openai_compatible, got {raw_p!r}"
            raise ValueError(msg)

        base = (env.get(_BASE_ENV) or env.get(_FALLBACK_BASE) or "").strip()
        key = (env.get(_KEY_ENV) or env.get(_FALLBACK_KEY) or "").strip()
        model = (env.get(_MODEL_ENV) or "").strip() or "dall-e-3"

        if provider == "openai_compatible":
            missing = [n for n, v in ((_BASE_ENV, base), (_KEY_ENV, key)) if not v]
            if missing:
                msg = (
                    f"{_PROVIDER_ENV}=openai_compatible requires {_BASE_ENV} or {_FALLBACK_BASE}, "
                    f"and {_KEY_ENV} or {_FALLBACK_KEY}"
                )
                raise ValueError(msg)

        if provider == "stub":
            base = base or "https://api.openai.com/v1"
            key = key or "placeholder"

        return cls(provider=provider, base_url=base, api_key=key, model=model)

    def resolve_config(self) -> ResolvedImageConfig | None:
        """Для ``stub`` — ``None`` (используется :class:`.StubImageGenerationClient`)."""
        if self.provider == "stub":
            return None
        return ResolvedImageConfig(
            base_url=self.base_url.rstrip("/"),
            api_key=self.api_key.strip(),
            model=self.model.strip(),
        )
