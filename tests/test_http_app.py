"""Тесты для HTTP приложения (health, webhook) — тикет 10."""

from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from mandala.domain.contracts import OutboundMessage
from mandala.http.app import create_app


@pytest.fixture
def client() -> TestClient:
    """HTTP клиент для тестирования FastAPI приложения."""
    app = create_app()
    return TestClient(app)


def test_health_success(client: TestClient) -> None:
    """Тест успешного health check."""
    with patch("mandala.http.app.get_engine") as mock_get_engine:
        mock_engine = Mock()
        mock_conn = Mock()
        mock_result = Mock()
        mock_result.fetchone.return_value = (1,)

        mock_conn.execute.return_value = mock_result
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine

        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    mock_conn.execute.assert_called_once()
    executed = mock_conn.execute.call_args[0][0]
    assert executed.text == "SELECT 1 as test"


def test_health_database_failure(client: TestClient) -> None:
    """Тест health check при недоступности БД."""
    from sqlalchemy.exc import SQLAlchemyError

    with patch("mandala.http.app.get_engine") as mock_get_engine:
        mock_engine = Mock()
        mock_engine.begin.side_effect = SQLAlchemyError("Connection failed")
        mock_get_engine.return_value = mock_engine

        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "Database unavailable"


def test_webhook_success(client: TestClient) -> None:
    """Тест успешной обработки webhook."""
    env_vars = {
        "TELEGRAM_WEBHOOK_SECRET": "test-secret",
        "TELEGRAM_VERTICAL_ID": "astrology",
        "TELEGRAM_BOT_TOKEN": "123:test-token",
    }

    telegram_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {"id": 123456, "is_bot": False, "first_name": "Test", "language_code": "ru"},
            "chat": {"id": 123456, "type": "private"},
            "date": 1234567890,
            "text": "Привет",
        },
    }

    with (
        patch.dict(os.environ, env_vars),
        patch("mandala.adapters.telegram.webhook_delivery.get_engine") as mock_get_engine,
        patch("mandala.adapters.telegram.webhook_delivery.handle_inbound") as mock_handle,
        patch("mandala.adapters.telegram.webhook_delivery.TelegramBotApiClient"),
        patch(
            "mandala.adapters.telegram.webhook_delivery.deliver_outbound_messages"
        ) as mock_deliver,
    ):
        mock_engine = Mock()
        mock_conn = Mock()
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine

        mock_handle.return_value = [OutboundMessage(text="Тестовый ответ")]

        headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
        response = client.post(
            "/webhooks/telegram/astrology", json=telegram_update, headers=headers
        )

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

        mock_handle.assert_called_once()
        event = mock_handle.call_args[0][0]
        assert event.vertical_id == "astrology"
        assert event.channel == "telegram"
        assert event.external_user_id == "123456"
        assert event.text == "Привет"

        mock_deliver.assert_called_once()


def test_webhook_invalid_secret(client: TestClient) -> None:
    """Тест webhook с неверным секретом."""
    env_vars = {"TELEGRAM_WEBHOOK_SECRET": "correct-secret"}

    telegram_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {"id": 123456, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 123456, "type": "private"},
            "date": 1234567890,
            "text": "Привет",
        },
    }

    with patch.dict(os.environ, env_vars):
        headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}
        response = client.post(
            "/webhooks/telegram/astrology", json=telegram_update, headers=headers
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid secret token"


def test_webhook_missing_secret_header(client: TestClient) -> None:
    """Тест webhook без заголовка секрета, когда он настроен."""
    env_vars = {"TELEGRAM_WEBHOOK_SECRET": "required-secret"}

    telegram_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {"id": 123456, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 123456, "type": "private"},
            "date": 1234567890,
            "text": "Привет",
        },
    }

    with patch.dict(os.environ, env_vars):
        response = client.post("/webhooks/telegram/astrology", json=telegram_update)

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid secret token"


