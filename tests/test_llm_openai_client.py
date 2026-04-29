"""Тесты LLM-клиента: HTTP через ``httpx.MockTransport``, конфиг, доменные ошибки."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from mandala.llm import (
    ChatMessage,
    LlmConfigProvider,
    LlmEnvSettings,
    LlmProviderError,
    OpenAICompatibleTextClient,
    load_vertical_overrides,
)
from mandala.llm.config import bundled_overrides_path


def _chat_completion_json(content: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-test",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


def test_openai_compatible_complete_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode())
        assert body["model"] == "gpt-test"
        assert len(body["messages"]) == 1
        return httpx.Response(200, json=_chat_completion_json("Привет"))

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = OpenAICompatibleTextClient(
            base_url="https://example.test/v1",
            api_key="sk-test",
            default_model="gpt-test",
            client=http_client,
        )
        out = client.complete([ChatMessage(role="user", content="Здравствуй")])

    assert out == "Привет"


def test_openai_compatible_http_error_maps_to_domain_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "invalid_api_key", "type": "invalid_request_error"}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = OpenAICompatibleTextClient(
            base_url="https://example.test/v1/",
            api_key="bad",
            default_model="m",
            client=http_client,
        )
        with pytest.raises(LlmProviderError) as ei:
            client.complete([ChatMessage(role="user", content="x")])

    err = ei.value
    assert err.status_code == 401
    assert err.provider_detail == "invalid_api_key"


def test_openai_compatible_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = OpenAICompatibleTextClient(
            base_url="https://example.test/v1",
            api_key="k",
            default_model="m",
            client=http_client,
        )
        with pytest.raises(LlmProviderError) as ei:
            client.complete([ChatMessage(role="user", content="a")])

    assert "LLM HTTP request failed" in str(ei.value)


def test_llm_env_settings_from_env() -> None:
    env = {
        "LLM_BASE_URL": "https://api.example/v1",
        "LLM_API_KEY": "secret",
        "LLM_MODEL": "model-x",
    }
    s = LlmEnvSettings.from_env(env)
    assert s.base_url == "https://api.example/v1"
    assert s.default_model == "model-x"


def test_llm_env_settings_missing_raises() -> None:
    with pytest.raises(ValueError, match="LLM_MODEL"):
        LlmEnvSettings.from_env({"LLM_BASE_URL": "x", "LLM_API_KEY": "y"})


def test_config_provider_resolves_vertical_overrides() -> None:
    env = LlmEnvSettings(
        base_url="https://default/v1",
        api_key="global-key",
        default_model="default-model",
    )
    overrides = load_vertical_overrides(path=bundled_overrides_path())
    provider = LlmConfigProvider(env, overrides)
    astrology = provider.resolve("astrology")
    assert astrology.model == "gpt-4o"
    assert astrology.base_url == "https://default/v1"
    therapy = provider.resolve("therapy")
    assert therapy.model == "gpt-4o-mini"


def test_load_overrides_explicit_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        load_vertical_overrides(path=missing)


@pytest.mark.llm_live
def test_live_openai_compatible_optional() -> None:
    """Opt-in: ``LLM_LIVE_TEST=1`` и валидные ``LLM_*`` в окружении.

    Без флага тест пропускается (см. README).
    """
    if os.environ.get("LLM_LIVE_TEST", "").strip() != "1":
        pytest.skip("set LLM_LIVE_TEST=1 to run live LLM call")

    try:
        settings = LlmEnvSettings.from_env()
    except ValueError:
        pytest.skip("LLM_BASE_URL, LLM_API_KEY, LLM_MODEL required for live test")

    client = OpenAICompatibleTextClient(
        base_url=settings.base_url,
        api_key=settings.api_key,
        default_model=settings.default_model,
    )
    try:
        text = client.complete(
            [ChatMessage(role="user", content="Reply with exactly: OK")],
            max_tokens=16,
        )
    finally:
        client.close()

    assert "OK" in text.upper()
