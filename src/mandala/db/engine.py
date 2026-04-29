"""Фабрика ``Engine`` из ``DATABASE_URL`` (синхронный драйвер psycopg v3)."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def create_engine_from_env(*, url: str | None = None) -> Engine:
    """Собрать движок. Если ``url`` не передан — читается ``DATABASE_URL`` из окружения."""
    raw = url if url is not None else os.environ.get("DATABASE_URL")
    if not raw:
        msg = "Задайте DATABASE_URL или передайте url= в create_engine_from_env"
        raise RuntimeError(msg)
    return create_engine(_normalize_database_url(raw), pool_pre_ping=True)
