#!/usr/bin/env bash
# Проверка тикета 3: Postgres (Podman), alembic upgrade head, планы/лимиты/channel_links.metadata.
# Запуск из любой директории: bash scripts/verify_migrations.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ROOT}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[verify-migrations] Копирую .env.example -> .env"
  cp "${ROOT}/.env.example" "$ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[verify-migrations] В .env нет DATABASE_URL" >&2
  exit 1
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "[verify-migrations] Нужен podman в PATH (podman compose)." >&2
  exit 1
fi
COMPOSE=(podman compose)

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PY="${ROOT}/.venv/bin/python"
else
  PY=python3
fi

if ! "$PY" -c "import psycopg" 2>/dev/null; then
  echo "[verify-migrations] Нет psycopg. Выполните: ${PY} -m pip install -e '.[dev]'" >&2
  exit 1
fi

if ! "$PY" -c "import alembic" 2>/dev/null; then
  echo "[verify-migrations] Нет alembic. Выполните: ${PY} -m pip install -e '.[dev]'" >&2
  exit 1
fi

echo "[verify-migrations] Поднимаю Postgres: ${COMPOSE[*]} up -d"
"${COMPOSE[@]}" -f "${ROOT}/compose.yaml" up -d

echo "[verify-migrations] Жду готовность БД (до ~45 с)..."
for _ in $(seq 1 45); do
  if DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/check_postgres.py" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/check_postgres.py"; then
  echo "[verify-migrations] БД не отвечает. Логи:" >&2
  "${COMPOSE[@]}" -f "${ROOT}/compose.yaml" logs postgres --tail 30 >&2 || true
  exit 1
fi

echo "[verify-migrations] alembic upgrade head"
(cd "$ROOT" && DATABASE_URL="$DATABASE_URL" "$PY" -m alembic upgrade head)

echo "[verify-migrations] Проверка схемы и seed-данных"
DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/verify_migration_state.py"

echo "[verify-migrations] Готово."