def test_webhook_no_secret_configured(client: TestClient) -> None:
    """Тест webhook когда секрет не настроен (должен работать без проверки)."""
    env_vars = {"TELEGRAM_VERTICAL_ID": "astrology", "TELEGRAM_BOT_TOKEN": "123:test-token"}

    telegram_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {"id": 123456, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 123456, "type": "private"},
            "date": 1234567890,
            "text": "Привет",
        },
    }

    with (
        patch.dict(os.environ, env_vars, clear=True),
        patch("mandala.adapters.telegram.webhook_delivery.get_engine") as mock_get_engine,
        patch("mandala.adapters.telegram.webhook_delivery.handle_inbound") as mock_handle,
        patch("mandala.adapters.telegram.webhook_delivery.TelegramBotApiClient"),
        patch("mandala.adapters.telegram.webhook_delivery.deliver_outbound_messages"),
    ):
        mock_engine = Mock()
        mock_conn = Mock()
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine

        mock_handle.return_value = [OutboundMessage(text="Ответ")]

        response = client.post("/webhooks/telegram/astrology", json=telegram_update)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_unprocessable_update(client: TestClient) -> None:
    """Тест webhook с update, который не может быть обработан."""
    env_vars = {"TELEGRAM_VERTICAL_ID": "astrology", "TELEGRAM_BOT_TOKEN": "123:test-token"}

    telegram_update = {
        "update_id": 123456789,
        "channel_post": {
            "message_id": 1,
            "chat": {"id": -123456, "type": "channel"},
            "date": 1234567890,
            "text": "Канальный пост",
        },
    }

    with patch.dict(os.environ, env_vars, clear=True):
        response = client.post("/webhooks/telegram/astrology", json=telegram_update)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_web_chat_success_body_vertical(client: TestClient) -> None:
    """Web webhook: vertical_id в теле, тот же handle_inbound."""
    with (
        patch("mandala.http.web_chat.get_engine") as mock_get_engine,
        patch("mandala.http.web_chat.handle_inbound") as mock_handle,
    ):
        mock_engine = Mock()
        mock_conn = Mock()
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine
        mock_handle.return_value = [OutboundMessage(text="Ответ web")]

        response = client.post(
            "/webhooks/web",
            json={"text": "Привет", "vertical_id": "astrology"},
            headers={"X-External-User-Id": "web-user-1"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "messages" in data
    assert len(data["messages"]) == 1
    assert data["messages"][0]["text"] == "Ответ web"
    mock_handle.assert_called_once()
    event = mock_handle.call_args[0][0]
    assert event.vertical_id == "astrology"
    assert event.channel == "web"
    assert event.external_user_id == "web-user-1"
    assert event.text == "Привет"


def test_web_chat_success_header_vertical(client: TestClient) -> None:
    """vertical_id только из X-Vertical-Id."""
    with (
        patch("mandala.http.web_chat.get_engine") as mock_get_engine,
        patch("mandala.http.web_chat.handle_inbound") as mock_handle,
    ):
        mock_engine = Mock()
        mock_conn = Mock()
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine
        mock_handle.return_value = []

        response = client.post(
            "/webhooks/web",
            json={"text": "x"},
            headers={"X-Vertical-Id": "therapy", "X-External-User-Id": "u-2"},
        )

    assert response.status_code == 200
    assert mock_handle.call_args[0][0].vertical_id == "therapy"


def test_web_chat_missing_vertical_and_external(client: TestClient) -> None:
    """422 без vertical_id и без X-External-User-Id."""
    r1 = client.post("/webhooks/web", json={"text": "x"}, headers={"X-External-User-Id": "u"})
    assert r1.status_code == 422
    r2 = client.post(
        "/webhooks/web",
        json={"text": "x", "vertical_id": "astrology"},
    )
    assert r2.status_code == 422


def test_webhook_wrong_vertical(client: TestClient) -> None:
    """Тест webhook для неподдерживаемой вертикали (токен только для astrology)."""
    env_vars = {
        "TELEGRAM_VERTICAL_ID": "astrology",
        "TELEGRAM_BOT_TOKEN": "123:test-token",
    }

    telegram_update = {
        "update_id": 123456789,
        "message": {
            "message_id": 1,
            "from": {"id": 123456, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 123456, "type": "private"},
            "date": 1234567890,
            "text": "Привет",
        },
    }

    with (
        patch.dict(os.environ, env_vars, clear=True),
        patch("mandala.adapters.telegram.webhook_delivery.get_engine") as mock_get_engine,
        patch("mandala.adapters.telegram.webhook_delivery.handle_inbound") as mock_handle,
    ):
        mock_engine = Mock()
        mock_conn = Mock()
        mock_engine.begin.return_value.__enter__ = Mock(return_value=mock_conn)
        mock_engine.begin.return_value.__exit__ = Mock(return_value=None)
        mock_get_engine.return_value = mock_engine

        mock_handle.return_value = [OutboundMessage(text="Ответ")]

        response = client.post("/webhooks/telegram/therapy", json=telegram_update)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
