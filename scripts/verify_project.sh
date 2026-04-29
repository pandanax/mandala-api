#!/usr/bin/env bash
# Сквозная проверка репозитория Mandala:
#   - если задан ``DATABASE_URL`` (окружение или ``.env``) — сначала Postgres и
#     ``alembic upgrade head``, чтобы интеграционные тесты в pytest не падали на пустой схеме;
#   - затем ``scripts/check.sh`` (ruff, mypy, полный pytest; интеграционные — при заданном ``DATABASE_URL``).
#
# Запуск из любой директории: bash scripts/verify_project.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PY="${ROOT}/.venv/bin/python"
else
  PY=python3
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "[verify-project] DATABASE_URL задан — сначала Postgres и миграции…"
  if ! "$PY" -c "import alembic" 2>/dev/null; then
    echo "[verify-project] Нет alembic. Выполните: ${PY} -m pip install -e '.[dev]'" >&2
    exit 1
  fi
  DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/check_postgres.py"
  (cd "$ROOT" && DATABASE_URL="$DATABASE_URL" "$PY" -m alembic upgrade head)
else
  echo "[verify-project] DATABASE_URL не задан — шаг Postgres/Alembic пропущен."
  echo "[verify-project] Подсказка: скопируйте .env.example в .env, поднимите Postgres."
fi

echo "[verify-project] Базовые проверки (см. scripts/check.sh; при DATABASE_URL интеграция уже в pytest)…"
bash "${ROOT}/scripts/check.sh"

echo "[verify-project] Готово."
