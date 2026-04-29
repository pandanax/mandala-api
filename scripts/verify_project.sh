#!/usr/bin/env bash
# Сквозная проверка репозитория Mandala:
#   1) ruff, mypy, pytest (как ``scripts/check.sh``);
#   2) если задан ``DATABASE_URL`` (в окружении или в ``.env`` в корне) —
#      доступность PostgreSQL, ``alembic upgrade head``, ``pytest -m integration``.
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

echo "[verify-project] Шаг 1/2: базовые проверки (см. scripts/check.sh)…"
bash "${ROOT}/scripts/check.sh"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[verify-project] DATABASE_URL не задан — шаг 2 пропущен (интеграция + Alembic)."
  echo "[verify-project] Подсказка: скопируйте .env.example в .env, поднимите Postgres, экспортируйте DATABASE_URL."
  echo "[verify-project] Готово."
  exit 0
fi

echo "[verify-project] Шаг 2/2: Postgres, миграции, интеграционные тесты…"

if ! "$PY" -c "import alembic" 2>/dev/null; then
  echo "[verify-project] Нет alembic. Выполните: ${PY} -m pip install -e '.[dev]'" >&2
  exit 1
fi

DATABASE_URL="$DATABASE_URL" "$PY" "${ROOT}/scripts/check_postgres.py"

(cd "$ROOT" && DATABASE_URL="$DATABASE_URL" "$PY" -m alembic upgrade head)

(cd "$ROOT" && DATABASE_URL="$DATABASE_URL" "$PY" -m pytest -m integration --tb=short -q)

echo "[verify-project] Готово (включая интеграционные тесты)."
