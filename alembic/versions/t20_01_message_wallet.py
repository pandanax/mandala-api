"""Кошелёк сообщений: колонка ``users.message_balance`` + миграция балансов.

Revision ID: t20_01_message_wallet
Revises: t19_01_telegram_stars
Create Date: 2026-07-28

Переход с месячных лимитов/подписки на **пакетную (кошельковую)** монетизацию. Баланс
сообщений живёт на ``users`` (per user+vertical), не привязан к периоду, не сбрасывается по
времени и не трогается ``/reset``.

Миграция существующих пользователей (решение зафиксировано здесь):

- Каждому существующему пользователю выдаём **стартовый баланс** ``_START_BALANCE`` (20) —
  как разовый грант новичку в новой модели.
- Уже плативших (текущий план ``premium`` из старой подписочной модели) **не понижаем**:
  выдаём баланс крупного пакета ``_PREMIUM_MIGRATION_BALANCE`` (1000), чтобы никто, кто платил,
  не потерял доступ при переходе. Отдельной денежной компенсации не делаем: продукт ранний,
  материальных плательщиков нет; безлимитное **промо** («вечный пакет») хранится в
  ``client_profiles.agent_card`` и этой миграцией НЕ затрагивается — оно продолжает
  обходить кошелёк (см. ``mandala.services.promo`` / ``mandala.services.quota``).

Старая месячная машинерия (``plan_limits`` month-строки, ``usage_counters``, поля периода
подписки на ``users``, привязка ``premium`` к товару Stars) НЕ удаляется — только выводится из
использования кодом; таблицы/типы остаются, чтобы не рвать миграционную цепочку и downgrade.
Новые пользователи получают стартовый грант в коде (``mandala.services.user_identity``),
поэтому ``server_default`` колонки после бэкофилла — ``0`` (прямой INSERT без гранта не
раздаёт баланс молча).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t20_01_message_wallet"
down_revision: str | Sequence[str] | None = "t19_01_telegram_stars"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Значения на момент миграции (совпадают с дефолтами кода; см. message_packs).
_START_BALANCE = 20
_PREMIUM_MIGRATION_BALANCE = 1000


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "message_balance",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        sa.text(
            "COMMENT ON COLUMN users.message_balance IS "
            "'Баланс кошелька сообщений (пакетная модель). Списывается по 1 за LLM-ответ; "
            "пополняется покупкой пакетов; не сгорает; не трогается /reset.'"
        )
    )
    # Бэкофилл: стартовый грант всем существующим пользователям…
    op.execute(sa.text("UPDATE users SET message_balance = :b").bindparams(b=_START_BALANCE))
    # …уже плативших (premium) не понижаем — баланс крупного пакета.
    op.execute(
        sa.text(
            """
            UPDATE users AS u
            SET message_balance = :b
            FROM plans AS p
            WHERE u.current_plan_id = p.id
              AND p.name = 'premium'
            """
        ).bindparams(b=_PREMIUM_MIGRATION_BALANCE)
    )
    # Новые пользователи получают стартовый грант через код → вернуть server_default к 0.
    op.alter_column("users", "message_balance", server_default=sa.text("0"))


def downgrade() -> None:
    op.drop_column("users", "message_balance")
