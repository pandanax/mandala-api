"""Фабрика HTTP-клиента LLM с учётом ``vertical_id`` (тикеты 11–12); image API — тикет 15."""

from __future__ import annotations

import logging

from mandala.llm.config import LlmConfigProvider, LlmEnvSettings, load_vertical_overrides
from mandala.llm.image_env import ImageEnvSettings
from mandala.llm.image_generation import ImageGenerationClient, StubImageGenerationClient
from mandala.llm.openai_compatible import OpenAICompatibleTextClient
from mandala.llm.openai_compatible_image import OpenAICompatibleImageClient

logger = logging.getLogger(__name__)


def create_text_client_for_vertical(vertical_id: str) -> OpenAICompatibleTextClient:
    """Собрать клиент из env и опциональных переопределений для slug вертикали."""
    settings = LlmEnvSettings.from_env()
    overrides = load_vertical_overrides()
    provider = LlmConfigProvider(settings, overrides)
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
