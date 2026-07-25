"""Фабрика HTTP-клиента LLM с учётом ``vertical_id`` (тикеты 11–12); image API — тикет 15."""

from __future__ import annotations

import logging

from mandala.llm.config import (
    LlmConfigProvider,
    LlmEnvSettings,
    load_env_model_overrides,
    load_vertical_overrides,
)
from mandala.llm.image_env import ImageEnvSettings
from mandala.llm.image_generation import ImageGenerationClient, StubImageGenerationClient
from mandala.llm.openai_compatible import OpenAICompatibleTextClient
from mandala.llm.openai_compatible_image import OpenAICompatibleImageClient

logger = logging.getLogger(__name__)


def build_config_provider() -> LlmConfigProvider:
    """Единый сборщик провайдера конфигурации LLM из окружения.

    Единственный источник правды по выбору модели: env (``LLM_MODEL`` + per-vertical
    ``LLM_MODEL_<VERTICAL>``) и файл переопределений (bundled JSON либо
    ``LLM_VERTICAL_OVERRIDES_PATH``). Приоритет — см. :meth:`LlmConfigProvider.resolve`.
    """
    settings = LlmEnvSettings.from_env()
    overrides = load_vertical_overrides()
    env_model_overrides = load_env_model_overrides()
    return LlmConfigProvider(settings, overrides, env_model_overrides)


def log_effective_models(log: logging.Logger | None = None) -> None:
    """Залогировать effective-модель каждой вертикали на старте приложения.

    Делает рассинхрон env↔bundled-JSON сразу видимым: для каждой вертикали печатает модель и
    источник (``env_vertical`` / ``vertical_overrides`` / ``env_default``). При неполном LLM-env
    (например в юнит-тестах) молча выходит — старт приложения не должен падать из-за лога.
    """
    out = log or logger
    try:
        provider = build_config_provider()
    except ValueError as e:
        out.warning("llm effective models: env not fully configured, skipping (%s)", e)
        return
    out.info("llm default model (LLM_MODEL) = %s", provider.default_model)
    for vid in provider.known_vertical_ids():
        resolved = provider.resolve(vid)
        out.info(
            "llm effective model vertical=%s model=%s source=%s",
            vid,
            resolved.model,
            provider.model_source(vid),
        )


def create_text_client_for_vertical(vertical_id: str) -> OpenAICompatibleTextClient:
    """Собрать клиент из env и опциональных переопределений для slug вертикали."""
    provider = build_config_provider()
    resolved = provider.resolve(vertical_id)
    return OpenAICompatibleTextClient(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        default_model=resolved.model,
    )


def create_stub_image_client_for_vertical(vertical_id: str) -> StubImageGenerationClient:
    """Явная заглушка без HTTP (тесты и отключение генерации)."""
    _ = vertical_id
    return StubImageGenerationClient()


def create_image_client_for_vertical(vertical_id: str) -> ImageGenerationClient:
    """Клиент генерации изображений из env (см. README): ``stub`` или ``openai_compatible``.

    При некорректных переменных для ``openai_compatible`` — предупреждение в лог и заглушка.
    ``vertical_id`` зарезервирован под будущие переопределения per-вертикаль
    (см. ``LLM_VERTICAL_OVERRIDES_PATH``).
    """
    _ = vertical_id
    try:
        settings = ImageEnvSettings.from_env()
    except ValueError as e:
        logger.warning("image generation env invalid, using stub: %s", e)
        return StubImageGenerationClient()
    resolved = settings.resolve_config()
    if resolved is None:
        return StubImageGenerationClient()
    return OpenAICompatibleImageClient(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        default_model=resolved.model,
    )
