"""Подменю прогноза, инлайн-навигация, /help (просто текст), setMyCommands, сплиттер."""

from __future__ import annotations

import asyncio

from mandala.adapters.telegram.text_format import TELEGRAM_MAX_TEXT_CHARS, split_text_for_telegram
from mandala.domain.handler import _handle_forecast_menu, _handle_topics_menu
from mandala.services.scenario_intake import _astrology_help_message


def test_topics_menu_returns_rich_inline_button_set() -> None:
    # Клик «⬅️ К темам» → детерминированное БОГАТОЕ меню тем (≥6 кнопок), не проза LLM.
    out = _handle_topics_menu()
    assert len(out) == 1
    msg = out[0]
    assert msg.text is not None and msg.text.strip()
    assert msg.buttons is not None
    codes = [cell["callback_data"] for row in msg.buttons for cell in row]
    # Богатый набор: минимум 6 тем разными действиями.
    assert len(codes) >= 6
    # Реальные возможности приложения представлены.
    assert {"/natal", "/matrix", "/numerology", "mdl:forecast_menu"} <= set(codes)
    # Постоянной нижней клавиатуры нет — только инлайн-кнопки.
    assert msg.reply_keyboard is None


def test_topics_menu_buttons_all_route_to_meaningful_actions() -> None:
    # Каждая кнопка-тема несёт валидный callback: команда, подменю или квик-экшен LLM.
    from mandala.services.scenario_intake import _ALL_COMMANDS
    from mandala.verticals.quick_actions import (
        expand_inbound_quick_action,
        is_forecast_menu,
    )

    out = _handle_topics_menu()
    codes = [cell["callback_data"] for row in (out[0].buttons or []) for cell in row]
    for code in codes:
        if code in _ALL_COMMANDS:
            continue  # мгновенный рендер-команда (/natal, /matrix, /numerology)
        expanded = expand_inbound_quick_action("astrology", code)
        # Либо подменю прогноза, либо развёрнутый запрос-тема к LLM (≠ сырой код).
        assert is_forecast_menu(expanded) or (expanded is not None and expanded != code), code


def test_topics_callback_routes_to_deterministic_menu_not_llm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Клик по «⬅️ К темам» (callback mdl:topics) через handle_inbound → меню кнопок, НЕ LLM.
    from unittest.mock import MagicMock
    from uuid import uuid4

    import mandala.domain.handler as handler_mod
    from mandala.domain.contracts import InboundEvent

    class _Profiles:
        def __init__(self, _conn: object) -> None:
            pass

        def ensure_row(self, **_kw: object) -> None:
            return None

        def get_by_user_id(self, _uid: object) -> object:
            prof = MagicMock()
            prof.agent_card = {}
            # Завершённая анкета → intake не перехватывает, доходим до роутинга тем.
            prof.scenario_state = {"intake_complete": True}
            return prof

    class _Identity:
        def __init__(self, _conn: object) -> None:
            pass

        def get_or_create_user(self, **_kw: object) -> object:
            return uuid4()

    monkeypatch.setattr(handler_mod, "ProfileRepository", _Profiles)
    monkeypatch.setattr(handler_mod, "UserIdentityService", _Identity)
    # Если бы дошло до LLM — тест упал бы: LLM не замокан. Меню должно прийти раньше.
    monkeypatch.setattr(
        handler_mod,
        "handle_inbound_text_llm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ушло в LLM вместо меню тем")),
    )

    ev = InboundEvent(
        vertical_id="astrology", channel="telegram", external_user_id="1", text="mdl:topics"
    )
    out = handler_mod.handle_inbound(ev, MagicMock())

    assert len(out) == 1
    codes = [cell["callback_data"] for row in (out[0].buttons or []) for cell in row]
    assert len(codes) >= 6
    assert {"/natal", "/matrix", "/numerology", "mdl:forecast_menu"} <= set(codes)


