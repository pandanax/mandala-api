"""Репозиторий: пользователь и план (минимально под сервис квот, тикет 7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class UsersRepository:
    """Чтение полей ``users`` без расширения зоны ответственности ``UserChannelRepository``."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def fetch_current_plan_id(self, *, user_id: UUID, vertical_id: str) -> UUID | None:
        """Вернуть ``current_plan_id``, если пользователь существует и ``vertical_id`` совпадает."""
        row = self._conn.execute(
            text(
                """
                SELECT current_plan_id
                FROM users
                WHERE id = :user_id
                  AND vertical_id = :vertical_id
                """
            ),
            {"user_id": user_id, "vertical_id": vertical_id},
        ).one_or_none()
        if row is None:
            return None
        pid = row[0]
        assert isinstance(pid, UUID)
        return pid
