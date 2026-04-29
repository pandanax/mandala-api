"""Проверки схемы после Alembic: тикеты 3–4 (ядро + JSONB/usage/payments).

Вызывается из ``scripts/verify_migrations.sh``. Нужны ``DATABASE_URL`` и psycopg.
"""

from __future__ import annotations

import os
import sys

EXPECTED_ALEMBIC_HEAD = "t4_01_dialog_oltp"

T4_TABLES = (
    "client_profiles",
    "messages",
    "generated_artifacts",
    "usage_counters",
    "payment_transactions",
)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 1
    try:
        import psycopg
    except ImportError:
        print("Нужен psycopg (dev-зависимости проекта).", file=sys.stderr)
        return 1

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'channel_links' "
                    "AND column_name IN ('metadata', 'link_metadata')"
                )
                cols = {row[0] for row in cur.fetchall()}
                if "metadata" not in cols:
                    print(
                        "[verify] В channel_links нет колонки metadata "
                        f"(есть: {sorted(cols) or '—'})",
                        file=sys.stderr,
                    )
                    return 1
                if "link_metadata" in cols:
                    print(
                        "[verify] В channel_links одновременно link_metadata и metadata",
                        file=sys.stderr,
                    )
                    return 1

                cur.execute("SELECT name FROM plans ORDER BY name")
                plan_names = [r[0] for r in cur.fetchall()]
                if plan_names != ["free", "premium"]:
                    print(
                        f"[verify] Ожидались планы free, premium; получено: {plan_names}",
                        file=sys.stderr,
                    )
                    return 1

                cur.execute(
                    """
                    SELECT p.name, l.resource, l.limit_per_period, l.period::text
                    FROM plan_limits l
                    JOIN plans p ON p.id = l.plan_id
                    ORDER BY p.name, l.resource
                    """
                )
                limits = cur.fetchall()
                expected = {
                    ("free", "image_generation", 0, "month"),
                    ("free", "text_reply", 20, "month"),
                    ("premium", "image_generation", 10, "month"),
                    ("premium", "text_reply", 200, "month"),
                }
                got = set(limits)
                if got != expected:
                    print("[verify] Неверные plan_limits:", file=sys.stderr)
                    print(f"  ожидалось: {sorted(expected)}", file=sys.stderr)
                    print(f"  в БД:     {sorted(got)}", file=sys.stderr)
                    return 1

                cur.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
                revs = [r[0] for r in cur.fetchall()]
                if len(revs) != 1:
                    print(
                        f"[verify] Ожидалась одна строка в alembic_version, получено: {revs}",
                        file=sys.stderr,
                    )
                    return 1
                if revs[0] != EXPECTED_ALEMBIC_HEAD:
                    print(
                        f"[verify] Ожидалась ревизия {EXPECTED_ALEMBIC_HEAD!r}, в БД: {revs[0]!r}",
                        file=sys.stderr,
                    )
                    return 1

                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename = ANY(%s)",
                    (list(T4_TABLES),),
                )
                found = {r[0] for r in cur.fetchall()}
                missing = set(T4_TABLES) - found
                if missing:
                    print(f"[verify] Нет таблиц тикета 4: {sorted(missing)}", file=sys.stderr)
                    return 1

                cur.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname = 'ix_messages_user_vertical_created_at'"
                )
                if cur.fetchone() is None:
                    print(
                        "[verify] Нет индекса ix_messages_user_vertical_created_at",
                        file=sys.stderr,
                    )
                    return 1

                cur.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname = 'uq_payment_provider_external_id'"
                )
                if cur.fetchone() is None:
                    print(
                        "[verify] Нет уникального индекса uq_payment_provider_external_id",
                        file=sys.stderr,
                    )
                    return 1
    except Exception as exc:
        print(f"[verify] Ошибка запросов: {exc}", file=sys.stderr)
        return 1

    print(
        "[verify] ok: alembic head, plans, plan_limits, channel_links.metadata, "
        "тикет 4 (profiles, messages, artifacts, usage, payments)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
