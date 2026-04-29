"""Pydantic-модели входа и выхода по ``docs/channels.md`` (тикет 6)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InboundAttachment(BaseModel):
    """Элемент списка ``InboundEvent.attachments`` (фото, документы и т.д.).

    Таблица в ``docs/channels.md`` не фиксирует подполя: конкретные ключи задаёт
    адаптер канала (например Telegram — тикет 9). Здесь — минимальный каркас для валидации.
    """

    model_config = ConfigDict(extra="allow")

    kind: str = Field(
        ...,
        description="Тип вложения в терминах адаптера (например ``photo``, ``document``).",
    )
    file_id: str | None = Field(
        default=None,
        description="Идентификатор файла в канале (например Telegram ``file_id``), если есть.",
    )


class InboundEvent(BaseModel):
    """Нормализованное входящее событие после парсинга канала.

    Семантика полей — ``docs/channels.md``, раздел «Вход: InboundEvent».
    """

    model_config = ConfigDict(extra="forbid")

    vertical_id: str = Field(
        ...,
        description=(
            "Slug вертикали (агент/продукт), по которому маршрутизируются конфиг, KB и "
            "``channel_links``. На входе в домен **уже задан** до ``handle_inbound``: его "
            "проставляет адаптер или тонкий слой перед ним (Telegram/Web/CLI, см. "
            "``docs/channels.md``). Резолвинг токена бота → ``vertical_id`` — зона адаптеров "
            "(тикеты 9–10), не этого контракта."
        ),
    )
    channel: str = Field(
        ...,
        description="Идентификатор канала: ``telegram``, ``web``, ``cli`` и т.д.",
    )
    external_user_id: str = Field(
        ...,
        description="Стабильный id пользователя в этом канале в рамках вертикали.",
    )
    text: str | None = Field(
        default=None,
        description="Текст сообщения пользователя, если есть.",
    )
    attachments: list[InboundAttachment] = Field(
        default_factory=list,
        description="Вложения (фото, документы), если канал их передаёт.",
    )
    callback_data: str | None = Field(
        default=None,
        description="Данные inline-кнопок и аналогов.",
    )
    locale: str | None = Field(
        default=None,
        description="Локаль пользователя/клиента, если доступна.",
    )
    raw_ref: dict[str, Any] | None = Field(
        default=None,
        description="Опциональная ссылка на сырой объект канала для ответа (например ``chat_id``).",
    )


class OutboundMessage(BaseModel):
    """Универсальное представление ответа пользователю.

    Семантика полей — ``docs/channels.md``, раздел «Выход: OutboundMessage».
    Поле ``vertical_id`` в контракте выхода **не требуется**: вертикаль уже в контексте
    обработки и логов (см. ``docs/channels.md``).
    """

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(
        default=None,
        description="Текст ответа (разметка — по соглашению с каналом-адаптером).",
    )
    buttons: list[list[dict[str, str]]] | None = Field(
        default=None,
        description="Опционально: клавиатура / inline (структура задаётся адаптером; тикет 9+).",
    )
    photo: str | None = Field(
        default=None,
        description="Фото: URL, ``file_id`` или иной идентификатор по возможностям канала.",
    )
    requires_payment: bool = Field(
        default=False,
        description=(
            "Признак UI оплаты (например Stars-only в Telegram); детали — в последующих тикетах."
        ),
    )
    defer: bool = Field(
        default=False,
        description="«Ответ позже» для долгих задач (например генерация в worker).",
    )
