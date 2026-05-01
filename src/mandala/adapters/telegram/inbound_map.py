"""Разбор ``Update`` Telegram → ``InboundEvent`` (тикет 9)."""

from __future__ import annotations

from typing import Any

from mandala.domain import InboundAttachment, InboundEvent

_CHANNEL = "telegram"


def _pick_largest_photo_file_id(photo: list[dict[str, Any]]) -> str | None:
    if not photo:
        return None
    last = photo[-1]
    fid = last.get("file_id")
    return str(fid) if fid is not None else None


def _attachments_from_message(msg: dict[str, Any]) -> list[InboundAttachment]:
    out: list[InboundAttachment] = []
    if "photo" in msg and isinstance(msg["photo"], list):
        fid = _pick_largest_photo_file_id(msg["photo"])
        if fid:
            out.append(InboundAttachment(kind="photo", file_id=fid))
    doc = msg.get("document")
    if isinstance(doc, dict):
        fid = doc.get("file_id")
        if fid is not None:
            out.append(InboundAttachment(kind="document", file_id=str(fid)))
    return out


def _message_body(msg: dict[str, Any]) -> tuple[str | None, list[InboundAttachment]]:
    text = msg.get("text")
    body_text: str | None
    if text is not None:
        body_text = str(text)
    else:
        cap = msg.get("caption")
        body_text = str(cap) if cap is not None else None
    return body_text, _attachments_from_message(msg)


def telegram_update_to_inbound_event(
    update: dict[str, Any],
    *,
    vertical_id: str,
) -> InboundEvent | None:
    """Построить ``InboundEvent`` или ``None``, если апдейт не обрабатываем (игнор).

    Поддерживаются: ``message``, ``edited_message``, ``callback_query``.
    """
    callback_data: str | None = None
    from_callback_query = False
    msg: dict[str, Any] | None = None
    actor: dict[str, Any] | None = None

    if "message" in update and isinstance(update["message"], dict):
        msg = update["message"]
        actor = msg.get("from") if isinstance(msg.get("from"), dict) else None
    elif "edited_message" in update and isinstance(update["edited_message"], dict):
        msg = update["edited_message"]
        actor = msg.get("from") if isinstance(msg.get("from"), dict) else None
    elif "callback_query" in update and isinstance(update["callback_query"], dict):
        cq = update["callback_query"]
        raw_d = cq.get("data")
        callback_data = str(raw_d) if raw_d is not None else None
        from_callback_query = True
        msg = cq.get("message") if isinstance(cq.get("message"), dict) else None
        actor = cq.get("from") if isinstance(cq.get("from"), dict) else None
        if actor is None:
            return None
        if msg is None:
            # У Telegram иногда нет ``message`` (очень старое сообщение с кнопкой и т.п.).
            # В личке с ботом ``chat_id`` для ответа совпадает с ``from.id``.
            if isinstance(actor, dict) and "id" in actor:
                uid = int(actor["id"])
                msg = {
                    "message_id": 0,
                    "chat": {"id": uid, "type": "private"},
                    "date": 0,
                }
            else:
                return None

    if msg is None:
        return None

    chat = msg.get("chat")
    if not isinstance(chat, dict) or "id" not in chat:
        return None
    chat_id = int(chat["id"])

    if isinstance(actor, dict) and "id" in actor:
        external_user_id = str(int(actor["id"]))
        loc = actor.get("language_code")
        locale_s = str(loc) if loc is not None else None
    else:
        external_user_id = str(chat_id)
        locale_s = None

    if from_callback_query:
        # Текст сообщения с клавиатурой — не ввод пользователя; в домен уходит действие кнопки.
        cb = (callback_data or "").strip()
        body_text = cb if cb else None
        attachments: list[InboundAttachment] = []
    else:
        body_text, attachments = _message_body(msg)
    raw_ref: dict[str, Any] = {"chat_id": chat_id}
    if "message_id" in msg:
        raw_ref["message_id"] = msg["message_id"]

    return InboundEvent(
        vertical_id=vertical_id,
        channel=_CHANNEL,
        external_user_id=external_user_id,
        text=body_text,
        attachments=attachments,
        callback_data=callback_data,
        locale=locale_s,
        raw_ref=raw_ref,
    )
