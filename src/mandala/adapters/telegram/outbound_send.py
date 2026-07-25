"""Доставка ``OutboundMessage`` в Telegram (тикет 9)."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from mandala.adapters.telegram.bot_api import TelegramApiError, TelegramBotApiClient
from mandala.adapters.telegram.text_format import (
    format_llm_text_for_telegram_html,
    split_text_for_telegram,
)
from mandala.domain import OutboundMessage
from mandala.observability import op_format

logger = logging.getLogger(__name__)

# Кэш username бота по токену: getMe вызываем один раз, а не на каждое сообщение.
_bot_username_cache: dict[str, str] = {}


def _resolve_bot_username(api: TelegramBotApiClient) -> str | None:
    """Username бота для deep-link ссылок (термины).

    Приоритет: env ``TELEGRAM_BOT_USERNAME`` (без сетевого вызова) → ``getMe`` с кэшем.
    Любая ошибка → ``None`` (термины отрендерятся обычным текстом — безопасная деградация).
    """
    env = os.environ.get("TELEGRAM_BOT_USERNAME")
    if env and env.strip():
        return env.strip().lstrip("@")
    token = str(getattr(api, "_token", "") or "")
    if token in _bot_username_cache:
        return _bot_username_cache[token] or None
    resolved = ""
    try:
        me = api.get_me()
        username = me.get("username")
        if isinstance(username, str) and username.strip():
            resolved = username.strip().lstrip("@")
    except Exception:  # noqa: BLE001 — доставка не должна падать из-за getMe
        logger.warning("getMe failed while resolving bot username", exc_info=True)
    _bot_username_cache[token] = resolved
    return resolved or None


def _telegram_entity_parse_failed(err: TelegramApiError) -> bool:
    d = err.description.lower()
    return "parse" in d or "entity" in d


def _send_message_html_or_plain(
    api: TelegramBotApiClient,
    *,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None,
    term_links: Sequence[dict[str, str]] | None = None,
    bot_username: str | None = None,
) -> None:
    formatted = format_llm_text_for_telegram_html(
        text, term_links=term_links, bot_username=bot_username
    )
    try:
        api.send_message(
            chat_id=chat_id,
            text=formatted,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramApiError as e:
        if _telegram_entity_parse_failed(e):
            logger.warning(
                "telegram sendMessage HTML parse failed, fallback plain chat_id=%s err=%s",
                chat_id,
                e.description,
            )
            api.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        else:
            raise


def _send_photo_caption_html_or_plain(
    api: TelegramBotApiClient,
    *,
    chat_id: int,
    photo: str,
    caption: str | None,
    reply_markup: dict[str, Any] | None,
    term_links: Sequence[dict[str, str]] | None = None,
    bot_username: str | None = None,
) -> None:
    if caption is None:
        api.send_photo(chat_id=chat_id, photo=photo, reply_markup=reply_markup)
        return
    formatted = format_llm_text_for_telegram_html(
        caption, term_links=term_links, bot_username=bot_username
    )
    try:
        api.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=formatted,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramApiError as e:
        if _telegram_entity_parse_failed(e):
            logger.warning(
                "telegram sendPhoto caption HTML parse failed, fallback plain chat_id=%s err=%s",
                chat_id,
                e.description,
            )
            api.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
            )
        else:
            raise


def _buttons_to_reply_markup(buttons: list[list[dict[str, str]]]) -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for row in buttons:
        line: list[dict[str, str]] = []
        for cell in row:
            text = cell.get("text", "")
            btn: dict[str, str] = {"text": text}
            if "url" in cell:
                btn["url"] = cell["url"]
            else:
                btn["callback_data"] = cell.get("callback_data", text)
            line.append(btn)
        rows.append(line)
    return {"inline_keyboard": rows}


def _reply_keyboard_to_markup(keyboard: list[list[str]]) -> dict[str, Any]:
    rows = [[{"text": btn} for btn in row] for row in keyboard]
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "persistent": True,
        "one_time_keyboard": False,
    }


def deliver_outbound_messages(
    api: TelegramBotApiClient,
    *,
    chat_id: int,
    messages: list[OutboundMessage],
    vertical_id: str | None = None,
    user_id: UUID | None = None,
) -> None:
    """Отправить ответы пользователю (``sendMessage`` / ``sendPhoto``).

    ``vertical_id`` / ``user_id`` — только для операционных логов (тикет 20), без PII текста.
    """
    if vertical_id is not None and messages:
        n_photo = sum(1 for m in messages if m.photo)
        logger.info(
            "funnel outbound %s",
            op_format(
                vertical_id=vertical_id,
                user_id=user_id,
                stage="telegram_deliver",
                n_messages=len(messages),
                n_photo=n_photo,
            ),
        )
    # Username бота нужен только если есть кликабельные термины — резолвим лениво один раз.
    bot_username: str | None = None
    if any(m.term_links for m in messages):
        bot_username = _resolve_bot_username(api)

    for msg in messages:
        # Счёт на оплату (Telegram Stars): выставляем через sendInvoice. Счёт несёт свой
        # заголовок/описание/цену, поэтому это терминальное сообщение — text/photo на нём
        # не отправляем (builder такие поля не заполняет).
        if msg.invoice is not None:
            inv = msg.invoice
            api.send_invoice(
                chat_id=chat_id,
                title=inv.title,
                description=inv.description,
                payload=inv.payload,
                prices=[{"label": inv.title, "amount": inv.amount_stars}],
                currency="XTR",
            )
            continue

        markup: dict[str, Any] | None = None
        if msg.reply_keyboard:
            markup = _reply_keyboard_to_markup(msg.reply_keyboard)
        elif msg.buttons:
            markup = _buttons_to_reply_markup(msg.buttons)

        if msg.photo:
            _send_photo_caption_html_or_plain(
                api,
                chat_id=chat_id,
                photo=msg.photo,
                caption=msg.text,
                reply_markup=markup,
                term_links=msg.term_links,
                bot_username=bot_username,
            )
        elif msg.text is not None:
            parts = split_text_for_telegram(msg.text)
            for i, part in enumerate(parts):
                # Клавиатуру (reply_markup) крепим только к последнему куску.
                part_markup = markup if i == len(parts) - 1 else None
                _send_message_html_or_plain(
                    api,
                    chat_id=chat_id,
                    text=part,
                    reply_markup=part_markup,
                    term_links=msg.term_links,
                    bot_username=bot_username,
                )
        # TODO(тикет 12+): ``defer`` — сценарий отложенных ответов (оплата — см. ``invoice`` выше).
