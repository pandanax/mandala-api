# Ожидается активированный venv с dev-зависимостями (`pip install -e ".[dev]"` или `uv sync --extra dev`).
PY ?= python3
# Только Podman Compose (тикет 2)
COMPOSE ?= podman compose

.PHONY: check verify db-up db-down db-check db-migrate
check:
	$(PY) -m ruff check src tests scripts
	$(PY) -m ruff format --check src tests scripts
	$(PY) -m mypy src/mandala tests scripts
	$(PY) -m pytest

# Сквозная проверка: check + при DATABASE_URL — Postgres, alembic, pytest -m integration
verify:
	bash scripts/verify_project.sh

db-up:
	$(COMPOSE) up -d

db-down:
	$(COMPOSE) down

# Проверка SELECT 1 (нужен запущенный Postgres и DATABASE_URL в окружении)
db-check:
	$(PY) scripts/check_postgres.py

# Накатить Alembic до head (тикет 3; нужен DATABASE_URL)
db-migrate:
	$(PY) -m alembic upgrade head
