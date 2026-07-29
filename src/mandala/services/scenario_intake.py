"""Сбор профиля по конфигу вертикали с пофазовым подтверждением (UX-апгрейд).

Чистое ядро состояний — :mod:`mandala.services.intake_flow` (без БД/сети). Здесь —
обёртка над БД: применяет патчи ядра, пишет в ``client_profiles`` / ``messages``,
резолвит город (сеть), при сохранении профиля синхронно считает и сохраняет
натальную карту (Swiss Ephemeris), Матрицу Судьбы и пифагорейскую нумерологию, и
гарантирует инлайн-навигацию на каждом ответе.

Служебные команды (``/start``, ``/help``, ``/profile``, ``/promo``, ``/topup``,
``/natal``, ``/matrix``, ``/numerology``, ``/reset``) перехватываются до валидации
шага. ``/natal``, ``/matrix`` и ``/numerology`` — мгновенный детерминированный рендер
из БД (без LLM), см. :mod:`mandala.services.chart_render`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.repositories.messages import MessageRepository
from mandala.repositories.profiles import ClientProfileDTO, ProfileRepository
from mandala.repositories.wallet import WalletRepository
from mandala.services.chart_render import (
    render_destiny_matrix_message,
    render_natal_chart_message,
    render_numerology_message,
)
from mandala.services.intake_flow import (
    CB_RESTART,
    INTAKE_SCHEMA_VERSION,
    KEY_INTAKE_COMPLETE,
    KEY_INTAKE_DRAFT,
    KEY_INTAKE_EDIT_ACTIVE,
    KEY_INTAKE_PENDING,
    KEY_INTAKE_PHASE,
    KEY_INTAKE_RETURN_SUMMARY,
    KEY_INTAKE_SCHEMA_VERSION,
    KEY_INTAKE_STEP_INDEX,
    PlaceResolution,
    PlaceResolveError,
    input_prompt_buttons,
    is_intake_callback,
    step_intake,
)
from mandala.services.nav_guarantee import ensure_nav, fallback_nav_buttons
from mandala.services.profile_view import build_profile_message
from mandala.services.telegram_stars import build_packs_picker_with_balance
from mandala.services.term_linkify import numerology_term_buttons
from mandala.verticals.client_knowledge import (
    AGENT_CARD_ASTRO_SYSTEM,
    AGENT_CARD_DESTINY_MATRIX_DATA,
    AGENT_CARD_NATAL_CHART_DATA,
    AGENT_CARD_NATAL_WHEEL_FILE_ID,
    AGENT_CARD_NUMEROLOGY_DATA,
)
from mandala.verticals.intake_config import IntakeStep, intake_steps_for_vertical
from mandala.verticals.post_intake_offers import post_intake_completion_message

logger = logging.getLogger(__name__)

# Команды, которые мы трактуем как UX-навигацию, а не как ответ на шаг анкеты.
_SOFT_RESTART_COMMANDS = frozenset({"/start", "/restart"})
_HARD_RESET_COMMANDS = frozenset({"/reset"})
_INFO_COMMANDS = frozenset({"/help", "/about", "/info"})
_PROMO_COMMANDS = frozenset({"/promo"})
_TOPUP_COMMANDS = frozenset({"/topup"})
_PROFILE_COMMANDS = frozenset({"/profile"})
# Мгновенный рендер из БД (без LLM): натальная карта, Карта судьбы, нумерология.
_NATAL_COMMANDS = frozenset({"/natal"})
_MATRIX_COMMANDS = frozenset({"/matrix"})
_NUMEROLOGY_COMMANDS = frozenset({"/numerology"})
_ALL_COMMANDS = (
    _SOFT_RESTART_COMMANDS
    | _HARD_RESET_COMMANDS
    | _INFO_COMMANDS
    | _PROMO_COMMANDS
    | _TOPUP_COMMANDS
    | _PROFILE_COMMANDS
    | _NATAL_COMMANDS
    | _MATRIX_COMMANDS
    | _NUMEROLOGY_COMMANDS
)


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def _guarantee_all_nav(messages: list[OutboundMessage], vertical_id: str) -> list[OutboundMessage]:
    """Гарантировать непустые инлайн-кнопки на КАЖДОМ сообщении (сквозное требование).

    В отличие от :func:`ensure_nav` (только терминальное сообщение), здесь фолбэк
    добавляется любому не-invoice сообщению без своих кнопок. Интерактивные шаги
    анкеты уже несут свои кнопки — их не трогаем.
    """
    if not messages:
        return messages
    fallback = fallback_nav_buttons(vertical_id)
    if fallback is None:
        return ensure_nav(messages, vertical_id)
    for i, msg in enumerate(messages):
        if msg.invoice is not None:
            continue
        if msg.buttons is None and (msg.text or msg.photo):
            messages[i] = msg.model_copy(update={"buttons": fallback})
    return messages


def _resolve_place_factory() -> Any:
    """Собрать инъектируемый резолвер места для ядра анкеты (сеть — здесь).

    Оборачивает :func:`mandala.astro.natal_chart._geocode_city`, переводя его
    технические ``ValueError`` в понятные пользователю сообщения. При неудаче
    бросает :class:`PlaceResolveError` — ядро НЕ примет ответ и переспросит место.
    """

    def _resolve(city: str) -> PlaceResolution:
        from mandala.astro.natal_chart import _geocode_city

        try:
            lat, lng, tz = _geocode_city(city)
        except ValueError as exc:
            msg = str(exc)
            if "Timezone" in msg:
                user = (
                    f"⚠️ Не удалось определить часовой пояс места «{city}» — без него нельзя "
                    "точно рассчитать карту по местному времени рождения. Уточните ближайший "
                    "крупный город."
                )
            elif "City not found" in msg or "Geocoding failed" in msg:
                user = (
                    f"⚠️ Не удалось найти город «{city}». Проверьте написание или укажите "
                    "ближайший крупный город."
                )
            else:
                user = f"⚠️ Не удалось проверить место «{city}». Укажите ближайший крупный город."
            raise PlaceResolveError(user) from exc
        return PlaceResolution(lat=lat, lng=lng, tz=tz, resolved_name=city)

    return _resolve


def handle_intake_before_llm(
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    profile: ClientProfileDTO,
) -> list[OutboundMessage] | None:
    """Если нужна анкета/правка — обработать ход и вернуть ответы; иначе ``None``.

    Возвращает ``None`` только когда управление надо передать пайплайну LLM
    (завершённый профиль без активной правки и обычный текст/кнопка не относятся к анкете).
    """
    steps = intake_steps_for_vertical(event.vertical_id)
    if steps is None:
        return None

    state = profile.scenario_state
    user_text = (event.text or "").strip()
    vertical = event.vertical_id

    # 1. Служебные команды — до анкеты и после (в т.ч. мгновенные /natal, /matrix).
    cmd = _extract_command(user_text)
    if cmd is not None:
        return _guarantee_all_nav(
            _handle_command(
                conn=conn,
                event=event,
                user_id=user_id,
                state=state,
                steps=steps,
                command=cmd,
                intake_complete=bool(state.get(KEY_INTAKE_COMPLETE)),
            ),
            vertical,
        )

    complete = bool(state.get(KEY_INTAKE_COMPLETE))
    phase = str(state.get(KEY_INTAKE_PHASE) or "")

    # 2. Гейт: анкета активна (собираем/правим) или пришла кнопка анкеты/правки?
    active = bool(phase) or not complete
    if not active and not is_intake_callback(user_text):
        return None  # завершённый профиль + обычный ввод → к LLM

    # 3. Ведём машину состояний ядра.
    return _guarantee_all_nav(
        _drive_intake(conn=conn, event=event, user_id=user_id, profile=profile, steps=steps),
        vertical,
    )


def _drive_intake(
    *,
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    profile: ClientProfileDTO,
    steps: Sequence[IntakeStep],
) -> list[OutboundMessage]:
    """Один ход ядра + применение патчей в БД (+ финализация профиля)."""
    outcome = step_intake(
        steps=steps,
        state=dict(profile.scenario_state),
        agent_card=dict(profile.agent_card),
        user_text=event.text or "",
        resolve_place=_resolve_place_factory(),
    )
    profiles = ProfileRepository(conn)

    # Зафиксированное поле пишем в историю (как раньше — чтобы контекст LLM был целостным).
    if outcome.committed_field is not None:
        field_key, value = outcome.committed_field
        MessageRepository(conn).insert(
            user_id=user_id,
            vertical_id=event.vertical_id,
            role="user",
            content_text=value,
            content_kind="text",
            content_meta={"intake_field": field_key},
        )

    if outcome.finalize:
        if outcome.state_patch:
            profiles.merge_scenario_state(user_id, outcome.state_patch)
        return _finalize_profile(
            conn=conn,
            user_id=user_id,
            profiles=profiles,
            vertical_id=event.vertical_id,
            agent_card_patch=outcome.agent_card_patch,
            editing=outcome.editing,
        )

    if outcome.state_patch:
        profiles.merge_scenario_state(user_id, outcome.state_patch)
    if outcome.agent_card_patch:
        profiles.merge_agent_card(user_id, outcome.agent_card_patch)
    return list(outcome.messages)


def _finalize_profile(
    *,
    conn: Connection,
    user_id: UUID,
    profiles: ProfileRepository,
    vertical_id: str,
    agent_card_patch: dict[str, Any],
    editing: bool,
) -> list[OutboundMessage]:
    """Атомарно сохранить профиль и синхронно посчитать карту+матрицу (astrology).

    Профиль пишется в БД ТОЛЬКО здесь (после подтверждения всей анкеты). Затем
    строгой математикой считаются натальная карта (Swiss Ephemeris) и Матрица Судьбы
    и сохраняются в ``agent_card`` — дальше ``/natal`` и ``/matrix`` рендерят мгновенно.
    """
    if agent_card_patch:
        profiles.merge_agent_card(user_id, agent_card_patch)

    fresh = profiles.get_by_user_id(user_id)
    ac = dict(fresh.agent_card) if fresh else dict(agent_card_patch)

    geo_error: str | None = None
    if vertical_id.strip() == "astrology":
        geo_error = _try_calculate_and_save_natal_chart(
            conn=conn, user_id=user_id, agent_card=ac, profiles=profiles
        )
        _try_compute_and_save_matrix(user_id=user_id, agent_card=ac, profiles=profiles)
        _try_compute_and_save_numerology(user_id=user_id, agent_card=ac, profiles=profiles)
        fresh2 = profiles.get_by_user_id(user_id)
        ac = dict(fresh2.agent_card) if fresh2 else ac

    intro = "✅ Профиль обновлён." if editing else "✅ Профиль сохранён."
    completion = post_intake_completion_message(vertical_id, ac)
    completion = completion.model_copy(
        update={"text": f"{intro}\n\n{completion.text}" if completion.text else intro}
    )
    msgs = [completion]
    if geo_error:
        msgs.append(OutboundMessage(text=geo_error))
    logger.info(
        "intake finalized vertical_id=%s user_id=%s editing=%s geo_error=%s",
        vertical_id,
        user_id,
        editing,
        bool(geo_error),
    )
    return msgs


def _try_compute_and_save_matrix(
    *,
    user_id: UUID,
    agent_card: dict[str, Any],
    profiles: ProfileRepository,
) -> None:
    """Посчитать Матрицу Судьбы из даты рождения и сохранить в ``agent_card``.

    Матрица — чистая нумерология даты (без эфемерид/времени/места), поэтому не может
    «не резолвиться» как город. Любой сбой — некритичен (просто не сохраняем).
    """
    birth_date = str(agent_card.get("birth_date") or "").strip()
    if not birth_date:
        return
    try:
        from mandala.astro.destiny_matrix import compute_destiny_matrix

        dm = compute_destiny_matrix(birth_date)
        profiles.merge_agent_card(user_id, {AGENT_CARD_DESTINY_MATRIX_DATA: dm})
        logger.info("destiny matrix computed user_id=%s", user_id)
    except Exception:
        logger.warning("destiny matrix computation failed user_id=%s", user_id, exc_info=True)


def _try_compute_and_save_numerology(
    *,
    user_id: UUID,
    agent_card: dict[str, Any],
    profiles: ProfileRepository,
) -> None:
    """Посчитать пифагорейскую нумерологию (имя + дата) и сохранить в ``agent_card``.

    В отличие от Матрицы (только дата), использует и ``full_name``, и ``birth_date``.
    Имя необязательно: без него движок аккуратно деградирует до чисел от даты. Любой
    сбой некритичен — просто не сохраняем.
    """
    birth_date = str(agent_card.get("birth_date") or "").strip()
    if not birth_date:
        return
    full_name = str(agent_card.get("full_name") or "").strip()
    try:
        from mandala.astro.numerology import compute_numerology

        data = compute_numerology(full_name, birth_date)
        profiles.merge_agent_card(user_id, {AGENT_CARD_NUMEROLOGY_DATA: data})
        logger.info("numerology computed user_id=%s has_name=%s", user_id, data.get("has_name"))
    except Exception:
        logger.warning("numerology computation failed user_id=%s", user_id, exc_info=True)


def _handle_promo_command(
    *,
    conn: Connection,
    user_id: UUID,
    vertical_id: str,
    code: str,
) -> list[OutboundMessage]:
    from mandala.services.promo import activate_promo, get_active_promo

    if not code:
        active = get_active_promo(user_id=user_id, vertical_id=vertical_id, conn=conn)
        if active:
            text = (
                f"✅ Активен промо-код: `{active}` — вечный пакет "
                "(безлимит навсегда).\n\nЧтобы применить другой — отправьте: /promo КОД"
            )
        else:
            text = "Промо-код не активирован. Чтобы применить — отправьте: /promo КОД"
        return [OutboundMessage(text=text, buttons=_promo_nav_buttons())]
    activated = activate_promo(code=code, user_id=user_id, vertical_id=vertical_id, conn=conn)
    if activated:
        text = "✅ Промо-код активирован! Подписка без ограничений навсегда.\n\nЧто дальше?"
    else:
        text = "❌ Неверный промо-код. Попробуйте другой (/promo КОД)."
    # Инлайн-навигация после промо (требование: что делать дальше — кнопками).
    return [OutboundMessage(text=text, buttons=_promo_nav_buttons())]


def _promo_nav_buttons() -> list[list[dict[str, str]]]:
    return [
        [_btn("🪐 Натальная карта", "/natal"), _btn("🌌 Карта судьбы", "/matrix")],
        [_btn("📊 Прогноз", "mdl:forecast_menu"), _btn("👤 Профиль", "mdl:profile")],
    ]


# Значения birth_time, означающие «время неизвестно» (совпадает с parse в natal_chart).
_TIME_UNKNOWN = frozenset({"unknown", "не знаю", "незнаю", "", "?"})


def _natal_wheel_caption(birth_date: str, birth_time: str, birth_place: str) -> str:
    """Подпись к колесу: дата · [время] · место (без имени — оно на карту не влияет).

    Время показываем только когда оно известно; при неизвестном — без пустого
    разделителя (``дата · место``).
    """
    parts = [birth_date]
    if birth_time.strip().lower() not in _TIME_UNKNOWN:
        parts.append(birth_time.strip())
    parts.append(birth_place)
    return "🪐 Натальная карта · " + " · ".join(parts)


def _natal_wheel_photo_message(agent_card: dict[str, Any]) -> OutboundMessage | None:
    """Сообщение с КОЛЕСОМ натальной карты (фото) или ``None``, если построить нельзя.

    Быстрый путь — кэш ``file_id`` (мгновенно, без перерисовки). Иначе рендерим PNG-байты
    колеса (детерминированно, kerykeion+cairosvg) и просим канал закешировать полученный
    ``file_id``. Любая ошибка рендера → ``None`` (``/natal`` мягко деградирует до текста).
    Кнопок у фото НЕТ (``buttons=[]``): навигация — на терминальном текстовом сообщении;
    пустой список также помечает «здесь nav не нужен» для ``_guarantee_all_nav``.
    """
    birth_date = str(agent_card.get("birth_date") or "").strip()
    birth_place = str(agent_card.get("birth_place") or "").strip()
    if not birth_date or not birth_place:
        return None
    birth_time = str(agent_card.get("birth_time") or "unknown").strip()
    # Колесо зависит от даты, ВРЕМЕНИ и места (не от имени) — показываем их в подписи.
    # Время добавляем только когда оно известно (иначе — без пустого разделителя).
    caption = _natal_wheel_caption(birth_date, birth_time, birth_place)
    cached = agent_card.get(AGENT_CARD_NATAL_WHEEL_FILE_ID)
    if isinstance(cached, str) and cached:
        return OutboundMessage(photo=cached, text=caption, buttons=[])
    system = str(agent_card.get(AGENT_CARD_ASTRO_SYSTEM) or "western")
    # Координаты берём из сохранённой карты (без повторного геокодинга → /natal офлайновый).
    coords: tuple[float, float, str] | None = None
    natal = agent_card.get(AGENT_CARD_NATAL_CHART_DATA)
    geo = natal.get("geo") if isinstance(natal, dict) else None
    if isinstance(geo, dict) and geo.get("lat") is not None and geo.get("tz"):
        coords = (float(geo["lat"]), float(geo["lng"]), str(geo["tz"]))
    try:
        from mandala.services.chart_wheel import render_natal_wheel_png

        png = render_natal_wheel_png(birth_date, birth_time, birth_place, system, coords=coords)
    except Exception:
        logger.warning("natal wheel render failed place=%r", birth_place, exc_info=True)
        return None
    return OutboundMessage(
        photo_bytes=png,
        photo_filename="natal_wheel.png",
        photo_cache_key=AGENT_CARD_NATAL_WHEEL_FILE_ID,
        text=caption,
        buttons=[],
    )


def _natal_messages(
    agent_card: dict[str, Any], natal_data: dict[str, Any]
) -> list[OutboundMessage]:
    """Колесо (фото) + блочный текстовый разбор с инлайн-навигацией.

    Фото — ведущее сообщение (короткая подпись), полный блочный разбор — отдельным
    текстом (caption Telegram ~1024 симв. не вмещает весь блок). Nav — на тексте.
    """
    messages: list[OutboundMessage] = []
    wheel = _natal_wheel_photo_message(agent_card)
    if wheel is not None:
        messages.append(wheel)
    messages.append(render_natal_chart_message(natal_data))
    return messages


def _instant_natal(conn: Connection, user_id: UUID) -> list[OutboundMessage]:
    """``/natal``: мгновенный рендер сохранённой карты (при отсутствии — пересчёт)."""
    profiles = ProfileRepository(conn)
    fresh = profiles.get_by_user_id(user_id)
    ac = dict(fresh.agent_card) if fresh else {}
    natal = ac.get(AGENT_CARD_NATAL_CHART_DATA)
    if isinstance(natal, dict) and natal:
        return _natal_messages(ac, natal)

    if str(ac.get("birth_date") or "").strip() and str(ac.get("birth_place") or "").strip():
        geo_error = _try_calculate_and_save_natal_chart(
            conn=conn, user_id=user_id, agent_card=ac, profiles=profiles
        )
        fresh2 = profiles.get_by_user_id(user_id)
        ac2 = dict(fresh2.agent_card) if fresh2 else {}
        natal2 = ac2.get(AGENT_CARD_NATAL_CHART_DATA)
        if isinstance(natal2, dict) and natal2:
            return _natal_messages(ac2, natal2)
        text = geo_error or (
            "Не удалось рассчитать натальную карту. Проверьте дату, место и время рождения."
        )
        return [OutboundMessage(text=text, buttons=[[_btn("👤 Профиль", "mdl:profile")]])]

    return [
        OutboundMessage(
            text=(
                "Чтобы рассчитать натальную карту, заполните анкету: имя, дата, место и "
                "время рождения."
            ),
            buttons=[[_btn("📝 Заполнить анкету", CB_RESTART)]],
        )
    ]


def _matrix_message_with_clickable_terms(
    profiles: ProfileRepository, user_id: UUID, dm: dict[str, Any]
) -> OutboundMessage:
    """Рендер Карты судьбы + детерминированные кнопки-термины (2–5 ключевых).

    Рендер ``/matrix`` не проходит через LLM, поэтому термины делаем кликабельными сами:
    находим известный глоссарий (:mod:`mandala.services.term_linkify`), выносим 2–5
    ключевых терминов НАДЁЖНЫМИ inline-callback кнопками ``mdl:nav:<id>`` и кладём
    ``nav_map`` (id → запрос-объяснение) в ``agent_card``. Клик по кнопке резолвит
    :func:`resolve_nav_action` → обычный ход LLM. Термины НЕ рендерятся ссылками в тексте
    (инлайн-текст в Telegram кликать нельзя; deep-link не доходит до returning-users).
    """
    msg = render_destiny_matrix_message(dm)
    term_buttons, nav_map = numerology_term_buttons(msg.text or "")
    if not term_buttons:
        return msg
    profiles.merge_agent_card(user_id, {"nav_map": nav_map})
    # Кнопки-термины идут НАД штатной навигацией рендера.
    buttons = term_buttons + (msg.buttons or [])
    return msg.model_copy(update={"buttons": buttons})


def _instant_matrix(conn: Connection, user_id: UUID) -> list[OutboundMessage]:
    """``/matrix``: мгновенный рендер сохранённой Карты судьбы (при отсутствии — пересчёт)."""
    profiles = ProfileRepository(conn)
    fresh = profiles.get_by_user_id(user_id)
    ac = dict(fresh.agent_card) if fresh else {}
    dm = ac.get(AGENT_CARD_DESTINY_MATRIX_DATA)
    if isinstance(dm, dict) and dm:
        return [_matrix_message_with_clickable_terms(profiles, user_id, dm)]

    if str(ac.get("birth_date") or "").strip():
        _try_compute_and_save_matrix(user_id=user_id, agent_card=ac, profiles=profiles)
        fresh2 = profiles.get_by_user_id(user_id)
        dm2 = (dict(fresh2.agent_card) if fresh2 else {}).get(AGENT_CARD_DESTINY_MATRIX_DATA)
        if isinstance(dm2, dict) and dm2:
            return [_matrix_message_with_clickable_terms(profiles, user_id, dm2)]

    return [
        OutboundMessage(
            text="Чтобы построить Карту судьбы, укажите дату рождения в анкете.",
            buttons=[[_btn("📝 Заполнить анкету", CB_RESTART)]],
        )
    ]


def _instant_numerology(conn: Connection, user_id: UUID) -> list[OutboundMessage]:
    """``/numerology``: мгновенный рендер сохранённой нумерологии (при отсутствии — пересчёт)."""
    profiles = ProfileRepository(conn)
    fresh = profiles.get_by_user_id(user_id)
    ac = dict(fresh.agent_card) if fresh else {}
    data = ac.get(AGENT_CARD_NUMEROLOGY_DATA)
    if isinstance(data, dict) and data:
        return [render_numerology_message(data)]

    if str(ac.get("birth_date") or "").strip():
        _try_compute_and_save_numerology(user_id=user_id, agent_card=ac, profiles=profiles)
        fresh2 = profiles.get_by_user_id(user_id)
        data2 = (dict(fresh2.agent_card) if fresh2 else {}).get(AGENT_CARD_NUMEROLOGY_DATA)
        if isinstance(data2, dict) and data2:
            return [render_numerology_message(data2)]

    return [
        OutboundMessage(
            text="Чтобы рассчитать нумерологию, укажите имя и дату рождения в анкете.",
            buttons=[[_btn("📝 Заполнить анкету", CB_RESTART)]],
        )
    ]


def _try_calculate_and_save_natal_chart(
    *,
    conn: Connection,
    user_id: UUID,
    agent_card: dict[str, Any],
    profiles: ProfileRepository,
) -> str | None:
    """Рассчитать натальную карту математически и сохранить в ``agent_card``.

    Возвращает ``None`` при успехе или при некритичных ошибках; строку-сообщение для
    пользователя, если геокодинг города/пояса не удался.
    """
    birth_date = str(agent_card.get("birth_date") or "").strip()
    birth_time = str(agent_card.get("birth_time") or "unknown").strip()
    birth_place = str(agent_card.get("birth_place") or "").strip()
    if not birth_date or not birth_place:
        return None
    system = str(agent_card.get(AGENT_CARD_ASTRO_SYSTEM) or "western")
    try:
        from mandala.astro.natal_chart import calculate_natal_chart

        chart_data = calculate_natal_chart(
            birth_date=birth_date,
            birth_time=birth_time,
            birth_place=birth_place,
            system=system,
        )
        # Пересчёт карты инвалидирует кэш колеса (правка профиля/смена системы): сбрасываем
        # file_id в "" — следующий /natal перерисует PNG и закеширует новый file_id.
        profiles.merge_agent_card(
            user_id,
            {AGENT_CARD_NATAL_CHART_DATA: chart_data, AGENT_CARD_NATAL_WHEEL_FILE_ID: ""},
        )
        logger.info("natal chart calculated system=%s user_id=%s", system, user_id)
        return None
    except ValueError as exc:
        err_msg = str(exc)
        if "City not found" in err_msg or "Geocoding failed" in err_msg:
            logger.warning(
                "natal chart geocoding failed place=%r user_id=%s: %s",
                birth_place,
                user_id,
                err_msg,
            )
            return (
                f"⚠️ Не удалось найти город «{birth_place}» для расчёта натальной карты. "
                "Откройте профиль и уточните место рождения."
            )
        if "Timezone" in err_msg:
            logger.warning(
                "natal chart timezone lookup failed place=%r user_id=%s: %s",
                birth_place,
                user_id,
                err_msg,
            )
            return (
                f"⚠️ Не удалось определить часовой пояс места «{birth_place}» — "
                "без него нельзя точно рассчитать карту по местному времени рождения. "
                "Откройте профиль и уточните место рождения."
            )
        logger.warning("natal chart calculation failed user_id=%s", user_id, exc_info=True)
        return None
    except Exception:
        logger.warning("natal chart calculation failed user_id=%s", user_id, exc_info=True)
        return None


def _first_step_intro(vertical_id: str) -> str:
    v = vertical_id.strip()
    if v == "astrology":
        return "Здравствуйте! Для персонализации ответов сначала короткая анкета. "
    if v == "therapy":
        return "Здравствуйте! Перед разговором задам пару вводных вопросов. "
    return ""


def _vertical_greeting(vertical_id: str) -> str:
    """Полное приветствие: что это за бот и что он умеет.

    Используется только в ответ на служебные команды (``/start``, ``/help`` и т.д.).
    """
    v = vertical_id.strip()
    if v == "astrology":
        return (
            "Здравствуйте! Это Mandala — ассистент по астрологии.\n"
            "Я помогу с разбором натальной карты и отвечу на вопросы по астрологии "
            "(прогнозы, совместимость, транзиты).\n"
            "Сначала задам пару коротких вопросов о месте и времени рождения, "
            "затем перейдём к свободному диалогу.\n"
            f"{_COMMANDS_HELP}"
        )
    if v == "therapy":
        return (
            "Здравствуйте! Это Mandala — собеседник в формате поддерживающей беседы.\n"
            "Я не врач и не заменяю психотерапию, но помогу разложить мысли и посмотреть "
            "на ситуацию со стороны.\n"
            "Сначала пара вводных вопросов, затем перейдём к разговору.\n"
            f"{_COMMANDS_HELP}"
        )
    return (
        "Здравствуйте! Сначала задам пару вводных вопросов, затем перейдём к диалогу.\n"
        f"{_COMMANDS_HELP}"
    )


_COMMANDS_HELP = (
    "Меню бота (кнопка «/» или ☰ рядом с полем ввода):\n"
    "• /natal — натальная карта\n"
    "• /matrix — Карта судьбы\n"
    "• /numerology — нумерология\n"
    "• /forecast — прогноз на сегодня, неделю, месяц или год\n"
    "• /morning — утренний прогноз-девиз (вкл/выкл, время МСК)\n"
    "• /profile — ваши данные рождения\n"
    "• /start — меню и приветствие (для нового пользователя — анкета)\n"
    "• /reset — полный сброс: удаляет анкету и всю историю сообщений\n"
    "• /promo — активировать промо-код\n"
    "• /topup — купить сообщения (пополнить баланс)\n"
    "• /help — это сообщение\n"
    "Кнопки под ответами — это навигация: углубиться в тему или вернуться назад."
)

_HELP_PHOTO_URL = "https://upload.wikimedia.org/wikipedia/commons/d/d1/Zodiac_woodcut.png"
_ASTROLOGY_HELP_TEXT = (
    "🌟 **Mandala** — личный астрологический ассистент.\n\n"
    "Что я умею — **4 возможности**:\n"
    "👤 **Профиль** — ваши данные рождения (имя, дата, время, место, система). Открыть: /profile\n"
    "🪐 **Натальная карта** — расчёт и разбор вашей карты. Открыть: /natal\n"
    "🌌 **Карта судьбы** — Матрица Судьбы: 22 аркана по дате рождения. Открыть: /matrix\n"
    "🔢 **Нумерология** — числа по имени и дате рождения. Открыть: /numerology\n\n"
    "Плюс 📊 **Прогноз** — на сегодня, неделю, месяц или год (кнопка «Прогноз» под ответами).\n\n"
    "Я веду вас как навигатор: под каждым ответом — инлайн-кнопки, чтобы углубиться в тему "
    "или вернуться назад. Термины кликабельны кнопками — нажмите, и я объясню.\n\n"
    "**Все команды** (кнопка «/» или ☰ рядом с полем ввода):\n"
    "• /natal — рассчитать и разобрать натальную карту\n"
    "• /matrix — Карта судьбы (Матрица) по дате рождения\n"
    "• /numerology — нумерология по имени и дате\n"
    "• /forecast — прогноз на сегодня, неделю, месяц или год\n"
    "• /morning — утренний прогноз-девиз (вкл/выкл, время МСК)\n"
    "• /profile — ваши данные рождения\n"
    "• /start — меню и приветствие (для нового пользователя — анкета)\n"
    "• /reset — полный сброс: удаляет анкету и всю историю (промо и баланс сохраняются)\n"
    "• /help — это сообщение\n"
    "• /promo — активировать промо-код\n"
    "• /topup — купить сообщения (пополнить баланс)"
)


def _help_nav_buttons() -> list[list[dict[str, str]]]:
    return [
        [_btn("🪐 Натальная карта", "/natal"), _btn("🌌 Карта судьбы", "/matrix")],
        [_btn("🔢 Нумерология", "/numerology"), _btn("👤 Профиль", "mdl:profile")],
        [_btn("📊 Прогноз", "mdl:forecast_menu")],
    ]


_HELP_PHOTO_CAPTION = "🌟 **Mandala** — личный астрологический ассистент"


def _astrology_help_message() -> list[OutboundMessage]:
    """Ответ на ``/help`` для astrology: картинка + описание меню и команд + инлайн-навигация.

    Текст хелпа длиннее лимита подписи Telegram (1024 символа), поэтому фото уходит
    отдельным сообщением с короткой подписью, а полный текст — обычным ``sendMessage``
    с навигацией на нём (терминальное сообщение). Иначе ``sendPhoto`` падает с
    ``MEDIA_CAPTION_TOO_LONG`` и пользователь не получает ничего.
    """
    return [
        OutboundMessage(text=_HELP_PHOTO_CAPTION, photo=_HELP_PHOTO_URL),
        OutboundMessage(text=_ASTROLOGY_HELP_TEXT, buttons=_help_nav_buttons()),
    ]


def _extract_command(user_text: str) -> str | None:
    """Если текст начинается с известной служебной команды — вернуть её в нижнем регистре."""
    if not user_text or not user_text.startswith("/"):
        return None
    head = user_text.split(maxsplit=1)[0]
    if "@" in head:
        head = head.split("@", 1)[0]
    head = head.lower()
    if head in _ALL_COMMANDS:
        return head
    if head == "/promo":
        return "/promo"
    return None


def _greeting_then_intake(greeting: str, prompt: str) -> list[OutboundMessage]:
    """Приветствие и первый вопрос анкеты — РАЗНЫМИ сообщениями, оба с инлайн-кнопками.

    Вопрос анкеты — начало сбора данных, поэтому его нельзя склеивать с приветствием
    в один пузырь. Каждое сообщение несёт инлайн-навигацию (сквозное требование).
    """
    intake_buttons = input_prompt_buttons(edit_active=False)
    out = [OutboundMessage(text=greeting.rstrip(), buttons=intake_buttons)]
    question = (prompt or "").strip()
    if question:
        out.append(OutboundMessage(text=question, buttons=intake_buttons))
    return out


def _already_completed_menu(
    conn: Connection, user_id: UUID, vertical_id: str, greeting: str
) -> list[OutboundMessage]:
    """Меню «Анкета уже заполнена» — без побочных эффектов.

    Единый источник для завершённых пользователей: и info-команды, и /start
    (мягкий рестарт) при `intake_complete` показывают это меню, НЕ пересоздавая
    анкету и НЕ трогая scenario_state/agent_card.
    """
    fresh = ProfileRepository(conn).get_by_user_id(user_id)
    ac = dict(fresh.agent_card) if fresh else {}
    follow = post_intake_completion_message(vertical_id, ac)
    body = (
        f"{greeting}\n\nАнкета уже заполнена. Выберите действие кнопкой "
        "или напишите запрос текстом."
    ).rstrip()
    return [OutboundMessage(text=body, buttons=follow.buttons)]


def _fresh_intake_state_patch() -> dict[str, Any]:
    """Патч состояния для перезапуска анкеты (мягкий рестарт / hard reset после сброса)."""
    return {
        KEY_INTAKE_STEP_INDEX: 0,
        KEY_INTAKE_COMPLETE: False,
        KEY_INTAKE_PHASE: "",
        KEY_INTAKE_PENDING: "",
        KEY_INTAKE_RETURN_SUMMARY: False,
        KEY_INTAKE_EDIT_ACTIVE: False,
        KEY_INTAKE_DRAFT: {},
        KEY_INTAKE_SCHEMA_VERSION: INTAKE_SCHEMA_VERSION,
    }


def _handle_command(
    *,
    conn: Connection,
    event: InboundEvent,
    user_id: UUID,
    state: dict[str, Any],
    steps: Sequence[IntakeStep],
    command: str,
    intake_complete: bool,
) -> list[OutboundMessage]:
    """UX-обработка служебной команды (навигация гарантируется вызывающим)."""
    greeting = _vertical_greeting(event.vertical_id)

    if command == "/help" and event.vertical_id.strip() == "astrology":
        return _astrology_help_message()

    if command in _NATAL_COMMANDS:
        return _instant_natal(conn, user_id)

    if command in _MATRIX_COMMANDS:
        return _instant_matrix(conn, user_id)

    if command in _NUMEROLOGY_COMMANDS:
        return _instant_numerology(conn, user_id)

    if command in _PROFILE_COMMANDS:
        profiles_repo = ProfileRepository(conn)
        fresh = profiles_repo.get_by_user_id(user_id)
        ac = dict(fresh.agent_card) if fresh else {}
        balance = WalletRepository(conn).get_balance(user_id=user_id, vertical_id=event.vertical_id)
        return [build_profile_message(event.vertical_id, ac, message_balance=balance)]

    if command in _PROMO_COMMANDS:
        raw_text = (event.text or "").strip()
        parts = raw_text.split(maxsplit=1)
        code = parts[1].strip().upper() if len(parts) > 1 else ""
        return _handle_promo_command(
            conn=conn, user_id=user_id, vertical_id=event.vertical_id, code=code
        )

    if command in _TOPUP_COMMANDS:
        return [
            build_packs_picker_with_balance(conn, user_id=user_id, vertical_id=event.vertical_id)
        ]

    if command in _HARD_RESET_COMMANDS:
        profiles = ProfileRepository(conn)
        profiles.reset_session(user_id)
        n_deleted = MessageRepository(conn).delete_for_user_vertical(
            user_id=user_id, vertical_id=event.vertical_id
        )
        logger.info(
            "intake hard reset vertical_id=%s user_id=%s deleted_messages=%d",
            event.vertical_id,
            user_id,
            n_deleted,
        )
        first_prompt = steps[0].prompt if steps else ""
        welcome = f"Готово, я всё забыл — начинаем с чистого листа.\n\n{greeting}"
        return _greeting_then_intake(welcome, first_prompt)

    if command in _SOFT_RESTART_COMMANDS:
        # /start (и /restart) — «старт в начале один раз». Если анкета уже
        # завершена (профиль + наталка/матрица в agent_card), НЕ пересоздаём
        # её и не сбрасываем scenario_state — просто показываем меню. Полный
        # сброс — только /reset (_HARD_RESET_COMMANDS выше).
        if intake_complete:
            return _already_completed_menu(conn, user_id, event.vertical_id, greeting)
        ProfileRepository(conn).merge_scenario_state(user_id, _fresh_intake_state_patch())
        first_prompt = steps[0].prompt if steps else ""
        return _greeting_then_intake(greeting, first_prompt)

    # info-команды: без побочных эффектов
    if intake_complete:
        return _already_completed_menu(conn, user_id, event.vertical_id, greeting)

    raw_idx = state.get(KEY_INTAKE_STEP_INDEX, 0)
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        idx = 0
    if idx < 0 or idx >= len(steps):
        idx = 0
    cur_prompt = steps[idx].prompt if steps else ""
    return _greeting_then_intake(greeting, cur_prompt)