def test_morning_callback_routes_to_settings_not_llm(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Кнопка «⚙️ Настроить» (mdl:morning) через handle_inbound → настройка рассылки, НЕ LLM.
    from unittest.mock import MagicMock
    from uuid import uuid4

    import mandala.domain.handler as handler_mod
    import mandala.services.daily_forecast_settings as dfs_mod
    from mandala.domain.contracts import InboundEvent

    store: dict[object, dict[str, object]] = {}

    class _Profiles:
        def __init__(self, _conn: object) -> None:
            pass

        def ensure_row(self, **_kw: object) -> None:
            return None

        def get_by_user_id(self, uid: object) -> object:
            prof = MagicMock()
            prof.agent_card = dict(store.get(uid, {}))
            prof.scenario_state = {"intake_complete": True}
            return prof

        def merge_agent_card(self, uid: object, patch: dict[str, object]) -> None:
            store.setdefault(uid, {}).update(patch)

    class _Identity:
        _uid = uuid4()

        def __init__(self, _conn: object) -> None:
            pass

        def get_or_create_user(self, **_kw: object) -> object:
            return _Identity._uid

    monkeypatch.setattr(handler_mod, "ProfileRepository", _Profiles)
    monkeypatch.setattr(handler_mod, "UserIdentityService", _Identity)
    monkeypatch.setattr(dfs_mod, "ProfileRepository", _Profiles)
    monkeypatch.setattr(
        handler_mod,
        "handle_inbound_text_llm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ушло в LLM вместо настройки")),
    )

    ev = InboundEvent(
        vertical_id="astrology", channel="telegram", external_user_id="1", text="mdl:morning:off"
    )
    out = handler_mod.handle_inbound(ev, MagicMock())

    assert len(out) == 1
    assert out[0].buttons, "у настройки должны быть кнопки"
    # Настройка применена детерминированно (без LLM).
    assert store[_Identity._uid]["daily_forecast_enabled"] is False


def test_forecast_menu_returns_four_period_inline_buttons() -> None:
    out = _handle_forecast_menu()
    assert len(out) == 1
    msg = out[0]
    assert msg.buttons is not None
    codes = [cell["callback_data"] for row in msg.buttons for cell in row]
    assert codes == ["mdl:fc_today", "mdl:fc_week", "mdl:fc_month", "mdl:fc_year"]
    # Постоянной нижней клавиатуры больше нет — навигация только инлайн-кнопками.
    assert msg.reply_keyboard is None


def test_help_message_is_plain_text_bold_menu_and_inline_nav() -> None:
    out = _astrology_help_message()
    # Хелп — это просто текст: ОДНО текстовое сообщение с навигацией, без картинки
    # и без отдельного сообщения. Текст (~2.1k) укладывается в лимит sendMessage (4096).
    assert len(out) == 1
    msg = out[0]
    # Никакого фото ни на одном сообщении.
    assert msg.photo is None
    assert msg.text is not None
    text = msg.text
    # Жирный оформлен markdown-ом **…**, который delivery-слой превратит в <b> HTML.
    assert "**Mandala**" in text
    # Хелп ясно доносит 4 возможности: профиль, наталка, Матрица, нумерология.
    assert "**Профиль**" in text
    assert "**Натальная карта**" in text
    assert "**Карта судьбы**" in text
    assert "**Нумерология**" in text
    assert "**Все команды**" in text
    # Перечислены ВСЕ 10 команд бота, КАЖДАЯ — отдельным пунктом списка (буллет),
    # одна команда = один буллет (никаких склеек «/natal — …, /matrix — …»).
    all_commands = (
        "/natal",
        "/matrix",
        "/numerology",
        "/forecast",
        "/profile",
        "/start",
        "/reset",
        "/help",
        "/promo",
        "/topup",
    )
    lines = text.splitlines()
    for cmd in all_commands:
        assert cmd in text, cmd
        # ровно один пункт-буллет вида «• /cmd — …» на команду
        bullet_lines = [ln for ln in lines if ln.lstrip().startswith(f"• {cmd} ")]
        assert len(bullet_lines) == 1, (cmd, bullet_lines)
    # /topup описан как покупка сообщений, не «тарифы».
    assert "купить сообщения" in text.lower()
    assert "тариф" not in text.lower()
    # Никакого _..._ italic — Telegram-рендер его не поддерживает (потекут подчёркивания).
    assert "_" not in text
    # Постоянной нижней клавиатуры нет; вместо неё — инлайн-навигация под сообщением.
    assert msg.reply_keyboard is None
    assert msg.buttons is not None and len(msg.buttons) > 0


def test_split_text_short_returns_single_chunk() -> None:
    text = "Короткий текст"
    assert split_text_for_telegram(text) == [text]


def test_split_text_long_paragraph_breaks_at_boundary() -> None:
    # Два абзаца, суммарно > лимита — должны стать двумя кусками.
    para_a = "А" * (TELEGRAM_MAX_TEXT_CHARS - 10)
    para_b = "Б" * 50
    text = para_a + "\n\n" + para_b
    parts = split_text_for_telegram(text)
    assert len(parts) == 2
    assert parts[0] == para_a
    assert parts[1] == para_b


