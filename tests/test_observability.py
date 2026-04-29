"""Тикет 20: операционное логирование — формат полей и маскирование."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from mandala.adapters.telegram.outbound_send import deliver_outbound_messages
from mandala.domain import OutboundMessage
from mandala.observability import mask_api_key, op_format


def test_op_format_stable_order() -> None:
    uid = UUID("00000000-0000-4000-8000-000000000001")
    s = op_format(
        vertical_id="astrology",
        user_id=uid,
        stage="test",
        resource="text_reply",
        outcome="allow",
        reason="none",
        extra_field="x",
    )
    assert s.startswith("vertical_id=astrology")
    assert "user_id=00000000-0000-4000-8000-000000000001" in s
    assert "stage=test" in s
    assert "extra_field=x" in s


def test_op_format_requires_vertical_id() -> None:
    with pytest.raises(TypeError):
        op_format(user_id=UUID(int=1))  # type: ignore[call-arg]


def test_mask_api_key() -> None:
    assert mask_api_key("") == "(empty)"
    assert mask_api_key("short") == "…"
    assert mask_api_key("sk-1234567890abcdef") == "sk-1…ef"


def test_deliver_outbound_logs_funnel_smoke() -> None:
    """Smoke: при ``vertical_id`` логгер INFO вызывается; текст ответа не попадает в поля лога."""
    with patch("mandala.adapters.telegram.outbound_send.logger") as log:
        api = MagicMock()
        deliver_outbound_messages(
            api,
            chat_id=1,
            messages=[OutboundMessage(text="секретный ответ пользователю")],
            vertical_id="therapy",
        )
    log.info.assert_called_once()
    msg, suffix = log.info.call_args[0]
    assert msg == "funnel outbound %s"
    assert "vertical_id=therapy" in suffix
    assert "секретный" not in suffix
    assert "n_messages=1" in suffix
