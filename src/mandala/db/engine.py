"""Фабрика ``Engine`` из ``DATABASE_URL`` (синхронный драйвер psycopg v3)."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def create_engine_from_env(*, url: str | None = None) -> Engine:
    """Собрать движок. Если ``url`` не передан — читается ``DATABASE_URL`` из окружения.

    Путь ответа исполняется в worker-потоках (``anyio.to_thread``, см.
    ``http/web_chat.py`` и ``adapters/telegram/webhook_delivery.py``), поэтому
    одновременно может быть открыто несколько sync-транзакций ``engine.begin()``.
    Пул рассчитан на такую конкуренцию: размер настраивается env-переменными
    ``DB_POOL_SIZE`` / ``DB_MAX_OVERFLOW`` / ``DB_POOL_TIMEOUT`` и по умолчанию
    покрывает YC ``concurrency 16`` с запасом (иначе 16-й ход ждал бы коннект из
    пула и ходы бы сериализовались уже на уровне БД).
    """
    raw = url if url is not None else os.environ.get("DATABASE_URL")
    if not raw:
        msg = "Задайте DATABASE_URL или передайте url= в create_engine_from_env"
        raise RuntimeError(msg)
    return create_engine(
        _normalize_database_url(raw),
        pool_pre_ping=True,
        pool_size=_int_env("DB_POOL_SIZE", 20),
        max_overflow=_int_env("DB_MAX_OVERFLOW", 10),
        pool_timeout=_int_env("DB_POOL_TIMEOUT", 30),
    )
