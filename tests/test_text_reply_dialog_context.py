"""Юнит-тесты сборки истории для LLM (тикет 17)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from mandala.llm import ChatMessage
from mandala.repositories.messages import MessageDTO
from mandala.services.text_reply import _message_rows_to_chat_messages


def test_message_rows_to_chat_messages_chronological_and_skip_empty() -> None:
    uid = uuid4()
    now = datetime.now(tz=UTC)
    rows = [
        MessageDTO(
            id=uuid4(),
            user_id=uid,
            vertical_id="therapy",
            role="user",
            content_kind="text",
            content_text="третий",
            content_meta=None,
            created_at=now,
        ),
        MessageDTO(
            id=uuid4(),
            user_id=uid,
            vertical_id="therapy",
            role="assistant",
            content_kind="text",
            content_text="второй ответ",
            content_meta=None,
            created_at=now,
        ),
        MessageDTO(
            id=uuid4(),
            user_id=uid,
            vertical_id="therapy",
            role="user",
            content_kind="text",
            content_text="первый",
            content_meta=None,
            created_at=now,
        ),
    ]
    chat = _message_rows_to_chat_messages(rows)
    assert chat == [
        ChatMessage(role="user", content="первый"),
        ChatMessage(role="assistant", content="второй ответ"),
        ChatMessage(role="user", content="третий"),
    ]


def test_message_rows_skips_system_and_blank_text() -> None:
    uid = uuid4()
    now = datetime.now(tz=UTC)
    rows = [
        MessageDTO(
            id=uuid4(),
            user_id=uid,
            vertical_id="x",
            role="user",
            content_kind="text",
            content_text="  hi  ",
            content_meta=None,
            created_at=now,
        ),
        MessageDTO(
            id=uuid4(),
            user_id=uid,
            vertical_id="x",
            role="assistant",
            content_kind="text",
            content_text="   ",
            content_meta=None,
            created_at=now,
        ),
        MessageDTO(
            id=uuid4(),
            user_id=uid,
            vertical_id="x",
            role="system",
            content_kind="text",
            content_text="ignored",
            content_meta=None,
            created_at=now,
        ),
    ]
    chat = _message_rows_to_chat_messages(rows)
    assert chat == [ChatMessage(role="user", content="hi")]
