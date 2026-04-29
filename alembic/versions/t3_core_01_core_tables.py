"""Вертикали, планы, лимиты, пользователи, связи с каналами.

Revision ID: t3_core_01
Revises:
Create Date: 2026-04-29

Привязка к плану: поле ``users.current_plan_id`` → ``plans``.
Отдельной таблицы подписок в этом тикете нет; история/периоды биллинга —
TODO тикет 4+.

Уникальность в канале: ``(vertical_id, channel, external_user_id)`` на
``channel_links``. Один Telegram ``external_user_id`` в разных вертикалях —
разные строки (разные боты/продукты).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t3_core_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Типы создаются автоматически при первом create_table, где они используются
    # (иначе дублируется CREATE TYPE).
    channel_type = sa.Enum("telegram", "web", "cli", name="channel_type")
    limit_period_type = sa.Enum(
        "week",
        "month",
        "year",
        "lifetime",
        name="plan_limit_period",
    )

    op.create_table(
        "agent_verticals",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Лёгкие метаданные продукта (пути KB, model ids); не тело карточки клиента.",
        ),
    )

    op.create_table(
        "plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("billing_provider", sa.Text(), nullable=True),
        sa.Column("external_product_id", sa.Text(), nullable=True),
        sa.UniqueConstraint("name", name="uq_plans_name"),
    )

    op.create_table(
        "plan_limits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("limit_per_period", sa.Integer(), nullable=False),
        sa.Column("period", limit_period_type, nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "plan_id",
            "resource",
            "period",
            name="uq_plan_limits_plan_resource_period",
        ),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column("current_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("subscription_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locale", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_plan_id"], ["plans.id"], ondelete="RESTRICT"),
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN users.current_plan_id IS "
            "'Активный план (денормализация на пользователе). "
            "История подписок/смена плана через отдельные сущности — TODO тикет 4+.'"
        )
    )

    op.create_table(
        "channel_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column("channel", channel_type, nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "vertical_id",
            "channel",
            "external_user_id",
            name="uq_channel_links_vertical_channel_external",
        ),
    )
    op.create_index("ix_channel_links_user_id", "channel_links", ["user_id"], unique=False)
    op.execute(
        sa.text(
            "COMMENT ON TABLE channel_links IS "
            "'vertical_id = users.vertical_id для user_id "
            "(инвариант приложения, TODO тикет 5+).'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_channel_links_user_id", table_name="channel_links")
    op.drop_table("channel_links")
    op.drop_table("users")
    op.drop_table("plan_limits")
    op.drop_table("plans")
    op.drop_table("agent_verticals")

    limit_period_type = sa.Enum(
        "week",
        "month",
        "year",
        "lifetime",
        name="plan_limit_period",
    )
    channel_type = sa.Enum("telegram", "web", "cli", name="channel_type")
    limit_period_type.drop(op.get_bind(), checkfirst=True)
    channel_type.drop(op.get_bind(), checkfirst=True)
