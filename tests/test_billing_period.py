"""Юнит-тесты ``billing_period`` (тикет 5)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from mandala.billing_period import (
    billing_period_for_date,
    billing_period_for_datetime,
    current_billing_period,
)


def test_billing_period_for_date() -> None:
    assert billing_period_for_date(date(2026, 3, 15)) == "2026-03"


def test_billing_period_for_datetime_naive_utc() -> None:
    d = datetime(2026, 1, 7, 12, 0, 0)
    assert billing_period_for_datetime(d) == "2026-01"


def test_billing_period_for_datetime_zurich_cross_month() -> None:
    from zoneinfo import ZoneInfo

    ts = datetime(2026, 1, 1, 0, 30, tzinfo=ZoneInfo("Europe/Zurich"))
    assert billing_period_for_datetime(ts) == "2025-12"


def test_current_billing_period_fixed_clock() -> None:
    fixed = datetime(2026, 7, 2, tzinfo=UTC)
    assert current_billing_period(fixed) == "2026-07"
