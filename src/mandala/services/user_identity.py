"""Резолвинг и создание пользователя по каналу и вертикали (тикет 8).

План по умолчанию — глобальная строка ``plans`` с ``name = 'free'`` (seed тикета 3);
``users.vertical_id`` задаёт продукт, лимиты квот считаются в паре с вертикалью
(см. ``mandala.services.quota``).

Создание под конкурентные запросы сериализуется ``pg_advisory_xact_lock`` по ключу из
``(vertical_id, channel, external_user_id)``, чтобы не появлялись две строки
``channel_links`` и лишние строки ``users`` без связи.
"""

from __future__ import annotations

import hashlib
import struct
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from mandala.repositories.plans import PlansRepository
from mandala.repositories.user_channel import UserChannelRepository

_PLAN_NAME_FREE = "free"


def _advisory_key_pair(vertical_id: str, channel: str, external_user_id: str) -> tuple[int, int]:
    digest = hashlib.sha256(
        f"{vertical_id}\0{channel}\0{external_user_id}".encode(),
    ).digest()
    k1, k2 = struct.unpack(">ii", digest[:8])
    return k1, k2


class UserIdentityService:
    """Стабильный ``user_id`` для пары канал + внешний id в рамках вертикали."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get_or_create_user(
        self,
        *,
        vertical_id: str,
        channel: str,
        external_user_id: str,
        locale: str | None = None,
    ) -> UUID:
        """Вернуть существующий или создать пользователя с планом ``free``.

        Ожидается вызов внутри транзакции (``with engine.begin() as conn`` и т.п.).
        """
        uc = UserChannelRepository(self._conn)
        existing = uc.find_user_id(
            vertical_id=vertical_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        if existing is not None:
            return existing

        k1, k2 = _advisory_key_pair(vertical_id, channel, external_user_id)
        self._conn.execute(
            text("SELECT pg_advisory_xact_lock(CAST(:k1 AS int), CAST(:k2 AS int))"),
            {"k1": k1, "k2": k2},
        )

        existing2 = uc.find_user_id(
            vertical_id=vertical_id,
            channel=channel,
            external_user_id=external_user_id,
        )
        if existing2 is not None:
            return existing2

        free_id = PlansRepository(self._conn).fetch_id_by_name(_PLAN_NAME_FREE)
        if free_id is None:
            msg = "plans: нет строки с name='free' (ожидается seed миграции)"
            raise RuntimeError(msg)

        new_id = uuid4()
        self._conn.execute(
            text(
                """
                INSERT INTO users (id, vertical_id, current_plan_id, locale)
                VALUES (:id, :vertical_id, :plan_id, :locale)
                """
            ),
            {
                "id": new_id,
                "vertical_id": vertical_id,
                "plan_id": free_id,
                "locale": locale,
            },
        )
        self._conn.execute(
            text(
                """
                INSERT INTO channel_links (user_id, vertical_id, channel, external_user_id)
                VALUES (
                    :user_id,
                    :vertical_id,
                    CAST(:channel AS channel_type),
                    :external_user_id
                )
                """
            ),
            {
                "user_id": new_id,
                "vertical_id": vertical_id,
                "channel": channel,
                "external_user_id": external_user_id,
            },
        )
        return new_id
