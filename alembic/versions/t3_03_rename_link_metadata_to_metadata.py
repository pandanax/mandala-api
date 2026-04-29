"""Переименование channel_links.link_metadata → metadata (data-model.md).

Revision ID: t3_03_rename_metadata
Revises: t3_seed_02
Create Date: 2026-04-29

Новые установки получают колонку ``metadata`` уже из ``t3_core_01``; здесь —
условный RENAME для БД, накатанных до правки ядра миграции.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t3_03_rename_metadata"
down_revision: str | Sequence[str] | None = "t3_seed_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'channel_links'
                      AND column_name = 'link_metadata'
                ) THEN
                    ALTER TABLE channel_links
                    RENAME COLUMN link_metadata TO metadata;
                END IF;
            END
            $$;
        """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'channel_links'
                      AND column_name = 'metadata'
                )
                AND NOT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'channel_links'
                      AND column_name = 'link_metadata'
                ) THEN
                    ALTER TABLE channel_links
                    RENAME COLUMN metadata TO link_metadata;
                END IF;
            END
            $$;
        """)
    )
