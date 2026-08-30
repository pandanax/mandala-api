"""Регрессии polling: callback подтверждается сразу, временный сбой не убивает поток."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine

from mandala.adapters.telegram import polling


def _callback_update() -> dict[str, Any]:
    return {
        "update_id": 10,
        "callback_query": {
            "id": "cq-1",
            "from": {"id": 42, "is_bot": False},
            "message": {
                "message_id": 5,
                "chat": {"id": 42, "type": "private"},
                "date": 1,
                "text": "age: 41. Верно?",
            },
            "data": "mdl:intake:ok",
        },
    }


def test_process_update_acks_callback_before_domain_work(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    api = MagicMock()
    engine = MagicMock()

    monkeypatch.setattr(polling, "process_telegram_billing_update", lambda *a, **kw: False)
    monkeypatch.setattr(
        polling,
        "answer_callback_query_if_present",
        lambda *a, **kw: order.append("ack"),
    )
    monkeypatch.setattr(
        polling,
        "resolve_voice_to_text",
        lambda event, _api: SimpleNamespace(event=event, soft_message=None),
    )

    def _handle(*args: object, **kwargs: object) -> list[object]:
        order.append("domain")
        return []

    monkeypatch.setattr(polling, "handle_inbound", _handle)
    monkeypatch.setattr(polling, "run_with_typing_keepalive", lambda _a, _c, fn: fn())
    monkeypatch.setattr(polling, "deliver_outbound_messages", lambda *a, **kw: {})
    monkeypatch.setattr(polling, "persist_photo_file_ids", lambda *a, **kw: None)

    polling.process_telegram_update(
        _callback_update(),
        vertical_id="nutrition",
        engine=engine,
        api=api,
    )

    assert order == ["ack", "domain"]


def test_polling_retries_after_get_updates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeApi:
        calls = 0

        def __init__(self, token: str) -> None:
            self.token = token

        def __enter__(self) -> _FakeApi:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get_updates(self, *, offset: int | None, timeout: int) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary conflict")
            raise KeyboardInterrupt

    fake_api = _FakeApi("token")
    monkeypatch.setattr(polling, "TelegramBotApiClient", lambda token: fake_api)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(KeyboardInterrupt):
        polling.run_polling_forever(
            bot_token="123:abc",
            vertical_id="nutrition",
            engine=cast(Engine, object()),
        )

    assert fake_api.calls == 2
