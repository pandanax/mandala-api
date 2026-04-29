"""Интеграционные тесты HTTP приложения с реальной БД (тикет 10)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mandala.http.app import create_app


@pytest.mark.integration
def test_health_with_real_database() -> None:
    """Интеграционный тест health endpoint с реальной БД."""
    # Пропускаем, если нет DATABASE_URL
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not configured")

    app = create_app()
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok", "database": "ok"}


@pytest.mark.integration
def test_webhook_with_real_database() -> None:
    """Интеграционный тест webhook с реальной БД."""
    # Пропускаем, если нет DATABASE_URL
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not configured")

    app = create_app()
    client = TestClient(app)

    # Настройки окружения для теста
    env_vars = {
        "TELEGRAM_VERTICAL_ID": "astrology",
        "TELEGRAM_BOT_TOKEN": "123:fake-token-for-test",
    }

    # Реалистичный Telegram Update
    telegram_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {
                "id": 987654321,
                "is_bot": False,
                "first_name": "IntegrationTest",
                "language_code": "ru",
            },
            "chat": {"id": 987654321, "type": "private"},
            "date": 1234567890,
            "text": "/start",
        },
    }

    with (
        patch.dict(os.environ, env_vars),
        patch("mandala.http.app.deliver_outbound_messages") as mock_deliver,
    ):
        # Не мокаем TelegramBotApiClient полностью, только deliver_outbound_messages
        # чтобы не делать реальные HTTP запросы к Telegram API

        response = client.post("/webhooks/telegram/astrology", json=telegram_update)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

    # Проверяем, что ответ был сгенерирован (mock_deliver должен быть вызван)
    mock_deliver.assert_called_once()

    # Проверяем аргументы вызова
    call_args = mock_deliver.call_args
    assert call_args[1]["chat_id"] == 987654321
    messages = call_args[1]["messages"]
    assert len(messages) > 0
    assert messages[0].text is not None
    # Проверяем, что в ответе есть информация о вертикали
    assert "astrology" in messages[0].text
