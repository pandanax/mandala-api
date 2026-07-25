#!/usr/bin/env bash
# Просмотрщик документации Mandala (mkdocs) — одна команда.
#
#   bash scripts/docs-serve.sh              # локальный сервер http://127.0.0.1:8001
#   bash scripts/docs-serve.sh build        # собрать статический сайт в ./site
#   bash scripts/docs-serve.sh <любые mkdocs-аргументы>
#
# Полностью изолирован от рантайма бота: зависимости (docs/requirements.txt) НЕ
# входят в pyproject/uv.lock и в прод-образ. Виртуальное окружение вьюера живёт
# отдельно и НЕ трогает .venv приложения (которое использует scripts/check.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REQ="docs/requirements.txt"
ADDR="127.0.0.1:8001"

# По умолчанию — `serve` на отдельном порту (8001), чтобы не пересекаться с
# приложением (8000/8080). Явные аргументы (build, gh-deploy, ...) уважаются.
if [[ $# -eq 0 ]]; then
  set -- serve --dev-addr "$ADDR"
fi

# Предпочитаем uv (эфемерное окружение, ничего не устанавливает глобально).
if command -v uv >/dev/null 2>&1; then
  exec uv run --isolated --with-requirements "$REQ" mkdocs "$@"
fi

# Fallback: отдельный venv вьюера рядом с проектом (не .venv приложения).
VENV="$ROOT/.venv-docs"
if [[ ! -x "$VENV/bin/mkdocs" ]]; then
  echo "[docs] Создаю изолированное окружение вьюера в .venv-docs…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$REQ"
fi
exec "$VENV/bin/mkdocs" "$@"
