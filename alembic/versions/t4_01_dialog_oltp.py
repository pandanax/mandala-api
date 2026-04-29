"""Профиль клиента (JSONB), сообщения, артефакты, usage, платежи (тикет 4).

Revision ID: t4_01_dialog_oltp (≤32 символа для alembic_version)
Revises: t3_03_rename_metadata
Create Date: 2026-04-29

Политика дублирования с ``messages`` (см. COMMENT на ``generated_artifacts``):
короткий ответ ассистента — в ``messages.content_text``; развёрнутая структура
(рекомендации, отчёт, ссылки на файлы) — опционально в ``generated_artifacts.payload``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "t4_01_dialog_oltp"
down_revision: str | Sequence[str] | None = "t3_03_rename_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    message_role = sa.Enum("user", "assistant", "system", name="message_role")
    message_content_kind = sa.Enum(
        "text",
        "image",
        "audio",
        "file",
        "mixed",
        "unknown",
        name="message_content_kind",
    )
    payment_status = sa.Enum(
        "pending",
        "completed",
        "failed",
        name="payment_status",
    )

    op.create_table(
        "client_profiles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column(
            "agent_card",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "scenario_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index("ix_client_profiles_vertical_id", "client_profiles", ["vertical_id"])
    op.execute(
        sa.text(
            "COMMENT ON TABLE client_profiles IS "
            "'vertical_id = users.vertical_id для user_id (инвариант приложения).'"
        )
    )

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column("role", message_role, nullable=False),
        sa.Column("content_kind", message_content_kind, nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column(
            "content_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
    )
    op.execute(
        sa.text(
            "CREATE INDEX ix_messages_user_vertical_created_at "
            "ON messages (user_id, vertical_id, created_at DESC)"
        )
    )

    op.create_table(
        "generated_artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_generated_artifacts_user_vertical_created",
        "generated_artifacts",
        ["user_id", "vertical_id", "created_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "COMMENT ON TABLE generated_artifacts IS "
            "'Дополняет messages: ответ LLM в messages.content_text; "
            "аудит/медиа в payload (маскировать PII в логах).'"
        )
    )

    op.create_table(
        "usage_counters",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column("billing_period", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "user_id",
            "vertical_id",
            "billing_period",
            "resource",
            name="uq_usage_user_vertical_period_resource",
        ),
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN usage_counters.billing_period IS "
            "'Период учёта, например YYYY-MM; правило периода — тикет 7.'"
        )
    )

    op.create_table(
        "payment_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vertical_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", payment_status, nullable=False),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["vertical_id"], ["agent_verticals.slug"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "provider",
            "external_id",
            name="uq_payment_provider_external_id",
        ),
    )
    op.create_index(
        "ix_payment_transactions_user_created",
        "payment_transactions",
        ["user_id", "created_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN payment_transactions.raw_payload IS "
            "'Сырой ответ провайдера; в логах маскировать PII (тикет 20+).'"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_user_created", table_name="payment_transactions")
    op.drop_table("payment_transactions")

    op.drop_table("usage_counters")

    op.drop_index(
        "ix_generated_artifacts_user_vertical_created",
        table_name="generated_artifacts",
    )
    op.drop_table("generated_artifacts")

    op.execute(sa.text("DROP INDEX IF EXISTS ix_messages_user_vertical_created_at"))
    op.drop_table("messages")

    op.drop_index("ix_client_profiles_vertical_id", table_name="client_profiles")
    op.drop_table("client_profiles")

    payment_status = sa.Enum("pending", "completed", "failed", name="payment_status")
    message_content_kind = sa.Enum(
        "text",
        "image",
        "audio",
        "file",
        "mixed",
        "unknown",
        name="message_content_kind",
    )
    message_role = sa.Enum("user", "assistant", "system", name="message_role")
    payment_status.drop(op.get_bind(), checkfirst=True)
    message_content_kind.drop(op.get_bind(), checkfirst=True)
    message_role.drop(op.get_bind(), checkfirst=True)
