"""Интеграции с LLM и image API (тикеты 11, 14–15)."""

from mandala.llm.client import TextCompletionClient
from mandala.llm.config import (
    LlmConfigProvider,
    LlmEnvSettings,
    ResolvedLlmConfig,
    VerticalLlmOverride,
    bundled_overrides_path,
    load_vertical_overrides,
)
from mandala.llm.exceptions import LlmProviderError
from mandala.llm.factory import (
    create_image_client_for_vertical,
    create_stub_image_client_for_vertical,
    create_text_client_for_vertical,
)
from mandala.llm.image_generation import (
    ImageGenerationClient,
    ImageGenerationResult,
    StubImageGenerationClient,
)
from mandala.llm.openai_compatible import OpenAICompatibleTextClient
from mandala.llm.types import ChatMessage, ChatRole

__all__ = [
    "ChatMessage",
    "ChatRole",
    "ImageGenerationClient",
    "ImageGenerationResult",
    "StubImageGenerationClient",
    "create_image_client_for_vertical",
    "create_stub_image_client_for_vertical",
    "create_text_client_for_vertical",
    "LlmConfigProvider",
    "LlmEnvSettings",
    "LlmProviderError",
    "OpenAICompatibleTextClient",
    "ResolvedLlmConfig",
    "TextCompletionClient",
    "VerticalLlmOverride",
    "bundled_overrides_path",
    "load_vertical_overrides",
]
