#!/usr/bin/env bash
# Линтер, форматирование, mypy и pytest (аналог цели ``make check``).
# Запуск из корня или откуда угодно: bash scripts/check.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PY="${ROOT}/.venv/bin/python"
else
  PY=python3
fi

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  echo "[check] Обновляю editable-установку и dev-зависимости…"
  "${ROOT}/.venv/bin/pip" install -e ".[dev]" -q
fi

echo "[check] ruff check"
"$PY" -m ruff check src tests scripts
echo "[check] ruff format --check"
"$PY" -m ruff format --check src tests scripts
echo "[check] mypy"
"$PY" -m mypy src/mandala tests scripts
echo "[check] pytest"
"$PY" -m pytest

echo "[check] Готово."
