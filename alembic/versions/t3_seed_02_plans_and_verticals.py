"""Seed: демо-вертикали, планы free/premium и лимиты (тикет 3).

Revision ID: t3_seed_02
Revises: t3_core_01
Create Date: 2026-04-29

Лимиты заданы данными: у ``free`` — ``image_generation`` = 0 в месяц (запрет картинок);
у ``premium`` — выше пороги по тексту и картинкам.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t3_seed_02"
down_revision: str | Sequence[str] | None = "t3_core_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("""
            INSERT INTO agent_verticals (slug, display_name, is_active)
            VALUES
                ('astrology', 'Астрология (демо)', true),
                ('therapy', 'Терапия (демо)', true)
            ON CONFLICT (slug) DO NOTHING
        """)
    )
    op.execute(
        sa.text("""
            INSERT INTO plans (name, description)
            VALUES
                ('free', 'Бесплатный план'),
                ('premium', 'Платный план')
            ON CONFLICT (name) DO NOTHING
        """)
    )

    stmts = [
        """
        INSERT INTO plan_limits (plan_id, resource, limit_per_period, period)
        SELECT p.id, 'text_reply', 20, 'month'::plan_limit_period
        FROM plans p WHERE p.name = 'free'
        ON CONFLICT ON CONSTRAINT uq_plan_limits_plan_resource_period DO NOTHING
        """,
        """
        INSERT INTO plan_limits (plan_id, resource, limit_per_period, period)
        SELECT p.id, 'image_generation', 0, 'month'::plan_limit_period
        FROM plans p WHERE p.name = 'free'
        ON CONFLICT ON CONSTRAINT uq_plan_limits_plan_resource_period DO NOTHING
        """,
        """
        INSERT INTO plan_limits (plan_id, resource, limit_per_period, period)
        SELECT p.id, 'text_reply', 200, 'month'::plan_limit_period
        FROM plans p WHERE p.name = 'premium'
        ON CONFLICT ON CONSTRAINT uq_plan_limits_plan_resource_period DO NOTHING
        """,
        """
        INSERT INTO plan_limits (plan_id, resource, limit_per_period, period)
        SELECT p.id, 'image_generation', 10, 'month'::plan_limit_period
        FROM plans p WHERE p.name = 'premium'
        ON CONFLICT ON CONSTRAINT uq_plan_limits_plan_resource_period DO NOTHING
        """,
    ]
    for sql in stmts:
        op.execute(sa.text(sql))


def downgrade() -> None:
    op.execute(
        sa.text("""
            DELETE FROM plan_limits
            WHERE plan_id IN (SELECT id FROM plans WHERE name IN ('free', 'premium'))
        """)
    )
    op.execute(sa.text("DELETE FROM plans WHERE name IN ('free', 'premium')"))
    op.execute(sa.text("DELETE FROM agent_verticals WHERE slug IN ('astrology', 'therapy')"))
