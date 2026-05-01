"""Текущая дата/время для system-слоя LLM (прогнозы «на этот месяц», «сегодня» и т.п.)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def build_llm_time_context_block(*, now: datetime | None = None) -> str:
    """Краткий блок для дописывания к системному промпту.

    Часовой пояс по умолчанию — ``Europe/Moscow`` (переопределение: env
    ``MANDALA_LLM_CONTEXT_TZ``, IANA-имя). При невалидном значении — UTC.
    """
    raw_tz = (os.environ.get("MANDALA_LLM_CONTEXT_TZ") or "Europe/Moscow").strip()
    try:
        tz: tzinfo = ZoneInfo(raw_tz)
        tz_label = raw_tz
    except (ZoneInfoNotFoundError, ValueError, OSError):
        tz = UTC
        tz_label = "UTC"

    if now is None:
        dt = datetime.now(tz)
    elif now.tzinfo is None:
        dt = now.replace(tzinfo=tz)
    else:
        dt = now.astimezone(tz)
    utc = dt.astimezone(UTC)
    local = dt.strftime("%Y-%m-%d %H:%M")
    if dt.tzname():
        local = f"{local} ({dt.tzname()})"

    return (
        "Текущие дата и время (ориентир для «сегодня», «этот месяц», «ближайший месяц» "
        "и любых прогнозов по периоду — не выдумывай календарь, опирайся на эти значения):\n"
        f"- {tz_label}: {local}\n"
        f"- UTC: {utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )
