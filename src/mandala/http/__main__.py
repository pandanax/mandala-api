"""Точка входа для HTTP приложения: ``python -m mandala.http`` (тикет 10)."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from mandala.http.app import create_app


def main() -> None:
    """Запуск FastAPI приложения через uvicorn."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # Получаем порт из переменной окружения или используем 8000 по умолчанию
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    app = create_app()

    # Запускаем uvicorn
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
