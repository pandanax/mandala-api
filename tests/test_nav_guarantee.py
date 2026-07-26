"""Гарантия инлайн-навигации: у каждого ответа бота есть кнопки, reply-клавиатуры нет."""

from __future__ import annotations

from mandala.domain.contracts import OutboundMessage
from mandala.services.nav_guarantee import ensure_nav, fallback_nav_buttons
from mandala.services.telegram_stars import build_premium_invoice_message


def _flat_callbacks(msg: OutboundMessage) -> list[str]:
    return [c.get("callback_data", "") for row in (msg.buttons or []) for c in row]


def test_fallback_buttons_known_verticals_and_unknown() -> None:
    assert fallback_nav_buttons("astrology") is not None
    assert fallback_nav_buttons("therapy") is not None
    assert fallback_nav_buttons("unknown") is None


def test_ensure_nav_adds_fallback_to_buttonless_last_message() -> None:
    out = ensure_nav([OutboundMessage(text="Разбор вашей карты…")], "astrology")
    assert out[-1].buttons is not None
    codes = _flat_callbacks(out[-1])
    # Фолбэк — единственная возвратная кнопка «⬅️ К темам» (→ модель снова даст навигацию),
    # а НЕ статические контентные кнопки Натальная/Прогноз/Сферы/Профиль (они в бургер-меню).
    assert codes == ["mdl:topics"]
    for stale in ("mdl:natal", "mdl:forecast_menu", "mdl:th_personality", "mdl:profile"):
        assert stale not in codes


def test_astrology_fallback_is_single_back_button() -> None:
    from mandala.services.nav_guarantee import fallback_nav_buttons

    rows = fallback_nav_buttons("astrology")
    assert rows is not None
    flat = [c for row in rows for c in row]
    assert len(flat) == 1
    assert flat[0]["callback_data"] == "mdl:topics"


def test_ensure_nav_preserves_llm_nav_buttons() -> None:
    existing = [[{"text": "Дальше", "callback_data": "mdl:nav:n0"}]]
    out = ensure_nav([OutboundMessage(text="t", buttons=existing)], "astrology")
    assert out[-1].buttons == existing


def test_ensure_nav_targets_last_non_invoice_message() -> None:
    # Ответ «лимит исчерпан + счёт»: навигацию крепим к тексту, счёт остаётся терминальным.
    msgs = [OutboundMessage(text="Лимит исчерпан."), build_premium_invoice_message()]
    out = ensure_nav(msgs, "astrology")
    assert out[0].buttons is not None  # текст получил навигацию
    assert out[1].invoice is not None  # счёт не тронут
    assert out[1].buttons is None


def test_ensure_nav_no_reply_keyboard_ever() -> None:
    out = ensure_nav([OutboundMessage(text="hi")], "astrology")
    assert all(m.reply_keyboard is None for m in out)


def test_ensure_nav_noop_for_unknown_vertical_and_empty() -> None:
    plain = [OutboundMessage(text="hi")]
    assert ensure_nav(plain, "unknown")[0].buttons is None
    assert ensure_nav([], "astrology") == []


def test_ensure_nav_skips_message_without_text_or_photo() -> None:
    # Единственное сообщение — счёт (нет текста/фото): навигацию вешать некуда.
    out = ensure_nav([build_premium_invoice_message()], "astrology")
    assert out[0].buttons is None


def test_therapy_fallback_uses_therapy_codes() -> None:
    out = ensure_nav([OutboundMessage(text="Расскажите, что происходит.")], "therapy")
    codes = _flat_callbacks(out[-1])
    assert any(c.startswith("mdl_th:") for c in codes)
