"""Утренний девиз-мотиватор: настройки, «пора ли слать» и генерация контента.

Проактивная рассылка — **подарок**: квоту/баланс кошелька НЕ трогает (в отличие от
обычного текстового ответа). Формат — короткий ЛОЗУНГ-мотиватор (1–2 строки), звучит как
девиз дня, эмодзи ок; БЕЗ развёрнутого разбора планет. Время трактуется в **фиксированном
МСК** (``Europe/Moscow``) для всех — не по месту рождения.

Разделение ответственности:

* здесь — чистые функции (парсинг настроек, ``should_send_daily_forecast``, билдер контента).
  «now» инжектируется параметром, чтобы тесты были детерминированы;
* планировщик (фоновая asyncio-задача в HTTP-lifespan) и обход БД — в
  :mod:`mandala.adapters.telegram.daily_forecast_scheduler`;
* деттерминированная настройка ``/morning`` — в :mod:`mandala.services.daily_forecast_settings`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mandala.domain.contracts import OutboundMessage
from mandala.llm import ChatMessage, TextCompletionClient
from mandala.verticals.client_knowledge import (
    AGENT_CARD_ASTRO_SYSTEM,
    AGENT_CARD_DAILY_FORECAST_ENABLED,
    AGENT_CARD_DAILY_FORECAST_LAST_SENT,
    AGENT_CARD_DAILY_FORECAST_TIME,
    AGENT_CARD_NATAL_CHART_DATA,
)

logger = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")

# Дефолтное время рассылки (МСК), когда пользователь не менял настройку.
DEFAULT_FORECAST_TIME = "10:00"

# Окно догона: если процесс проспал/рестартовал, шлём только пока прошло не больше стольких
# минут после запланированного времени (иначе после ночного простоя можно улететь в 3 ночи).
CATCHUP_WINDOW_MINUTES = 180

# Малый потолок токенов для девиза — это одна-две строки, не разбор.
DAILY_SLOGAN_MAX_TOKENS = 220


def now_msk() -> datetime:
    """Текущее время в фиксированном МСК (провайдер по умолчанию; в тестах перекрывается)."""
    return datetime.now(tz=MSK)


def today_str_msk(now: datetime) -> str:
    """Дата (МСК) в формате ``YYYY-MM-DD`` для сравнения с ``last_sent`` (идемпотентность)."""
    return now.date().isoformat()


def parse_hhmm(raw: str) -> tuple[int, int] | None:
    """Разобрать ``"HH:MM"`` в ``(hour, minute)`` или ``None`` при невалидном вводе."""
    s = raw.strip()
    if ":" not in s:
        return None
    hh, _, mm = s.partition(":")
    hh, mm = hh.strip(), mm.strip()
    if not (hh.isdigit() and mm.isdigit()):
        return None
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def is_daily_forecast_enabled(agent_card: Mapping[str, Any]) -> bool:
    """Включена ли рассылка. **Отсутствие ключа = True** (дефолт ВКЛ для всех)."""
    val = agent_card.get(AGENT_CARD_DAILY_FORECAST_ENABLED)
    if val is None:
        return True
    return bool(val)


def daily_forecast_time(agent_card: Mapping[str, Any]) -> str:
    """Настроенное время рассылки ``"HH:MM"`` (МСК); дефолт :data:`DEFAULT_FORECAST_TIME`."""
    raw = agent_card.get(AGENT_CARD_DAILY_FORECAST_TIME)
    if isinstance(raw, str) and parse_hhmm(raw) is not None:
        # Нормализуем к HH:MM (без вольностей вроде «7:0»).
        h, m = parse_hhmm(raw)  # type: ignore[misc]
        return f"{h:02d}:{m:02d}"
    return DEFAULT_FORECAST_TIME


def should_send_daily_forecast(agent_card: Mapping[str, Any], now: datetime) -> bool:
    """Чистая функция «пора отправлять» (детерминированно, ``now`` инжектируется).

    True, если рассылка включена, сегодня ещё не отправляли, текущее МСК-время достигло
    запланированного и мы в окне догона (:data:`CATCHUP_WINDOW_MINUTES`). Иначе False.
    """
    if not is_daily_forecast_enabled(agent_card):
        return False

    last_sent = agent_card.get(AGENT_CARD_DAILY_FORECAST_LAST_SENT)
    if isinstance(last_sent, str) and last_sent.strip() == today_str_msk(now):
        return False  # уже слали сегодня

    parsed = parse_hhmm(daily_forecast_time(agent_card))
    if parsed is None:  # невозможно (time() нормализован), но на всякий случай
        return False
    cfg_h, cfg_m = parsed
    cfg_minutes = cfg_h * 60 + cfg_m
    now_minutes = now.hour * 60 + now.minute

    if now_minutes < cfg_minutes:
        return False  # ещё не наступило время
    if now_minutes - cfg_minutes > CATCHUP_WINDOW_MINUTES:
        return False  # окно догона прошло — не шлём с большим опозданием
    return True


def _astro_system(agent_card: Mapping[str, Any]) -> str:
    """Активная школа: приоритет — система рассчитанной карты, затем анкета, затем western."""
    natal = agent_card.get(AGENT_CARD_NATAL_CHART_DATA)
    if isinstance(natal, dict) and isinstance(natal.get("chart_system_key"), str):
        return natal["chart_system_key"] or "western"
    sys = agent_card.get(AGENT_CARD_ASTRO_SYSTEM)
    if isinstance(sys, str) and sys.strip():
        return sys.strip()
    return "western"


_SLOGAN_SYSTEM_PROMPT = (
    "Ты — тёплый астролог-навигатор. Твоя задача: дать короткий ДЕВИЗ ДНЯ — лозунг-мотиватор "
    "на 1–2 строки. Он должен звучать как девиз, заряжать и поднимать настроение. Пиши "
    "по-русски. Эмодзи уместны (1–2). НЕЛЬЗЯ: разбирать планеты, перечислять транзиты, писать "
    "абзацы, давать медицинские/финансовые советы, задавать вопросы. Только сам девиз, без "
    "префиксов вроде «Девиз дня:». Максимум две короткие строки."
)


def _slogan_user_prompt(agent_card: Mapping[str, Any], now: datetime) -> str:
    """Пользовательский промпт: лёгкая персонализация (транзиты дня) либо общий девиз.

    Позиции планет НЕ раскрываем пользователю — они лишь задают «настроение» дня для модели.
    Деградация: без даты рождения/карты — общий мотивирующий девиз (без астрологии).
    """
    lines = ["Составь девиз на сегодня."]
    birth_date = str(agent_card.get("birth_date") or "").strip()
    natal = agent_card.get(AGENT_CARD_NATAL_CHART_DATA)
    sun = natal.get("sun_sign") if isinstance(natal, dict) else None
    if isinstance(sun, str) and sun.strip():
        lines.append(f"Солнечный знак человека: {sun.strip()}.")
    # Транзиты дня — «фон настроения» (не показываем позиции в ответе).
    if birth_date:
        try:
            from mandala.astro.natal_chart import calculate_current_transits

            transits = calculate_current_transits(
                now.year, now.month, now.day, now.hour, system=_astro_system(agent_card)
            )
            planets = transits.get("planets", {})
            if isinstance(planets, dict) and planets:
                brief = ", ".join(
                    f"{p} в {d.get('sign', '?')}" for p, d in list(planets.items())[:4]
                )
                lines.append(
                    "Астрологический фон дня (только для твоего настроя, НЕ упоминай позиции "
                    f"в ответе): {brief}."
                )
        except Exception:
            logger.debug("daily slogan: transits unavailable, general slogan", exc_info=True)
    return "\n".join(lines)


def build_daily_slogan(
    agent_card: Mapping[str, Any],
    *,
    llm_client: TextCompletionClient,
    now: datetime,
) -> str | None:
    """Сгенерировать короткий девиз дня (LLM). ``None`` при сбое LLM — тогда НЕ шлём.

    Квоту НЕ дёргает: рассылка бесплатна. Деградация встроена в промпт (нет карты →
    общий девиз). Любые служебные хвосты (``---mandala---``/nav) на всякий случай срезаем.
    """
    chat = [
        ChatMessage(role="system", content=_SLOGAN_SYSTEM_PROMPT),
        ChatMessage(role="user", content=_slogan_user_prompt(agent_card, now)),
    ]
    try:
        reply = llm_client.complete(chat, max_tokens=DAILY_SLOGAN_MAX_TOKENS)
    except Exception:
        logger.warning("daily slogan LLM failed — skipping this user this tick", exc_info=True)
        return None
    text = _strip_service_suffixes(reply).strip()
    if not text:
        return None
    return text


def _strip_service_suffixes(reply: str) -> str:
    """Срезать возможные служебные блоки (nav / agent-card), если модель их добавила."""
    from mandala.services.nav_protocol import split_llm_nav_suffix
    from mandala.verticals.client_knowledge import split_llm_agent_card_suffix

    head, _ = split_llm_nav_suffix(reply)
    head, _ = split_llm_agent_card_suffix(head)
    return head


def build_daily_forecast_message(slogan: str) -> OutboundMessage:
    """Собрать исходящее сообщение утреннего девиза с инлайн-навигацией (короткие лейблы)."""
    return OutboundMessage(
        text=slogan,
        buttons=[
            [
                {"text": "📊 Подробнее", "callback_data": "mdl:fc_today"},
                {"text": "⚙️ Настроить", "callback_data": "mdl:morning"},
            ]
        ],
    )
