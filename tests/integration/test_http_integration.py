"""Интеграционные тесты HTTP приложения с реальной БД (тикет 10)."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

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
        patch("mandala.http.app.deliver_outbound_messages") as mock_deliver,
        patch(
            "mandala.services.text_reply.create_text_client_for_vertical",
        ) as mock_llm_factory,
    ):
        llm = Mock()
        llm.complete.return_value = "Демо-ответ ассистента (вертикаль astrology, тикет 12)."
        llm.close = Mock()
        mock_llm_factory.return_value = llm

        # Тикет 13: сначала анкета (4 шага для astrology), затем диалог с LLM
        r1 = client.post("/webhooks/telegram/astrology", json=_msg("/start", 1))
        r2 = client.post("/webhooks/telegram/astrology", json=_msg("Иван Иванов", 2))
        r3 = client.post("/webhooks/telegram/astrology", json=_msg("01.01.1990", 3))
        r4 = client.post("/webhooks/telegram/astrology", json=_msg("Санкт-Петербург", 4))
        r5 = client.post("/webhooks/telegram/astrology", json=_msg("10:15", 5))
        r6 = client.post("/webhooks/telegram/astrology", json=_msg("Что скажешь про неделю?", 6))
        response = r6

    assert (
        r1.status_code == 200
        and r2.status_code == 200
        and r3.status_code == 200
        and r4.status_code == 200
        and r5.status_code == 200
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

    assert mock_llm_factory.call_count == 1
    llm.complete.assert_called_once()
    assert mock_deliver.call_count == 6

    call_args = mock_deliver.call_args
    assert call_args[1]["chat_id"] == chat_id
    messages = call_args[1]["messages"]
    assert len(messages) > 0
    assert messages[0].text is not None
    assert "astrology" in messages[0].text


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

        r1 = client.post(
            "/webhooks/web",
            json={"text": "/start", "vertical_id": "astrology"},
            headers={"X-External-User-Id": ext_uid},
        )
        r2 = client.post(
            "/webhooks/web",
            json={"text": "Иван Иванов", "vertical_id": "astrology"},
            headers={"X-External-User-Id": ext_uid},
        )
        r3 = client.post(
            "/webhooks/web",
            json={"text": "01.01.1990", "vertical_id": "astrology"},
            headers={"X-External-User-Id": ext_uid},
        )
        r4 = client.post(
            "/webhooks/web",
            json={"text": "Санкт-Петербург", "vertical_id": "astrology"},
            headers={"X-External-User-Id": ext_uid},
        )
        r5 = client.post(
            "/webhooks/web",
            json={"text": "10:15", "vertical_id": "astrology"},
            headers={"X-External-User-Id": ext_uid},
        )
        r6 = client.post(
            "/webhooks/web",
            json={"text": "Неделя?", "vertical_id": "astrology"},
            headers={"X-External-User-Id": ext_uid},
        )

    for r in (r1, r2, r3, r4, r5, r6):
        assert r.status_code == 200, r.text
        body = r.json()
        assert "messages" in body
        assert len(body["messages"]) >= 1

    assert mock_llm_factory.call_count == 1
    llm.complete.assert_called_once()
    last = r6.json()["messages"][0]
    assert last.get("text") is not None
    assert "astrology" in last["text"]
