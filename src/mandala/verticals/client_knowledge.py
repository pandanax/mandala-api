"""Долгоживущие поля профиля (``agent_card``), в т.ч. извлечение из ответа LLM."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Ключ в ``agent_card``: сохранённый текст натальной карты (не пересчитывается без запроса).
AGENT_CARD_NATAL_CHART_TEXT = "natal_chart_text"

# Ключ в ``agent_card``: математически рассчитанная натальная карта (dict от astro.natal_chart).
AGENT_CARD_NATAL_CHART_DATA = "natal_chart_data"

# Ключ в ``agent_card``: рассчитанная Матрица Судьбы (dict от astro.destiny_matrix).
# Считается и сохраняется при сохранении профиля; ``/matrix`` рендерит её мгновенно из БД.
AGENT_CARD_DESTINY_MATRIX_DATA = "destiny_matrix_data"

# Ключ в ``agent_card``: рассчитанная пифагорейская нумерология (dict от astro.numerology).
# Использует ИМЯ + дату рождения (в отличие от Матрицы — только дата). Считается и
# сохраняется при сохранении профиля; ``/numerology`` рендерит её мгновенно из БД.
AGENT_CARD_NUMEROLOGY_DATA = "numerology_data"

# Ключ в ``agent_card``: активная система (western/vedic).
AGENT_CARD_ASTRO_SYSTEM = "astro_system"

# --- Утренняя рассылка (проактивный девиз-мотиватор, см. services/daily_forecast.py) ---
# Все три ключа живут в ``agent_card`` (без миграции). Время трактуется в фиксированном
# МСК (Europe/Moscow) для всех — не по месту рождения.
#
# Включена ли рассылка. **Отсутствие ключа = True** (по умолчанию ВКЛ для существующих и новых).
AGENT_CARD_DAILY_FORECAST_ENABLED = "daily_forecast_enabled"
# Время рассылки "HH:MM" в МСК (дефолт "10:00").
AGENT_CARD_DAILY_FORECAST_TIME = "daily_forecast_time"
# Дата последней успешной отправки "YYYY-MM-DD" (МСК) — идемпотентность (переживает рестарт).
AGENT_CARD_DAILY_FORECAST_LAST_SENT = "daily_forecast_last_sent"

# Ключ в ``agent_card``: Telegram ``file_id`` уже загруженного колеса натальной карты.
# Кэш: первый ``/natal`` грузит PNG-байты, Telegram возвращает ``file_id`` — сохраняем и
# переиспользуем при следующих ``/natal`` (мгновенно, без перерисовки). Инвалидируется
# (сбрасывается в "") при пересчёте карты — правка профиля/смена системы.
AGENT_CARD_NATAL_WHEEL_FILE_ID = "natal_wheel_file_id"

# Маркер в конце ответа ассистента (отдельная строка после ``\\n``).
MANDALA_AGENT_CARD_MARKER = "---mandala---"

# Какие ключи разрешено дописывать из ответа модели (остальное игнорируем).
ALLOWED_AGENT_CARD_KEYS_FROM_LLM = frozenset({AGENT_CARD_NATAL_CHART_TEXT})

_MAX_NATAL_CHART_CHARS = 12_000


def split_llm_agent_card_suffix(assistant_reply: str) -> tuple[str, dict[str, Any]]:
    """Отделить хвост ``---mandala---`` + JSON от текста для пользователя.

    Возвращает ``(текст_для_чата, patch_для_merge_agent_card)``; ``patch`` пустой, если не найдено.
    """
    raw = assistant_reply.strip()
    if MANDALA_AGENT_CARD_MARKER not in raw:
        return (assistant_reply, {})
    idx = raw.rfind(MANDALA_AGENT_CARD_MARKER)
    head = raw[:idx].rstrip()
    tail = raw[idx + len(MANDALA_AGENT_CARD_MARKER) :].strip()
    if not tail:
        return (head if head else assistant_reply, {})
    try:
        parsed = json.loads(tail)
    except json.JSONDecodeError:
        logger.warning("mandala agent_card JSON parse failed tail_len=%d", len(tail))
        return (assistant_reply, {})
    if not isinstance(parsed, dict):
        return (assistant_reply, {})
    patch: dict[str, Any] = {}
    for k, v in parsed.items():
        if k not in ALLOWED_AGENT_CARD_KEYS_FROM_LLM:
            continue
        if not isinstance(v, str):
            continue
        s = v.strip()
        if not s:
            continue
        if k == AGENT_CARD_NATAL_CHART_TEXT and len(s) > _MAX_NATAL_CHART_CHARS:
            s = s[:_MAX_NATAL_CHART_CHARS]
        patch[k] = s
    if not patch:
        return (assistant_reply, {})
    return (head if head else assistant_reply, patch)
