"""Календарный ключ периода для ``usage_counters.billing_period`` (тикеты 5, 7).

Формат **YYYY-MM** согласован с ``docs/data-model.md`` и миграцией ``t4_01_dialog_oltp``.
Вся логика нормализации периода для квот — только здесь, без дублирования в репозиториях.
Сервис квот (**``mandala.services.quota``**) подставляет ``current_billing_period()`` в счётчики;
месячные лимиты в ``plan_limits`` (**``period = month``**) — тот же календарный месяц в UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def billing_period_for_date(d: date) -> str:
    """Вернуть ``billing_period`` для календарного месяца даты ``d``."""
    return f"{d.year:04d}-{d.month:02d}"


def billing_period_for_datetime(ts: datetime) -> str:
    """Нормализовать ``ts`` в UTC и вернуть ``billing_period`` календарного месяца."""
    if ts.tzinfo is None:
        ts_utc = ts.replace(tzinfo=UTC)
    else:
        ts_utc = ts.astimezone(UTC)
    return billing_period_for_date(ts_utc.date())


def current_billing_period(now: datetime | None = None) -> str:
    """Текущий календарный месяц в UTC (для операций «сейчас»)."""
    ts = now if now is not None else datetime.now(tz=UTC)
    return billing_period_for_datetime(ts)
