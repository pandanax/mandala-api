"""Пакеты сообщений (prepaid): единый источник правды (payload ↔ цена ⭐ ↔ +сообщений).

Пакетная («кошельковая») монетизация вместо месячных лимитов/подписки: пользователь
покупает пакет сообщений, который **добавляется** к балансу кошелька и **не сгорает**.
Каждый LLM-ответ списывает 1 сообщение (см. ``mandala.services.quota`` и
``mandala.repositories.wallet``). Стартовый разовый грант новому пользователю —
``starting_balance()``.

Три пакета. Цены (в Stars) и гранты (кол-во сообщений) параметризуются env с дефолтами:

- ``100``  — ``mandala_pack_100``  — ``1 ⭐`` → ``100`` сообщений
- ``300``  — ``mandala_pack_300``  — ``2 ⭐`` → ``300`` сообщений
- ``1000`` — ``mandala_pack_1000`` — ``5 ⭐`` → ``1000`` сообщений

``pack_id`` — стабильный идентификатор (числовой лейбл дефолтного гранта); он же суффикс
``payload`` (``mandala_pack_<id>``) и хвост callback-кнопки (``mdl:pack:<id>``). ``payload`` —
это ``invoice_payload`` счёта Stars и уникальный ключ товара в журнале покупок
(``payment_transactions``), поэтому он НЕ должен меняться при смене цены/гранта через env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

# Стартовый разовый грант новому пользователю (не возобновляется, не сбрасывается /reset).
_START_BALANCE_ENV: Final = "MANDALA_MESSAGE_WALLET_START"
_DEFAULT_START_BALANCE: Final = 20


@dataclass(frozen=True)
class MessagePack:
    """Один пакет сообщений (значения уже разрезолвлены с учётом env)."""

    pack_id: str
    payload: str
    price_stars: int
    messages: int

    @property
    def title(self) -> str:
        """Заголовок счёта (видит пользователь при оплате)."""
        return f"Пакет сообщений · {self.messages}"

    @property
    def description(self) -> str:
        """Описание товара в счёте Stars."""
        return f"{self.messages} сообщений на баланс кошелька. Не сгорают. Оплата — Telegram Stars."

    @property
    def button_label(self) -> str:
        """Подпись инлайн-кнопки пакета в пикере покупки."""
        return f"{self.price_stars} ⭐ · {self.messages} сообщений"


# (pack_id, price_env, default_price, messages_env, default_messages)
_SPEC: Final[tuple[tuple[str, str, int, str, int], ...]] = (
    ("100", "MANDALA_PACK_100_PRICE", 1, "MANDALA_PACK_100_MESSAGES", 100),
    ("300", "MANDALA_PACK_300_PRICE", 2, "MANDALA_PACK_300_MESSAGES", 300),
    ("1000", "MANDALA_PACK_1000_PRICE", 5, "MANDALA_PACK_1000_MESSAGES", 1000),
)

# Стабильные идентификаторы пакетов (не зависят от env; для валидации callback-кнопок).
PACK_IDS: Final[tuple[str, ...]] = tuple(spec[0] for spec in _SPEC)


def _env_positive_int(name: str, default: int) -> int:
    """Прочитать целое ≥ 1 из env или вернуть дефолт (нечисло/≤0 → дефолт)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _build_pack(
    pack_id: str,
    price_env: str,
    default_price: int,
    messages_env: str,
    default_messages: int,
) -> MessagePack:
    return MessagePack(
        pack_id=pack_id,
        payload=f"mandala_pack_{pack_id}",
        price_stars=_env_positive_int(price_env, default_price),
        messages=_env_positive_int(messages_env, default_messages),
    )


def all_packs() -> list[MessagePack]:
    """Все три пакета (цены/гранты берутся из env на момент вызова)."""
    return [_build_pack(*spec) for spec in _SPEC]


def pack_by_id(pack_id: str) -> MessagePack | None:
    """Пакет по ``pack_id`` (``"100"`` / ``"300"`` / ``"1000"``) или ``None``."""
    pid = pack_id.strip()
    for spec in _SPEC:
        if spec[0] == pid:
            return _build_pack(*spec)
    return None


def pack_by_payload(payload: str) -> MessagePack | None:
    """Пакет по ``invoice_payload`` (``mandala_pack_<id>``) или ``None``."""
    p = payload.strip()
    for pack in all_packs():
        if pack.payload == p:
            return pack
    return None


def starting_balance() -> int:
    """Стартовый разовый грант новому пользователю (env ``MANDALA_MESSAGE_WALLET_START``)."""
    return _env_positive_int(_START_BALANCE_ENV, _DEFAULT_START_BALANCE)
