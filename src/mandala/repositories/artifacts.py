"""Репозиторий: сгенерированные артефакты (тикет 5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class ArtifactRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        kind: str,
        payload: Mapping[str, Any],
        source_message_id: UUID | None = None,
    ) -> UUID:
        row = self._conn.execute(
            text(
                """
                INSERT INTO generated_artifacts (
                    user_id, vertical_id, kind, payload, source_message_id
                )
                VALUES (
                    :user_id,
                    :vertical_id,
                    :kind,
                    CAST(:payload AS jsonb),
                    :source_message_id
                )
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "kind": kind,
                "payload": json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=False),
                "source_message_id": source_message_id,
            },
        ).one()
        aid = row[0]
        assert isinstance(aid, UUID)
        return aid
