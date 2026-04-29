"""Типы сообщений для текстовых completions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """Одно сообщение в формате Chat Completions (OpenAI-compatible)."""

    role: ChatRole
    content: str = Field(min_length=1)
