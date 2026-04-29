"""Репозиторий: планы и лимиты (тикеты 5, 8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class PlansRepository:
    """Чтение справочника ``plans`` (идентификатор по имени — для дефолтного ``free``)."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def fetch_id_by_name(self, name: str) -> UUID | None:
        """Вернуть ``plans.id`` по уникальному ``name`` или ``None``."""
        row = self._conn.execute(
            text("SELECT id FROM plans WHERE name = :name"),
            {"name": name},
        ).one_or_none()
        if row is None:
            return None
        pid = row[0]
        assert isinstance(pid, UUID)
        return pid

    def fetch_id_by_billing_product(
        self,
        *,
        billing_provider: str,
        external_product_id: str,
    ) -> UUID | None:
        """Сопоставить ``invoice_payload`` / внешний id товара с планом (тикет 19)."""
        row = self._conn.execute(
            text(
                """
                SELECT id
                FROM plans
                WHERE billing_provider = :bp
                  AND external_product_id = :eid
                """
            ),
            {"bp": billing_provider, "eid": external_product_id},
        ).one_or_none()
        if row is None:
            return None
        pid = row[0]
        assert isinstance(pid, UUID)
        return pid


@dataclass(frozen=True)
class PlanLimitDTO:
    """Одна строка ``plan_limits``."""

    resource: str
    limit_per_period: int
    period: str


class PlanLimitsRepository:
    """Чтение лимитов по ``plan_id``."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def list_for_plan(self, plan_id: UUID) -> list[PlanLimitDTO]:
        rows = self._conn.execute(
            text(
                """
                SELECT resource, limit_per_period, period::text AS period_label
                FROM plan_limits
                WHERE plan_id = :plan_id
                ORDER BY resource, period_label
                """
            ),
            {"plan_id": plan_id},
        ).all()
        return [PlanLimitDTO(r[0], int(r[1]), r[2]) for r in rows]
