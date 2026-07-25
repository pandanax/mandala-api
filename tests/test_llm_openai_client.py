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
    load_env_model_overrides,
    load_vertical_overrides,
    log_effective_models,
)
from mandala.llm.config import (
    MODEL_SOURCE_ENV_DEFAULT,
    MODEL_SOURCE_ENV_VERTICAL,
    MODEL_SOURCE_OVERRIDES,
    bundled_overrides_path,
)


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
    # Конкретные имена моделей задаются в bundled JSON и могут меняться вместе с провайдером
    # (см. src/mandala/llm/vertical_overrides.json). Главное — что override применился
    # и пришла не дефолтная модель из env.
    assert astrology.model and astrology.model != "default-model"
    assert astrology.base_url == "https://default/v1"
    therapy = provider.resolve("therapy")
    assert therapy.model and therapy.model != "default-model"


def test_load_overrides_explicit_missing_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        load_vertical_overrides(path=missing)


def _env() -> LlmEnvSettings:
    return LlmEnvSettings(
        base_url="https://default/v1",
        api_key="global-key",
        default_model="default-model",
    )


def test_load_env_model_overrides_parses_prefix() -> None:
    parsed = load_env_model_overrides(
        {
            "LLM_MODEL": "global",  # «голый» — не суффикс, игнор
            "LLM_MODEL_ASTROLOGY": " gpt-astro ",  # обрезаем пробелы
            "LLM_MODEL_THERAPY": "",  # пустое значение — игнор
            "UNRELATED": "x",
        }
    )
    assert parsed == {"astrology": "gpt-astro"}


def test_resolve_falls_back_to_env_default_when_no_override() -> None:
    # Нет ни bundled JSON, ни per-vertical env — модель берётся из LLM_MODEL.
    provider = LlmConfigProvider(_env())
    resolved = provider.resolve("astrology")
    assert resolved.model == "default-model"
    assert provider.model_source("astrology") == MODEL_SOURCE_ENV_DEFAULT


def test_resolve_uses_bundled_json_override() -> None:
    overrides = load_vertical_overrides(path=bundled_overrides_path())
    provider = LlmConfigProvider(_env(), overrides)
    resolved = provider.resolve("astrology")
    assert resolved.model and resolved.model != "default-model"
    assert provider.model_source("astrology") == MODEL_SOURCE_OVERRIDES


def test_env_per_vertical_override_wins_over_bundled_json() -> None:
    # Явный LLM_MODEL_ASTROLOGY перебивает bundled JSON — «поменял в env → сработало».
    overrides = load_vertical_overrides(path=bundled_overrides_path())
    env_models = load_env_model_overrides({"LLM_MODEL_ASTROLOGY": "operator-choice"})
    provider = LlmConfigProvider(_env(), overrides, env_models)
    assert provider.resolve("astrology").model == "operator-choice"
    assert provider.model_source("astrology") == MODEL_SOURCE_ENV_VERTICAL
    # therapy без per-vertical env остаётся на дефолте из bundled JSON.
    assert provider.model_source("therapy") == MODEL_SOURCE_OVERRIDES
    assert provider.resolve("therapy").model != "operator-choice"


def test_bundled_defaults_unchanged_when_nothing_overridden() -> None:
    # Регресс: без per-vertical env astrology/therapy = deepseek-v4-flash из bundled JSON.
    overrides = load_vertical_overrides(path=bundled_overrides_path())
    provider = LlmConfigProvider(_env(), overrides)
    assert provider.resolve("astrology").model == "deepseek-v4-flash"
    assert provider.resolve("therapy").model == "deepseek-v4-flash"


def test_known_vertical_ids_unions_overrides_and_env() -> None:
    overrides = load_vertical_overrides(path=bundled_overrides_path())
    env_models = load_env_model_overrides({"LLM_MODEL_TAROT": "some-model"})
    provider = LlmConfigProvider(_env(), overrides, env_models)
    assert provider.known_vertical_ids() == ["astrology", "tarot", "therapy"]


def test_log_effective_models_reports_each_vertical(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://default/v1")
    monkeypatch.setenv("LLM_API_KEY", "global-key")
    monkeypatch.setenv("LLM_MODEL", "default-model")
    monkeypatch.setenv("LLM_MODEL_ASTROLOGY", "operator-choice")
    monkeypatch.delenv("LLM_VERTICAL_OVERRIDES_PATH", raising=False)

    with caplog.at_level("INFO"):
        log_effective_models()

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "vertical=astrology model=operator-choice source=env_vertical" in text
    assert "vertical=therapy model=deepseek-v4-flash source=vertical_overrides" in text
    assert "llm default model (LLM_MODEL) = default-model" in text


def test_log_effective_models_skips_without_env(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    with caplog.at_level("WARNING"):
        log_effective_models()  # не должно бросать
    assert any("skipping" in r.getMessage() for r in caplog.records)


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
