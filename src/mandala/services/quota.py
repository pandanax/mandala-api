"""Сервис квот поверх **кошелька сообщений** (пакетная монетизация).

Модель — prepaid-баланс на ``users.message_balance`` (см. ``mandala.repositories.wallet``),
а не месячные лимиты. Каждый **LLM-ответ** (ресурс ``text_reply``) списывает 1 сообщение;
мгновенные детерминированные рендеры (``/natal`` и т.п.) сюда не заходят и не тарифицируются.

- ``can_consume`` — можно ли ответить сейчас: активное **промо** («вечный пакет») → всегда да;
  иначе для ``text_reply`` — ``баланс > 0``.
- ``consume`` — атомарно списать 1 сообщение (без ухода в минус, без гонок — условие в самом
  ``UPDATE`` кошелька); при промо списания нет.

**Картинки не тарифицируются из кошелька** (решение капитана). Ресурс ``image_generation``
доступен только при безлимитном промо (обрабатывается веткой промо выше); без промо —
отказ, кошелёк не трогается (на free картинок и так нет).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.observability import op_format
from mandala.repositories.wallet import WalletRepository

logger = logging.getLogger(__name__)

# Ресурсы — тип операции, которую проверяем/списываем.
RESOURCE_TEXT_REPLY = "text_reply"
RESOURCE_IMAGE_GENERATION = "image_generation"

REASON_INSUFFICIENT_BALANCE = "insufficient_balance"
# Не-текстовые ресурсы (картинки) кошелёк не покрывает — доступны только по промо.
REASON_NOT_IN_WALLET = "not_in_wallet"

# Обратная совместимость: прежний код/тесты ссылались на этот код отказа.
REASON_LIMIT_EXCEEDED = REASON_INSUFFICIENT_BALANCE


@dataclass(frozen=True)
class QuotaConsumeResult:
    """Результат ``consume``: успешный расход или отказ без исключения."""

    allowed: bool
    reason: str | None = None


class QuotaService:
    """Квоты по ``(user_id, vertical_id, resource)`` поверх баланса кошелька."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def can_consume(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        resource: str,
    ) -> bool:
        """Вернуть, можно ли израсходовать единицу ресурса сейчас (без списания)."""
        from mandala.services.promo import is_promo_active

        if is_promo_active(user_id=user_id, vertical_id=vertical_id, conn=self._conn):
            logger.info(
                "funnel quota %s",
                op_format(
                    vertical_id=vertical_id,
                    user_id=user_id,
                    stage="can_consume",
                    resource=resource,
                    outcome="allow",
                    reason="promo_active",
                ),
            )
            return True

        if resource != RESOURCE_TEXT_REPLY:
            # Картинки и прочее — не из кошелька; без промо недоступны.
            logger.info(
                "funnel quota %s",
                op_format(
                    vertical_id=vertical_id,
                    user_id=user_id,
                    stage="can_consume",
                    resource=resource,
                    outcome="deny",
                    reason=REASON_NOT_IN_WALLET,
                ),
            )
            return False

        balance = WalletRepository(self._conn).get_balance(user_id=user_id, vertical_id=vertical_id)
        allowed = balance is not None and balance > 0
        logger.info(
            "funnel quota %s",
            op_format(
                vertical_id=vertical_id,
                user_id=user_id,
                stage="can_consume",
                resource=resource,
                outcome="allow" if allowed else "deny",
                reason=None if allowed else REASON_INSUFFICIENT_BALANCE,
                balance=balance,
            ),
        )
        return allowed

    def consume(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        resource: str,
    ) -> QuotaConsumeResult:
        """Атомарно списать 1 сообщение с баланса, если он положительный.

        При активном промо-коде («вечный пакет») списания нет — безлимит. Не-текстовые
        ресурсы (картинки) кошелёк не тарифицирует: без промо — отказ без списания.
        """
        from mandala.services.promo import is_promo_active

        if is_promo_active(user_id=user_id, vertical_id=vertical_id, conn=self._conn):
            logger.debug(
                "funnel quota %s",
                op_format(
                    vertical_id=vertical_id,
                    user_id=user_id,
                    stage="consume",
                    resource=resource,
                    outcome="allow",
                    reason="promo_active",
                ),
            )
            return QuotaConsumeResult(allowed=True, reason=None)

        if resource != RESOURCE_TEXT_REPLY:
            # Картинки не списываем из кошелька (капитан): нейтрально, без побочных эффектов.
            return QuotaConsumeResult(allowed=False, reason=REASON_NOT_IN_WALLET)

        new_balance = WalletRepository(self._conn).try_consume(
            user_id=user_id, vertical_id=vertical_id
        )
        if new_balance is not None:
            logger.debug(
                "funnel quota %s",
                op_format(
                    vertical_id=vertical_id,
                    user_id=user_id,
                    stage="consume",
                    resource=resource,
                    outcome="allow",
                    balance=new_balance,
                ),
            )
            return QuotaConsumeResult(allowed=True, reason=None)
        logger.info(
            "funnel quota %s",
            op_format(
                vertical_id=vertical_id,
                user_id=user_id,
                stage="consume",
                resource=resource,
                outcome="deny",
                reason=REASON_INSUFFICIENT_BALANCE,
            ),
        )
        return QuotaConsumeResult(allowed=False, reason=REASON_INSUFFICIENT_BALANCE)
