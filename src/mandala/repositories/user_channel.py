"""Репозиторий: пользователь по ``channel_links`` и ``vertical_id`` (тикет 5)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class UserChannelRepository:
    """Поиск ``user_id`` по внешнему идентификатору канала в рамках вертикали."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def find_user_id(
        self,
        *,
        vertical_id: str,
        channel: str,
        external_user_id: str,
    ) -> UUID | None:
        """Вернуть внутренний ``user_id`` или ``None``, если связи нет."""
        row = self._conn.execute(
            text(
                """
                SELECT cl.user_id
                FROM channel_links AS cl
                WHERE cl.vertical_id = :vertical_id
                  AND cl.channel = CAST(:channel AS channel_type)
                  AND cl.external_user_id = :external_user_id
                """
            ),
            {
                "vertical_id": vertical_id,
                "channel": channel,
                "external_user_id": external_user_id,
            },
        ).one_or_none()
        if row is None:
            return None
        uid = row[0]
        assert isinstance(uid, UUID)
        return uid
