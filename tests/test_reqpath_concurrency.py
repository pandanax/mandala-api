"""Путь ответа не блокирует event-loop: параллельные ходы исполняются одновременно.

Синхронный ход (БД-транзакция + сетевой LLM-вызов) уводится в worker-поток
(`anyio.to_thread.run_sync`) на обоих входах — web (`http/web_chat.py`) и Telegram
webhook (`adapters/telegram/webhook_delivery.py`). Здесь мы моделируем медленный ход
блокирующим ``time.sleep`` (как и сетевой read, он отпускает GIL) и проверяем, что
N параллельных запросов завершаются примерно за время ОДНОГО хода, а не за сумму —
т.е. медленный ход одного пользователя не сериализует ответ другому.

Тесты синхронные (без ``pytest-asyncio``): драйвят ASGI-приложение через
``httpx.AsyncClient`` внутри ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from mandala.domain.contracts import OutboundMessage
from mandala.http.app import create_app

# Длительность одного «медленного» хода и число параллельных запросов подобраны так,
# чтобы сериализованный путь (N * SLEEP) уверенно отличался от конкурентного (~SLEEP).
_SLEEP_SEC = 0.4
_N_CONCURRENT = 3
# Порог: между одним ходом (~SLEEP) и сериализацией (N * SLEEP). При N=3 это 0.8с —
# конкурентный путь (~0.4с) проходит, сериализованный (~1.2с) — нет.
_MAX_ELAPSED_SEC = 2 * _SLEEP_SEC


class _FakeConn:
    """Заглушка соединения: в тестах ``handle_inbound`` замокан и его не использует."""


class _FakeBegin:
    def __enter__(self) -> _FakeConn:
        return _FakeConn()

    def __exit__(self, *args: object) -> None:
        return None


class _FakeEngine:
    def begin(self) -> _FakeBegin:
        return _FakeBegin()


async def _gather_post(
    app: Any,
    path: str,
    payloads: list[tuple[dict[str, Any], dict[str, str]]],
) -> tuple[list[httpx.Response], float]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = time.perf_counter()
        responses = await asyncio.gather(
            *(client.post(path, json=body, headers=headers) for body, headers in payloads)
        )
        elapsed = time.perf_counter() - start
    return list(responses), elapsed


def test_web_inbound_does_not_serialize_slow_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Медленный web-ход одного пользователя не блокирует ход другого."""

    def slow_handle(event: Any, conn: Any) -> list[OutboundMessage]:
        time.sleep(_SLEEP_SEC)
        return [OutboundMessage(text="ok")]

    monkeypatch.setattr("mandala.http.web_chat.handle_inbound", slow_handle)
    monkeypatch.setattr("mandala.http.web_chat.get_engine", lambda: _FakeEngine())

    app = create_app()
    payloads = [
        (
            {"text": "hi", "vertical_id": "astrology"},
            {"X-External-User-Id": f"user-{i}"},
        )
        for i in range(_N_CONCURRENT)
    ]
    responses, elapsed = asyncio.run(_gather_post(app, "/webhooks/web", payloads))

    assert [r.status_code for r in responses] == [200] * _N_CONCURRENT
    assert all(r.json()["messages"][0]["text"] == "ok" for r in responses)
    assert elapsed < _MAX_ELAPSED_SEC, (
        f"{_N_CONCURRENT} параллельных web-ходов заняли {elapsed:.2f}s "
        f"(>= {_MAX_ELAPSED_SEC:.2f}s) — похоже, ходы сериализуются (event-loop блокируется)"
    )


def test_telegram_webhook_does_not_serialize_slow_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Медленный Telegram-ход одного чата не блокирует webhook другого."""

    def slow_process(update_data: dict[str, Any], *, vertical_id: str) -> None:
        time.sleep(_SLEEP_SEC)

    # Async-обёртка process_telegram_webhook_update_async вызывает этот sync-символ
    # по module-global; подменяя его, проверяем именно offload в поток.
    monkeypatch.setattr(
        "mandala.adapters.telegram.webhook_delivery.process_telegram_webhook_update",
        slow_process,
    )
    # Без секрета проверка X-Telegram-Bot-Api-Secret-Token не выполняется.
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    app = create_app()
    payloads: list[tuple[dict[str, Any], dict[str, str]]] = []
    for i in range(_N_CONCURRENT):
        update = {
            "update_id": 1000 + i,
            "message": {
                "message_id": i,
                "from": {"id": i, "is_bot": False, "first_name": "T", "language_code": "ru"},
                "chat": {"id": i, "type": "private"},
                "date": 1234567890,
                "text": "привет",
            },
        }
        payloads.append((update, {}))

    responses, elapsed = asyncio.run(_gather_post(app, "/webhooks/telegram/astrology", payloads))

    assert [r.status_code for r in responses] == [200] * _N_CONCURRENT
    assert all(r.json()["status"] == "ok" for r in responses)
    assert elapsed < _MAX_ELAPSED_SEC, (
        f"{_N_CONCURRENT} параллельных webhook-ходов заняли {elapsed:.2f}s "
        f"(>= {_MAX_ELAPSED_SEC:.2f}s) — похоже, ходы сериализуются (event-loop блокируется)"
    )
