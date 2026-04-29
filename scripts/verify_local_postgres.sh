#!/usr/bin/env bash
# Проверка тикета 2: поднять Postgres (compose) и выполнить SELECT 1 по DATABASE_URL.
# Запуск из любой директории: bash scripts/verify_local_postgres.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ROOT}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "[verify] Копирую .env.example -> .env"
  cp "${ROOT}/.env.example" "$ENV_FILE"
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[verify] В .env нет DATABASE_URL" >&2
  exit 1
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "[verify] Нужен podman в PATH (podman compose). Docker не используется." >&2
  exit 1
fi
COMPOSE=(podman compose)

echo "[verify] Поднимаю Postgres: ${COMPOSE[*]} -f compose.yaml up -d"
"${COMPOSE[@]}" -f "${ROOT}/compose.yaml" up -d

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PY="${ROOT}/.venv/bin/python"
else
  PY=python3
fi

if ! "$PY" -c "import psycopg" 2>/dev/null; then
  echo "[verify] Нет psycopg. Выполните: ${PY} -m pip install -e '.[dev]' или uv sync --extra dev" >&2
  exit 1
fi

echo "[verify] Жду готовность БД (до ~45 с)..."
for _ in $(seq 1 45); do
  if DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/check_postgres.py" >/dev/null 2>&1; then
    DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/check_postgres.py"
    echo "[verify] Готово."
    exit 0
  fi
  sleep 1
done

echo "[verify] Таймаут. Логи postgres:" >&2
"${COMPOSE[@]}" -f "${ROOT}/compose.yaml" logs postgres --tail 30 >&2 || true
exit 1
