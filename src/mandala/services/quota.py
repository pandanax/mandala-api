"""Сервис квот: лимиты из ``plan_limits``, учёт в ``usage_counters`` (тикет 7).

Согласование периода с ``mandala.billing_period``:

- Ключ ``usage_counters.billing_period`` — календарный месяц ``YYYY-MM`` в UTC
  (``current_billing_period`` / ``billing_period_for_datetime``).
- Строки ``plan_limits`` с ``period = month`` относятся к тому же месячному окну:
  лимит применяется к счётчику за текущий календарный месяц (одна строка на ресурс).

Гонки при инкременте закрыты **одним атомарным**
``UPDATE … SET count = count + 1 WHERE … AND count < :limit``
в ``UsageRepository.try_increment``: Postgres сериализует конфликтующие обновления строки;
условие ``count < limit`` не позволит сумме превысить лимит даже при параллельных вызовах.

``can_consume`` только читает лимит и текущий ``count`` с ``FOR UPDATE`` в той же транзакции —
удобно для проверки перед дорогой операцией в одном ``BEGIN`` с последующим ``consume``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.billing_period import current_billing_period
from mandala.repositories.plans import PlanLimitDTO, PlanLimitsRepository
from mandala.repositories.usage import UsageRepository
from mandala.repositories.users import UsersRepository

# Ресурсы — значения ``usage_counters.resource`` / ``plan_limits.resource``
RESOURCE_TEXT_REPLY = "text_reply"
RESOURCE_IMAGE_GENERATION = "image_generation"

_PERIOD_MONTH = "month"

REASON_LIMIT_EXCEEDED = "limit_exceeded"
REASON_USER_OR_VERTICAL_MISMATCH = "user_or_vertical_mismatch"
REASON_NO_PLAN_LIMIT_ROW = "no_plan_limit_row"


@dataclass(frozen=True)
class QuotaConsumeResult:
    """Результат ``consume``: успешный расход или отказ без исключения."""

    allowed: bool
    reason: str | None = None


def _monthly_limit_for_resource(limits: list[PlanLimitDTO], resource: str) -> int | None:
    """Найти лимит для ресурса с месячным периодом (под ключ ``YYYY-MM``)."""
    for row in limits:
        if row.resource == resource and row.period == _PERIOD_MONTH:
            return row.limit_per_period
    return None


class QuotaService:
    """Квоты по ``(user_id, vertical_id, resource)`` в текущем биллинговом месяце."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def _resolve_limit(self, *, plan_id: UUID, resource: str) -> int | None:
        rows = PlanLimitsRepository(self._conn).list_for_plan(plan_id)
        return _monthly_limit_for_resource(rows, resource)

    def can_consume(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        resource: str,
        now: datetime | None = None,
    ) -> bool:
        """Вернуть, можно ли израсходовать единицу сейчас.

        Блокирует строку счётчика ``FOR UPDATE``. Узкий snapshot в транзакции ``conn``;
        для гарантии расхода используйте ``consume``.
        """
        users = UsersRepository(self._conn)
        plan_id = users.fetch_current_plan_id(user_id=user_id, vertical_id=vertical_id)
        if plan_id is None:
            return False
        limit = self._resolve_limit(plan_id=plan_id, resource=resource)
        if limit is None:
            return False
        period_key = current_billing_period(now)
        usage = UsageRepository(self._conn)
        count = usage.fetch_count_for_update(
            user_id=user_id,
            vertical_id=vertical_id,
            billing_period=period_key,
            resource=resource,
        )
        return count < limit

    def consume(
        self,
        *,
        user_id: UUID,
        vertical_id: str,
        resource: str,
        now: datetime | None = None,
    ) -> QuotaConsumeResult:
        """Атомарно увеличить счётчик на 1, если не исчерпан лимит текущего месяца.

        При ``limit_per_period == 0`` (например ``image_generation`` на ``free``) инкремента нет,
        возвращается отказ — вызывать внешние image API не следует.
        """
        users = UsersRepository(self._conn)
        plan_id = users.fetch_current_plan_id(user_id=user_id, vertical_id=vertical_id)
        if plan_id is None:
            return QuotaConsumeResult(allowed=False, reason=REASON_USER_OR_VERTICAL_MISMATCH)

        limit = self._resolve_limit(plan_id=plan_id, resource=resource)
        if limit is None:
            return QuotaConsumeResult(allowed=False, reason=REASON_NO_PLAN_LIMIT_ROW)

        period_key = current_billing_period(now)
        usage = UsageRepository(self._conn)
        ok = usage.try_increment(
            user_id=user_id,
            vertical_id=vertical_id,
            billing_period=period_key,
            resource=resource,
            limit=limit,
        )
        if ok:
            return QuotaConsumeResult(allowed=True, reason=None)
        return QuotaConsumeResult(allowed=False, reason=REASON_LIMIT_EXCEEDED)
