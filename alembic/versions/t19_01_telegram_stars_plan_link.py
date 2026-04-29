"""Привязка плана premium к товару Telegram Stars (тикет 19).

Revision ID: t19_01_telegram_stars
Revises: t4_01_dialog_oltp
Create Date: 2026-04-30

Поле ``invoice_payload`` в выставлении счёта (sendInvoice / createInvoiceLink) должно
совпадать с ``plans.external_product_id`` для маппинга на план; ``billing_provider``
фиксирован для Stars.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t19_01_telegram_stars"
down_revision: str | Sequence[str] | None = "t4_01_dialog_oltp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Согласовано с ``mandala.services.telegram_stars`` (MVP: один платный план).
_PREMIUM_STARS_PAYLOAD = "mandala_premium_stars"
_PROVIDER = "telegram_stars"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET billing_provider = :bp,
                external_product_id = :eid
            WHERE name = 'premium'
            """
        ).bindparams(bp=_PROVIDER, eid=_PREMIUM_STARS_PAYLOAD)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE plans
            SET billing_provider = NULL,
                external_product_id = NULL
            WHERE name = 'premium'
              AND billing_provider = :bp
              AND external_product_id = :eid
            """
        ).bindparams(bp=_PROVIDER, eid=_PREMIUM_STARS_PAYLOAD)
    )
