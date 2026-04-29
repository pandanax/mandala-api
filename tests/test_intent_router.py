"""Юнит-тесты классификатора намерений после анкеты (тикет 14)."""

from __future__ import annotations

import pytest

from mandala.services.intent_router import image_prompt_from_user_text, post_intake_intent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/image cat", "image"),
        ("/IMAGE x", "image"),
        ("/picture", "image"),
        ("нарисуй солнце", "image"),
        ("Draw something", "image"),
        ("просто текст", "text"),
        ("", "text"),
        (None, "text"),
    ],
)
def test_post_intake_intent(text: str | None, expected: str) -> None:
    assert post_intake_intent(text) == expected


def test_image_prompt_from_user_text() -> None:
    assert image_prompt_from_user_text("/image  космос ") == "космос"
    assert image_prompt_from_user_text("нарисуй  дом") == "дом"
    assert image_prompt_from_user_text("draw x") == "x"
