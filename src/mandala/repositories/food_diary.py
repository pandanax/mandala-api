"""PostgreSQL repository for the nutrition food diary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class FoodDiaryEntry:
    id: UUID
    user_id: UUID
    eaten_at: datetime
    raw_text: str
    items: list[dict[str, Any]]
    calories_kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    confidence: str
    assumptions: list[str]
    created_at: datetime


class FoodDiaryRepository:
    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert(
        self,
        *,
        user_id: UUID,
        eaten_at: datetime,
        raw_text: str,
        items: list[dict[str, Any]],
        calories_kcal: float,
        protein_g: float,
        fat_g: float,
        carbs_g: float,
        confidence: str,
        assumptions: list[str],
    ) -> UUID:
        row = self._conn.execute(
            text(
                """
                INSERT INTO nutrition_meals (
                    user_id, eaten_at, raw_text, items,
                    calories_kcal, protein_g, fat_g, carbs_g,
                    confidence, assumptions
                )
                VALUES (
                    :user_id, :eaten_at, :raw_text, CAST(:items AS jsonb),
                    :calories_kcal, :protein_g, :fat_g, :carbs_g,
                    :confidence, CAST(:assumptions AS jsonb)
                )
                RETURNING id
                """
            ),
            {
                "user_id": user_id,
                "eaten_at": eaten_at,
                "raw_text": raw_text,
                "items": json.dumps(items, ensure_ascii=False),
                "calories_kcal": calories_kcal,
                "protein_g": protein_g,
                "fat_g": fat_g,
                "carbs_g": carbs_g,
                "confidence": confidence,
                "assumptions": json.dumps(assumptions, ensure_ascii=False),
            },
        ).one()
        entry_id = row[0]
        assert isinstance(entry_id, UUID)
        return entry_id

    def list_between(
        self,
        *,
        user_id: UUID,
        start: datetime,
        end: datetime,
    ) -> list[FoodDiaryEntry]:
        rows = self._conn.execute(
            text(
                """
                SELECT id, user_id, eaten_at, raw_text, items,
                       calories_kcal, protein_g, fat_g, carbs_g,
                       confidence, assumptions, created_at
                FROM nutrition_meals
                WHERE user_id = :user_id
                  AND eaten_at >= :start
                  AND eaten_at < :end
                ORDER BY eaten_at ASC, id ASC
                """
            ),
            {"user_id": user_id, "start": start, "end": end},
        ).all()
        return [self._to_entry(row) for row in rows]

    def delete_for_user(self, *, user_id: UUID) -> int:
        result = self._conn.execute(
            text("DELETE FROM nutrition_meals WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _to_entry(row: Any) -> FoodDiaryEntry:
        raw_items = row[4] if isinstance(row[4], list) else []
        raw_assumptions = row[10] if isinstance(row[10], list) else []
        return FoodDiaryEntry(
            id=row[0],
            user_id=row[1],
            eaten_at=row[2],
            raw_text=row[3],
            items=[dict(item) for item in raw_items if isinstance(item, dict)],
            calories_kcal=float(row[5]),
            protein_g=float(row[6]),
            fat_g=float(row[7]),
            carbs_g=float(row[8]),
            confidence=str(row[9]),
            assumptions=[str(item) for item in raw_assumptions],
            created_at=row[11],
        )
