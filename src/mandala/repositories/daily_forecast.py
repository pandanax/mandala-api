"""Репозиторий: получатели утренней рассылки (проактивный девиз-мотиватор).

Цель отправки — ``external_user_id`` пользователя (в личке Telegram это и есть ``chat_id``).
Берём только пользователей вертикали, у которых уже есть дата рождения (``agent_card ?
'birth_date'``) — т.е. реально заполнявших анкету; брошенных на первом вопросе не трогаем.
Фильтрация «пора слать» и настройки — на уровне сервиса (``services/daily_forecast``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class DailyForecastRecipient:
    user_id: UUID
    external_user_id: str
    agent_card: dict[str, Any]


class DailyForecastRepository:
    """Выборка получателей утренней рассылки по вертикали (join профиль + канал)."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def list_recipients(
        self, *, vertical_id: str, channel: str = "telegram"
    ) -> list[DailyForecastRecipient]:
        """Все получатели вертикали (``user_id`` + ``external_user_id`` + ``agent_card``).

        Только те, у кого в ``agent_card`` есть ``birth_date`` (заполнили анкету).
        """
        rows: Iterator[Any] = self._conn.execute(
            text(
                """
                SELECT cp.user_id, cl.external_user_id, cp.agent_card
                FROM client_profiles AS cp
                JOIN channel_links AS cl
                  ON cl.user_id = cp.user_id
                WHERE cp.vertical_id = :vertical_id
                  AND cl.channel = CAST(:channel AS channel_type)
                  AND cp.agent_card ? 'birth_date'
                """
            ),
            {"vertical_id": vertical_id, "channel": channel},
        )
        out: list[DailyForecastRecipient] = []
        for row in rows:
            ext = row[1]
            if not isinstance(ext, str) or not ext.strip():
                continue
            out.append(
                DailyForecastRecipient(
                    user_id=row[0],
                    external_user_id=ext,
                    agent_card=dict(row[2]) if row[2] is not None else {},
                )
            )
        return out
