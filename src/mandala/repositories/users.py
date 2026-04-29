"""Репозиторий: пользователь и план (минимально под сервис квот, тикет 7)."""

from __future__ import annotations

from datetime import UTC, datetime
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

    def update_current_plan(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        plan_id: UUID,
        subscription_period_start: datetime | None = None,
    ) -> bool:
        """Обновить ``current_plan_id`` и ``updated_at``; опционально старт периода подписки.

        Возвращает ``True``, если обновлена ровно одна строка (пользователь и вертикаль совпали).
        """
        now = datetime.now(tz=UTC)
        start = subscription_period_start if subscription_period_start is not None else now
        result = self._conn.execute(
            text(
                """
                UPDATE users
                SET current_plan_id = :plan_id,
                    subscription_period_start = :period_start,
                    updated_at = :now
                WHERE id = :user_id
                  AND vertical_id = :vertical_id
                """
            ),
            {
                "plan_id": plan_id,
                "period_start": start,
                "now": now,
                "user_id": user_id,
                "vertical_id": vertical_id,
            },
        )
        return result.rowcount == 1

    def set_subscription_period_end(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        subscription_period_end: datetime,
    ) -> bool:
        """Установить ``subscription_period_end`` при активной строке ``users``."""
        result = self._conn.execute(
            text(
                """
                UPDATE users
                SET subscription_period_end = :period_end,
                    updated_at = :now
                WHERE id = :user_id
                  AND vertical_id = :vertical_id
                """
            ),
            {
                "period_end": subscription_period_end,
                "now": datetime.now(tz=UTC),
                "user_id": user_id,
                "vertical_id": vertical_id,
            },
        )
        return result.rowcount == 1
