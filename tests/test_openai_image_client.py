"""Юнит-тесты HTTP-клиента изображений (тикет 15)."""

from __future__ import annotations

import json

import httpx

from mandala.llm.openai_compatible_image import OpenAICompatibleImageClient


def test_openai_compatible_image_client_parses_url() -> None:
    body = json.dumps(
        {
            "created": 1,
            "data": [{"url": "https://cdn.example.com/img.png", "revised_prompt": "x"}],
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/images/generations")
        assert "Bearer sk-test" in request.headers.get("authorization", "")
        payload = json.loads(request.content.decode())
        assert payload["model"] == "dall-e-3"
        assert payload["response_format"] == "url"
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)
    with OpenAICompatibleImageClient(
        base_url="https://api.example/v1",
        api_key="sk-test",
        default_model="dall-e-3",
        client=httpx.Client(transport=transport),
    ) as cli:
        r = cli.generate("a red circle")

    assert r.image_url == "https://cdn.example.com/img.png"
    assert r.stub_ref is None
    assert "circle" in r.prompt_echo or "red" in r.prompt_echo
