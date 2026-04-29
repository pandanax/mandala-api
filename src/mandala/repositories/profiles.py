"""Репозиторий: профиль клиента (JSONB), тикет 5."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class ClientProfileDTO:
    user_id: UUID
    vertical_id: str
    agent_card: dict[str, Any]
    scenario_state: dict[str, Any]
    display_name: str | None


class ProfileRepository:
    """CRUD профиля; слияние JSON — поверхностное ``||`` по ключам верхнего уровня."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get_by_user_id(self, user_id: UUID) -> ClientProfileDTO | None:
        row = self._conn.execute(
            text(
                """
                SELECT user_id, vertical_id, agent_card, scenario_state, display_name
                FROM client_profiles
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        ).one_or_none()
        if row is None:
            return None
        return ClientProfileDTO(
            user_id=row[0],
            vertical_id=row[1],
            agent_card=dict(row[2]) if row[2] is not None else {},
            scenario_state=dict(row[3]) if row[3] is not None else {},
            display_name=row[4],
        )

    def ensure_row(self, *, user_id: UUID, vertical_id: str) -> None:
        """Создать пустую строку профиля, если её ещё нет (идемпотентно)."""
        self._conn.execute(
            text(
                """
                INSERT INTO client_profiles (
                    user_id, vertical_id, agent_card, scenario_state
                )
                VALUES (
                    :user_id, :vertical_id, '{}'::jsonb, '{}'::jsonb
                )
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            {"user_id": user_id, "vertical_id": vertical_id},
        )

    def merge_scenario_state(self, user_id: UUID, patch: Mapping[str, Any]) -> None:
        """``scenario_state = COALESCE(scenario_state, '{}') || patch`` (shallow merge)."""
        payload = json.dumps(dict(patch), separators=(",", ":"), ensure_ascii=False)
        res = self._conn.execute(
            text(
                """
                UPDATE client_profiles
                SET scenario_state =
                    COALESCE(scenario_state, '{}'::jsonb) || CAST(:patch AS jsonb),
                    updated_at = now()
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id, "patch": payload},
        )
        if res.rowcount == 0:
            msg = "client_profiles: нет строки для user_id (вызовите ensure_row раньше)"
            raise RuntimeError(msg)

    def merge_agent_card(self, user_id: UUID, patch: Mapping[str, Any]) -> None:
        """Аналогично ``merge_scenario_state`` для ``agent_card``."""
        payload = json.dumps(dict(patch), separators=(",", ":"), ensure_ascii=False)
        res = self._conn.execute(
            text(
                """
                UPDATE client_profiles
                SET agent_card =
                    COALESCE(agent_card, '{}'::jsonb) || CAST(:patch AS jsonb),
                    updated_at = now()
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id, "patch": payload},
        )
        if res.rowcount == 0:
            msg = "client_profiles: нет строки для user_id (вызовите ensure_row раньше)"
            raise RuntimeError(msg)
