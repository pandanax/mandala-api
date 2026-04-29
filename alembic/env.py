"""Конфигурация Alembic: URL из DATABASE_URL (тикет 2: psycopg v3)."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        msg = "DATABASE_URL не задан (нужен для alembic upgrade head)."
        raise RuntimeError(msg)
    # SQLAlchemy 2 + psycopg v3
    if raw.startswith("postgresql://") and not raw.startswith("postgresql+"):
        return raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
