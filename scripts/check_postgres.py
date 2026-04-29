"""Проверка подключения к PostgreSQL по DATABASE_URL (тикет 2).

Запуск из корня репозитория (с активированным venv и psycopg)::

    export $(grep -v '^#' .env | xargs)   # или вручную DATABASE_URL=...
    python scripts/check_postgres.py

Или: ``make db-check`` (подхватывает .env через compose-совместимые переменные).
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 1
    try:
        import psycopg
    except ImportError:
        print(
            "Нужен psycopg: pip install 'psycopg[binary]>=3.2' или uv sync --extra dev",
            file=sys.stderr,
        )
        return 1
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                one = cur.fetchone()
    except Exception as exc:
        print(f"Не удалось подключиться: {exc}", file=sys.stderr)
        return 1
    if one != (1,):
        print(f"Неожиданный ответ: {one!r}", file=sys.stderr)
        return 1
    print("ok: SELECT 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
