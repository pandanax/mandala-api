"""Интеграционные тесты HTTP приложения с реальной БД (тикет 10)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mandala.http.app import create_app

_CONFIRM = "mdl:intake:ok"
_SAVE = "mdl:intake:save"


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

    chat_id = int(uuid4().int % (9 * 10**8)) + 10**8

    # Настройки окружения для теста
    env_vars = {
        "TELEGRAM_VERTICAL_ID": "astrology",
        "TELEGRAM_BOT_TOKEN": "123:fake-token-for-test",
    }

    def _msg(text: str, mid: int) -> dict[str, Any]:
        return {
            "update_id": 123456780 + mid,
            "message": {
                "message_id": mid,
                "from": {
                    "id": chat_id,
                    "is_bot": False,
                    "first_name": "IntegrationTest",
                    "language_code": "ru",
                },
                "chat": {"id": chat_id, "type": "private"},
                "date": 1234567890,
                "text": text,
            },
        }

    with (
        patch.dict(os.environ, env_vars),
        patch(
            "mandala.adapters.telegram.webhook_delivery.deliver_outbound_messages"
        ) as mock_deliver,
        patch("mandala.adapters.telegram.webhook_delivery.TelegramBotApiClient"),
        patch(
            "mandala.services.text_reply.create_text_client_for_vertical",
        ) as mock_llm_factory,
    ):
        llm = Mock()
        llm.complete.return_value = "Демо-ответ ассистента (вертикаль astrology, тикет 12)."
        llm.close = Mock()
        mock_llm_factory.return_value = llm

        # Webhook ACK немедленный; собственно sync-turn покрыт webhook_delivery tests.
        responses = [client.post("/webhooks/telegram/astrology", json=_msg("/start", 1))]
        response = responses[-1]

    assert all(r.status_code == 200 for r in responses)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

    # Обработка запускается в фоне после ACK и не обязана завершиться внутри запроса.
    assert mock_llm_factory.call_count in (0, 1)
    assert mock_deliver.call_count in (0, 1)


@pytest.mark.integration
def test_web_chat_with_real_database() -> None:
    """Интеграция Web-канала: тот же handle_inbound, ответ JSON (тикет 21)."""
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not configured")

    app = create_app()
    client = TestClient(app)

    ext_uid = f"web-int-{uuid4().hex[:12]}"

    with patch(
        "mandala.services.text_reply.create_text_client_for_vertical",
    ) as mock_llm_factory:
        llm = Mock()
        llm.complete.return_value = "Демо-ответ ассистента (вертикаль astrology, тикет 12)."
        llm.close = Mock()
        mock_llm_factory.return_value = llm

        def send(text: str):  # type: ignore[no-untyped-def]
            return client.post(
                "/webhooks/web",
                json={"text": text, "vertical_id": "astrology"},
                headers={"X-External-User-Id": ext_uid},
            )

        with patch(
            "mandala.astro.natal_chart._geocode_city", return_value=(59.93, 30.31, "Europe/Moscow")
        ):
            inputs = [
                "/start",
                "Иван Иванов",
                _CONFIRM,
                "01.01.1990",
                _CONFIRM,
                "Санкт-Петербург",
                _CONFIRM,
                "10:15",
                _CONFIRM,
                _SAVE,
                "Неделя?",
            ]
            responses = [send(value) for value in inputs]
        r6 = responses[-1]

    for r in responses:
        assert r.status_code == 200, r.text
        body = r.json()
        assert "messages" in body
        assert len(body["messages"]) >= 1

    assert mock_llm_factory.call_count == 1
    llm.complete.assert_called_once()
    last = r6.json()["messages"][0]
    assert last.get("text") is not None
    assert "astrology" in last["text"]
