"""Детерминированная настройка утренней рассылки (``/morning``) — без LLM.

Показывает текущее состояние (вкл/выкл + время МСК) и меняет его инлайн-кнопками либо
текстовыми командами:

* ``/morning`` или callback ``mdl:morning`` — показать настройку;
* ``/morning on`` / ``/morning off`` (и callbacks ``mdl:morning:on`` / ``mdl:morning:off``);
* ``/morning HH:MM`` или пресет-кнопка ``mdl:morning:set:HH:MM`` — задать время (МСК).

Настройки живут в ``client_profiles.agent_card`` (без миграции). Роутится в
``domain/handler`` до анкеты, поэтому работает в любом состоянии профиля.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.domain.contracts import OutboundMessage
from mandala.repositories.profiles import ProfileRepository
from mandala.services.daily_forecast import (
    daily_forecast_time,
    is_daily_forecast_enabled,
    parse_hhmm,
)
from mandala.verticals.client_knowledge import (
    AGENT_CARD_DAILY_FORECAST_ENABLED,
    AGENT_CARD_DAILY_FORECAST_TIME,
)

_MORNING_COMMAND = "/morning"
_CB_PREFIX = "mdl:morning"
_CB_ON = "mdl:morning:on"
_CB_OFF = "mdl:morning:off"
_CB_SET_PREFIX = "mdl:morning:set:"

# Пресеты времени (МСК) кнопками.
_TIME_PRESETS = ("07:00", "08:00", "09:00", "10:00", "11:00", "12:00")


def _cmd_head_and_arg(text: str) -> tuple[str, str]:
    """Разбить ``/morning 08:30`` → (``/morning``, ``08:30``); форму ``/cmd@bot`` учитываем."""
    parts = text.strip().split(maxsplit=1)
    head = parts[0]
    if "@" in head:
        head = head.split("@", 1)[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    return head.lower(), arg


def is_daily_forecast_action(text: str | None) -> bool:
    """True, если ``text`` — команда/кнопка настройки утренней рассылки."""
    if text is None:
        return False
    raw = text.strip()
    if not raw:
        return False
    if raw.startswith(_CB_PREFIX):
        return True
    head, _ = _cmd_head_and_arg(raw)
    return head == _MORNING_COMMAND


def _btn(label: str, cb: str) -> dict[str, str]:
    return {"text": label, "callback_data": cb}


def _settings_message(*, enabled: bool, time_hhmm: str, note: str | None = None) -> OutboundMessage:
    """Собрать сообщение с текущим состоянием и кнопками (короткие лейблы)."""
    status = f"🔔 включена, {time_hhmm} МСК" if enabled else "🔕 отключена"
    lines = [
        "☀️ **Утренний прогноз** — короткий девиз-мотиватор каждое утро.",
        f"Сейчас: {status}.",
    ]
    if note:
        lines.append("")
        lines.append(note)
    lines.append("")
    lines.append("Выберите время (МСК) или включите/отключите:")

    toggle = _btn("🔕 Отключить", _CB_OFF) if enabled else _btn("🔔 Включить", _CB_ON)
    preset_row1 = [_btn(f"🕐 {t}", f"{_CB_SET_PREFIX}{t}") for t in _TIME_PRESETS[:3]]
    preset_row2 = [_btn(f"🕐 {t}", f"{_CB_SET_PREFIX}{t}") for t in _TIME_PRESETS[3:]]
    buttons = [
        [toggle],
        preset_row1,
        preset_row2,
        [_btn("⬅️ К темам", "mdl:topics")],
    ]
    return OutboundMessage(text="\n".join(lines), buttons=buttons)


def handle_daily_forecast_action(
    conn: Connection,
    *,
    user_id: UUID,
    text: str,
) -> list[OutboundMessage]:
    """Обработать команду/кнопку настройки рассылки и вернуть ответ (это уже action).

    Меняет ``agent_card`` детерминированно (без LLM) и показывает актуальное состояние.
    """
    profiles = ProfileRepository(conn)
    fresh = profiles.get_by_user_id(user_id)
    ac = dict(fresh.agent_card) if fresh else {}

    raw = text.strip()
    note: str | None = None

    # --- Разбор действия -------------------------------------------------------------
    if raw.startswith(_CB_SET_PREFIX):
        new_time = raw[len(_CB_SET_PREFIX) :].strip()
        note = _apply_time(profiles, user_id, ac, new_time)
    elif raw == _CB_ON:
        note = _apply_enabled(profiles, user_id, ac, True)
    elif raw == _CB_OFF:
        note = _apply_enabled(profiles, user_id, ac, False)
    elif raw.startswith(_CB_PREFIX):
        note = None  # просто показать настройку (mdl:morning)
    else:
        # Текстовая команда /morning [on|off|HH:MM]
        _, arg = _cmd_head_and_arg(raw)
        arg_l = arg.lower()
        if arg_l in ("on", "вкл", "включить"):
            note = _apply_enabled(profiles, user_id, ac, True)
        elif arg_l in ("off", "выкл", "отключить"):
            note = _apply_enabled(profiles, user_id, ac, False)
        elif arg:
            note = _apply_time(profiles, user_id, ac, arg)
        # без аргумента — просто показать настройку

    return [
        _settings_message(
            enabled=is_daily_forecast_enabled(ac),
            time_hhmm=daily_forecast_time(ac),
            note=note,
        )
    ]


def _apply_enabled(
    profiles: ProfileRepository, user_id: UUID, ac: dict[str, Any], value: bool
) -> str:
    profiles.merge_agent_card(user_id, {AGENT_CARD_DAILY_FORECAST_ENABLED: value})
    ac[AGENT_CARD_DAILY_FORECAST_ENABLED] = value
    return "✅ Утренний прогноз включён." if value else "🔕 Утренний прогноз отключён."


def _apply_time(
    profiles: ProfileRepository, user_id: UUID, ac: dict[str, Any], raw_time: str
) -> str:
    parsed = parse_hhmm(raw_time)
    if parsed is None:
        return "⚠️ Не понял время. Пример: /morning 08:30 (часы:минуты, МСК)."
    h, m = parsed
    normalized = f"{h:02d}:{m:02d}"
    # Установка времени включает рассылку (иначе бессмысленно выбирать время).
    profiles.merge_agent_card(
        user_id,
        {AGENT_CARD_DAILY_FORECAST_TIME: normalized, AGENT_CARD_DAILY_FORECAST_ENABLED: True},
    )
    ac[AGENT_CARD_DAILY_FORECAST_TIME] = normalized
    ac[AGENT_CARD_DAILY_FORECAST_ENABLED] = True
    return f"✅ Время утреннего прогноза: {normalized} МСК."
