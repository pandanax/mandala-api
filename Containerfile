# Mandala HTTP (тикет 23). Сборка: podman build -f Containerfile -t mandala:local .
# В проде по целевой схеме: HOST=127.0.0.1 за Nginx; см. scripts/deploy/README.md

FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
COPY README.md ./README.md
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN uv sync --frozen --no-dev --extra deploy --no-editable

FROM python:3.11-slim-bookworm AS runtime
WORKDIR /app

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin mandala

COPY --from=builder /app/.venv /app/.venv
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOST=127.0.0.1 \
    PORT=8000

USER mandala
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"

CMD ["python", "-m", "mandala.http"]