def test_split_text_each_part_within_limit() -> None:
    # Случайный длинный текст — гарантируем, что ни один кусок не превышает лимит.
    long = "\n\n".join(["Слово " * 300 for _ in range(5)])
    parts = split_text_for_telegram(long)
    assert all(len(p) <= TELEGRAM_MAX_TEXT_CHARS for p in parts)
    # Контент не теряется: суммарная длина совпадает с исходной (с поправкой на trim).
    assert sum(len(p) for p in parts) <= len(long)
    assert len(parts) > 1


def test_register_bot_commands_noop_without_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_VERTICAL_ID", raising=False)
    from mandala.adapters.telegram.bot_commands import register_bot_commands_if_configured

    assert asyncio.run(register_bot_commands_if_configured()) is False


def test_burger_menu_contains_profile_reset_help() -> None:
    # Профиль/рестарт/help — в бургер-меню (setMyCommands), не в основном потоке кнопок.
    from mandala.adapters.telegram.bot_commands import BOT_COMMANDS

    names = [cmd for cmd, _ in BOT_COMMANDS]
    assert "profile" in names
    assert "reset" in names
    assert "help" in names


def test_burger_menu_contains_natal_and_forecast() -> None:
    # «Натальная карта» и «Прогноз» переехали из inline-кнопок в бургер-меню.
    from mandala.adapters.telegram.bot_commands import BOT_COMMANDS

    names = [cmd for cmd, _ in BOT_COMMANDS]
    assert "natal" in names
    assert "forecast" in names


def test_burger_command_recognizes_forecast_only() -> None:
    from mandala.domain.handler import _burger_nav_command

    assert _burger_nav_command("astrology", "/forecast") == "forecast"
    assert _burger_nav_command("astrology", "/forecast@MandalaBot") == "forecast"
    # /natal и /matrix — мгновенный рендер из БД в scenario_intake, а НЕ бургер-LLM.
    assert _burger_nav_command("astrology", "/natal") is None
    assert _burger_nav_command("astrology", "/matrix") is None
    # Не бургер-команда / не та вертикаль → None.
    assert _burger_nav_command("astrology", "расскажи про мою луну") is None
    assert _burger_nav_command("therapy", "/forecast") is None


def test_forecast_burger_command_shows_period_menu(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # /forecast из меню → то же подменю периодов, что раньше давала inline-кнопка «Прогноз».
    from unittest.mock import MagicMock
    from uuid import uuid4

    import mandala.domain.handler as handler_mod
    from mandala.domain.contracts import InboundEvent

    class _Profiles:
        def __init__(self, _conn: object) -> None:
            pass

        def ensure_row(self, **_kw: object) -> None:
            return None

        def get_by_user_id(self, _uid: object) -> object:
            prof = MagicMock()
            prof.agent_card = {}
            prof.scenario_state = {}
            return prof

    class _Identity:
        def __init__(self, _conn: object) -> None:
            pass

        def get_or_create_user(self, **_kw: object) -> object:
            return uuid4()

    monkeypatch.setattr(handler_mod, "ProfileRepository", _Profiles)
    monkeypatch.setattr(handler_mod, "UserIdentityService", _Identity)

    ev = InboundEvent(
        vertical_id="astrology", channel="telegram", external_user_id="1", text="/forecast"
    )
    out = handler_mod.handle_inbound(ev, MagicMock())

    assert len(out) == 1
    codes = [cell["callback_data"] for row in (out[0].buttons or []) for cell in row]
    assert codes == ["mdl:fc_today", "mdl:fc_week", "mdl:fc_month", "mdl:fc_year"]


def test_build_profile_message_renders_fields_without_reply_keyboard() -> None:
    from mandala.services.profile_view import build_profile_message

    msg = build_profile_message(
        "astrology",
        {"full_name": "Аня", "birth_date": "1990-01-01", "astro_system": "western"},
    )
    assert msg.text is not None
    assert "Ваш профиль" in msg.text
    assert "Аня" in msg.text
    assert "1990-01-01" in msg.text
    # Постоянной нижней клавиатуры больше нет; инлайн-навигацию крепит ensure_nav.
    assert msg.reply_keyboard is None
    # Тело — только данные анкеты (без прозы про сброс/баланс).
    assert "/reset" not in msg.text
