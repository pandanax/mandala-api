"""Seed the nutrition vertical without changing previously applied migrations."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t21_01_seed_nutrition"
down_revision: str | Sequence[str] | None = "t20_01_message_wallet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO agent_verticals (slug, name, is_active)
            VALUES ('nutrition', 'Нутрициолог', true)
            ON CONFLICT (slug) DO UPDATE
            SET name = EXCLUDED.name, is_active = EXCLUDED.is_active
            """
        )
    )


def downgrade() -> None:
    # Deliberately non-destructive: profiles may already reference this vertical.
    pass
