"""Заглушка клиента изображений (тикет 14)."""

from __future__ import annotations

from mandala.llm import StubImageGenerationClient


def test_stub_image_generation_client() -> None:
    c = StubImageGenerationClient()
    r = c.generate("  hello  ")
    assert r.stub_ref == "stub14"
    assert r.image_url is None
    assert r.prompt_echo == "hello"
