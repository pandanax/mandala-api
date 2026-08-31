"""Integration coverage for nutrition_meals (requires DATABASE_URL + Alembic head)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.repositories import FoodDiaryRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL не задан — интеграционные тесты пропущены",
    ),
]


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def _nutrition_user(conn: Connection) -> UUID:
    plan_id = conn.execute(text("SELECT id FROM plans WHERE name = 'free'")).scalar_one()
    uid = uuid4()
    conn.execute(
        text(
            """
            INSERT INTO users (id, vertical_id, current_plan_id)
            VALUES (:id, 'nutrition', :plan_id)
            """
        ),
        {"id": uid, "plan_id": plan_id},
    )
    return uid


def test_insert_get_update_delete_and_reset_food_entries(engine: Engine) -> None:
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            uid = _nutrition_user(conn)
            repo = FoodDiaryRepository(conn)
            eaten_at = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
            entry_id = repo.insert(
                user_id=uid,
                eaten_at=eaten_at,
                raw_text="каша 200 г",
                items=[{"name": "каша", "amount": "200 г"}],
                calories_kcal=220,
                protein_g=7,
                fat_g=4,
                carbs_g=40,
                confidence="medium",
                assumptions=["вес готового блюда"],
            )
            assert isinstance(entry_id, UUID)

            rows = repo.list_between(
                user_id=uid,
                start=eaten_at - timedelta(minutes=1),
                end=eaten_at + timedelta(minutes=1),
            )
            assert len(rows) == 1
            assert rows[0].raw_text == "каша 200 г"
            assert rows[0].calories_kcal == 220
            assert rows[0].items[0]["name"] == "каша"

            loaded = repo.get_by_id(user_id=uid, entry_id=entry_id)
            assert loaded is not None
            assert loaded.raw_text == "каша 200 г"
            other_uid = _nutrition_user(conn)
            assert repo.get_by_id(user_id=other_uid, entry_id=entry_id) is None
            assert not repo.delete(user_id=other_uid, entry_id=entry_id)

            assert repo.update(
                user_id=uid,
                entry_id=entry_id,
                raw_text="каша 300 г",
                items=[{"name": "каша", "amount": "300 г"}],
                calories_kcal=330,
                protein_g=10.5,
                fat_g=6,
                carbs_g=60,
                confidence="high",
                assumptions=["вес готового блюда"],
            )
            updated = repo.get_by_id(user_id=uid, entry_id=entry_id)
            assert updated is not None
            assert updated.raw_text == "каша 300 г"
            assert updated.calories_kcal == 330
            assert updated.eaten_at == eaten_at

            assert repo.delete(user_id=uid, entry_id=entry_id)
            assert repo.get_by_id(user_id=uid, entry_id=entry_id) is None

            repo.insert(
                user_id=uid,
                eaten_at=eaten_at,
                raw_text="яблоко",
                items=[{"name": "яблоко", "amount": "1 шт."}],
                calories_kcal=80,
                protein_g=0.4,
                fat_g=0.2,
                carbs_g=20,
                confidence="medium",
                assumptions=[],
            )

            assert repo.delete_for_user(user_id=uid) == 1
            assert not repo.list_between(
                user_id=uid,
                start=eaten_at - timedelta(days=1),
                end=eaten_at + timedelta(days=1),
            )
        finally:
            transaction.rollback()
