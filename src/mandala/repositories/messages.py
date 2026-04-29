"""Репозиторий: сообщения (тикет 5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

MessageRole = Literal["user", "assistant", "system"]
ContentKind = Literal["text", "image", "audio", "file", "mixed", "unknown"]


@dataclass(frozen=True)
class MessageDTO:
    id: UUID
    user_id: UUID
    vertical_id: str
    role: str
    content_kind: str | None
    content_text: str | None
    content_meta: dict[str, Any] | None
    created_at: datetime


class MessageRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        role: MessageRole,
        content_text: str | None = None,
        content_kind: ContentKind | None = None,
        content_meta: dict[str, Any] | None = None,
    ) -> UUID:
        """Вставить сообщение.

        ``created_at`` задаётся явно (UTC), чтобы в одной транзакции несколько
        вставок не получали одинаковый ``now()`` и не ломали порядок ``list_recent``.
        """
        created_at = datetime.now(tz=UTC)
        row = self._conn.execute(
            text(
                """
                INSERT INTO messages (
                    user_id, vertical_id, role, content_kind, content_text, content_meta,
                    created_at
                )
                VALUES (
                    :user_id,
                    :vertical_id,
                    CAST(:role AS message_role),
                    CAST(:content_kind AS message_content_kind),
                    :content_text,
                    CAST(:content_meta AS jsonb),
                    :created_at
                )
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "role": role,
                "content_kind": content_kind,
                "content_text": content_text,
                "content_meta": json.dumps(content_meta) if content_meta is not None else None,
                "created_at": created_at,
            },
        ).one()
        mid = row[0]
        assert isinstance(mid, UUID)
        return mid

    def list_recent(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        limit: int,
    ) -> list[MessageDTO]:
        rows = self._conn.execute(
            text(
                """
                SELECT id, user_id, vertical_id, role::text, content_kind::text,
                       content_text, content_meta, created_at
                FROM messages
                WHERE user_id = :user_id AND vertical_id = :vertical_id
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "vertical_id": vertical_id, "limit": limit},
        ).all()
        out: list[MessageDTO] = []
        for r in rows:
            meta = r[6]
            out.append(
                MessageDTO(
                    id=r[0],
                    user_id=r[1],
                    vertical_id=r[2],
                    role=r[3],
                    content_kind=r[4],
                    content_text=r[5],
                    content_meta=dict(meta) if meta is not None else None,
                    created_at=r[7],
                )
            )
        return out
