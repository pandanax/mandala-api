"""Предохранитель доставки: длинная подпись к фото (>1024) не роняет sendPhoto.

Если у сообщения есть И фото, И текст длиннее лимита подписи Telegram (1024), delivery-слой
шлёт фото с ОБРЕЗАННОЙ подписью и полный текст отдельным sendMessage; кнопки — на текстовом
(последнем) сообщении. Короткая подпись (≤1024) остаётся одним sendPhoto (не регрессим /natal).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mandala.adapters.telegram.outbound_send import (
    TELEGRAM_CAPTION_LIMIT,
    _truncate_caption,
    deliver_outbound_messages,
)
from mandala.domain import OutboundMessage


def test_long_caption_splits_photo_and_text() -> None:
    api = MagicMock()
    api.send_photo.return_value = {"photo": [{"file_id": "fid", "width": 10, "height": 10}]}
    long_text = "слово " * 400  # ~2400 символов, заведомо > 1024
    assert len(long_text) > TELEGRAM_CAPTION_LIMIT
    msg = OutboundMessage(
        text=long_text,
        photo="https://example/pic.png",
        buttons=[[{"text": "К темам", "callback_data": "mdl:topics"}]],
    )

    deliver_outbound_messages(api, chat_id=7, messages=[msg])

    # Ровно один sendPhoto с подписью ≤ лимита и БЕЗ кнопок (они уйдут на текст).
    assert api.send_photo.call_count == 1
    pkw = api.send_photo.call_args.kwargs
    caption = pkw.get("caption") or ""
    assert len(caption) <= TELEGRAM_CAPTION_LIMIT
    assert pkw.get("reply_markup") is None

    # Полный текст ушёл отдельным sendMessage, кнопки — на нём (последнем куске).
    assert api.send_message.call_count >= 1
    last = api.send_message.call_args
    assert last.kwargs.get("reply_markup") == {
        "inline_keyboard": [[{"text": "К темам", "callback_data": "mdl:topics"}]]
    }
    # Полный (необрезанный) текст присутствует в отправленном (по кускам).
    sent = "".join((c.kwargs.get("text") or "") for c in api.send_message.call_args_list)
    # Сравниваем по «сырому» смыслу: все слова доставлены (HTML-форматирование не режет их).
    assert "слово" in sent
    assert len(sent) >= len(long_text) - 5


def test_short_caption_single_send_photo_no_extra_message() -> None:
    api = MagicMock()
    api.send_photo.return_value = {"photo": [{"file_id": "fid", "width": 10, "height": 10}]}
    msg = OutboundMessage(text="🪐 короткая подпись", photo="https://example/pic.png", buttons=[])

    deliver_outbound_messages(api, chat_id=7, messages=[msg])

    assert api.send_photo.call_count == 1
    assert api.send_message.call_count == 0  # не регрессим колесо /natal


def test_caption_exactly_at_limit_not_split() -> None:
    api = MagicMock()
    api.send_photo.return_value = {"photo": [{"file_id": "fid", "width": 10, "height": 10}]}
    text = "x" * TELEGRAM_CAPTION_LIMIT  # ровно лимит — порог строго > 1024
    msg = OutboundMessage(text=text, photo="https://example/pic.png", buttons=[])

    deliver_outbound_messages(api, chat_id=7, messages=[msg])

    assert api.send_photo.call_count == 1
    assert api.send_message.call_count == 0


def test_truncate_caption_word_boundary_and_ellipsis() -> None:
    text = "alpha beta gamma " * 200
    out = _truncate_caption(text)
    assert len(out) <= TELEGRAM_CAPTION_LIMIT
    assert out.endswith("…")
    # Обрезка по границе слова: без «висящего» пробела перед «…».
    assert not out.endswith(" …")
