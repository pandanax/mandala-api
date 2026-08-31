"""Persist nutrition food-diary entries and estimated macros.

Revision ID: t22_01_nutrition_meals
Revises: t21_01_seed_nutrition
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t22_01_nutrition_meals"
down_revision: str | Sequence[str] | None = "t21_01_seed_nutrition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nutrition_meals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eaten_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("calories_kcal", sa.Numeric(9, 2), nullable=False),
        sa.Column("protein_g", sa.Numeric(9, 2), nullable=False),
        sa.Column("fat_g", sa.Numeric(9, 2), nullable=False),
        sa.Column("carbs_g", sa.Numeric(9, 2), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column(
            "assumptions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "estimate_version",
            sa.String(length=32),
            nullable=False,
            server_default="llm-v1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "calories_kcal >= 0 AND calories_kcal <= 10000",
            name="ck_nutrition_meals_calories",
        ),
        sa.CheckConstraint(
            "protein_g >= 0 AND protein_g <= 1000",
            name="ck_nutrition_meals_protein",
        ),
        sa.CheckConstraint("fat_g >= 0 AND fat_g <= 1000", name="ck_nutrition_meals_fat"),
        sa.CheckConstraint(
            "carbs_g >= 0 AND carbs_g <= 1000",
            name="ck_nutrition_meals_carbs",
        ),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high')",
            name="ck_nutrition_meals_confidence",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_nutrition_meals_user_eaten_at",
        "nutrition_meals",
        ["user_id", "eaten_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "COMMENT ON TABLE nutrition_meals IS "
            "'Food diary for nutrition users. Calories and macros are estimates, not facts.'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_nutrition_meals_user_eaten_at", table_name="nutrition_meals")
    op.drop_table("nutrition_meals")
