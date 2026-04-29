"""Репозиторий: счётчики usage (тикет 5; атомарность под тикет 7)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class UsageRepository:
    """Идемпотентное создание строки счётчика и атомарный инкремент при ``count < limit``."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def ensure_counter_row(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        billing_period: str,
        resource: str,
    ) -> None:
        """Вставить нулевой счётчик, если строки ещё нет (по уникальному ключу)."""
        self._conn.execute(
            text(
                """
                INSERT INTO usage_counters (
                    user_id, vertical_id, billing_period, resource, count
                )
                VALUES (
                    :user_id, :vertical_id, :billing_period, :resource, 0
                )
                ON CONFLICT ON CONSTRAINT uq_usage_user_vertical_period_resource
                DO NOTHING
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "billing_period": billing_period,
                "resource": resource,
            },
        )

    def fetch_count_for_update(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        billing_period: str,
        resource: str,
    ) -> int:
        """Обеспечить строку и вернуть ``count`` с блокировкой ``FOR UPDATE`` (чтение под квоту)."""
        self.ensure_counter_row(
            user_id=user_id,
            vertical_id=vertical_id,
            billing_period=billing_period,
            resource=resource,
        )
        row = self._conn.execute(
            text(
                """
                SELECT count
                FROM usage_counters
                WHERE user_id = :user_id
                  AND vertical_id = :vertical_id
                  AND billing_period = :billing_period
                  AND resource = :resource
                FOR UPDATE
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "billing_period": billing_period,
                "resource": resource,
            },
        ).one()
        return int(row[0])

    def try_increment(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        billing_period: str,
        resource: str,
        limit: int,
    ) -> bool:
        """
        Атомарно увеличить ``count`` на 1, если ``count < limit``.

        Сначала гарантируется наличие строки (0), затем один ``UPDATE … RETURNING``.
        При ``limit == 0`` увеличения не будет.
        """
        self.ensure_counter_row(
            user_id=user_id,
            vertical_id=vertical_id,
            billing_period=billing_period,
            resource=resource,
        )
        row = self._conn.execute(
            text(
                """
                UPDATE usage_counters
                SET count = count + 1
                WHERE user_id = :user_id
                  AND vertical_id = :vertical_id
                  AND billing_period = :billing_period
                  AND resource = :resource
                  AND count < :limit
                RETURNING count
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "billing_period": billing_period,
                "resource": resource,
                "limit": limit,
            },
        ).first()
        return row is not None

    def reset_counts_for_user_vertical_period(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        billing_period: str,
    ) -> None:
        """Обнулить все счётчики за период (смена плана / апгрейд, тикет 19)."""
        self._conn.execute(
            text(
                """
                UPDATE usage_counters
                SET count = 0
                WHERE user_id = :user_id
                  AND vertical_id = :vertical_id
                  AND billing_period = :billing_period
                """
            ),
            {
                "user_id": user_id,
                "vertical_id": vertical_id,
                "billing_period": billing_period,
            },
        )
