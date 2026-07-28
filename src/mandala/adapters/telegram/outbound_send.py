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


def _largest_photo_file_id(result: dict[str, Any] | None) -> str | None:
    """``file_id`` самого крупного размера из ответа ``sendPhoto`` (для кэша)."""
    if not isinstance(result, dict):
        return None
    sizes = result.get("photo")
    if not isinstance(sizes, list) or not sizes:
        return None
    best: str | None = None
    best_area = -1
    for s in sizes:
        if not isinstance(s, dict):
            continue
        fid = s.get("file_id")
        if not isinstance(fid, str):
            continue
        area = int(s.get("width", 0) or 0) * int(s.get("height", 0) or 0)
        if area >= best_area:
            best_area = area
            best = fid
    return best


def _send_photo_caption_html_or_plain(
    api: TelegramBotApiClient,
    *,
    chat_id: int,
    photo: str | None,
    photo_bytes: bytes | None,
    filename: str,
    caption: str | None,
    reply_markup: dict[str, Any] | None,
    term_links: Sequence[dict[str, str]] | None = None,
    bot_username: str | None = None,
) -> dict[str, Any] | None:
    """Отправить фото (URL/``file_id`` или байты multipart'ом). Вернуть ответ ``sendPhoto``."""
    if caption is None:
        return api.send_photo(
            chat_id=chat_id,
            photo=photo,
            photo_bytes=photo_bytes,
            filename=filename,
            reply_markup=reply_markup,
        )
    formatted = format_llm_text_for_telegram_html(
        caption, term_links=term_links, bot_username=bot_username
    )
    try:
        return api.send_photo(
            chat_id=chat_id,
            photo=photo,
            photo_bytes=photo_bytes,
            filename=filename,
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
            return api.send_photo(
                chat_id=chat_id,
                photo=photo,
                photo_bytes=photo_bytes,
                filename=filename,
                caption=caption,
                reply_markup=reply_markup,
            )
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


def deliver_outbound_messages(
    api: TelegramBotApiClient,
    *,
    chat_id: int,
    messages: list[OutboundMessage],
    vertical_id: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, str]:
    """Отправить ответы пользователю (``sendMessage`` / ``sendPhoto``).

    ``vertical_id`` / ``user_id`` — только для операционных логов (тикет 20), без PII текста.

    Возвращает карту ``photo_cache_key → file_id`` для сообщений, у которых были
    загружены байты фото с заданным ``photo_cache_key`` — вызывающий код может
    сохранить эти ``file_id`` в ``agent_card`` для мгновенной переотправки. Обычно пусто.
    """
    uploaded_file_ids: dict[str, str] = {}
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

    # Постоянной нижней reply-клавиатуры больше нет — навигация только инлайн-кнопками
    # под сообщениями. У существующих пользователей клавиатура «залипла»; гасим её
    # одноразово, прикрепляя ReplyKeyboardRemove к первому сообщению без своей инлайн-
    # разметки в этом ответе (без лишнего пузыря и без нового состояния). Новые
    # пользователи начинают с /start (приветствие — сообщение без кнопок), поэтому
    # клавиатура снимается сразу; у остальных — на ближайшем /start.
    sticky_cleared = False

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
        if msg.buttons:
            markup = _buttons_to_reply_markup(msg.buttons)
        elif not sticky_cleared and (msg.text is not None or msg.photo or msg.photo_bytes):
            # Крепим только к реально отправляемому сообщению (иначе снятие «потеряется»).
            markup = {"remove_keyboard": True}
            sticky_cleared = True

        if msg.photo_bytes or msg.photo:
            result = _send_photo_caption_html_or_plain(
                api,
                chat_id=chat_id,
                photo=msg.photo,
                photo_bytes=msg.photo_bytes,
                filename=msg.photo_filename,
                caption=msg.text,
                reply_markup=markup,
                term_links=msg.term_links,
                bot_username=bot_username,
            )
            if msg.photo_bytes is not None and msg.photo_cache_key:
                fid = _largest_photo_file_id(result)
                if fid:
                    uploaded_file_ids[msg.photo_cache_key] = fid
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

    return uploaded_file_ids
