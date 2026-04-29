"""Классификация намерения после анкеты: текст vs запрос картинки (тикет 14)."""

from __future__ import annotations

from typing import Literal

PostIntakeIntent = Literal["text", "image"]


def post_intake_intent(text: str | None) -> PostIntakeIntent:
    """Грубая эвристика: явные команды и узкие префиксы (меньше ложных срабатываний)."""
    raw = (text or "").strip()
    if not raw:
        return "text"
    low = raw.lower()
    for cmd in ("/image", "/picture"):
        if low.startswith(cmd):
            return "image"
    if low.startswith("нарисуй "):
        return "image"
    if low.startswith("draw "):
        return "image"
    return "text"


def image_prompt_from_user_text(text: str | None) -> str:
    """Выделить промпт для image API из исходного сообщения пользователя."""
    raw = (text or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    for cmd in ("/image", "/picture"):
        if low.startswith(cmd):
            return raw[len(cmd) :].strip()
    if low.startswith("нарисуй "):
        return raw[len("нарисуй ") :].strip()
    if low.startswith("draw "):
        return raw[len("draw ") :].strip()
    return raw
