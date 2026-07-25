"""Конвертация текста LLM → HTML для Telegram."""

from __future__ import annotations

from mandala.adapters.telegram.text_format import format_llm_text_for_telegram_html


def test_format_plain_escape() -> None:
    assert format_llm_text_for_telegram_html("a") == "a"
    assert format_llm_text_for_telegram_html("x < y & z") == "x &lt; y &amp; z"


def test_format_bold_and_heading() -> None:
    s = format_llm_text_for_telegram_html("## Заголовок\n\n**жирный** текст")
    assert "<b>Заголовок</b>" in s
    assert "<b>жирный</b>" in s
    assert "текст" in s


def test_format_code_fence() -> None:
    s = format_llm_text_for_telegram_html("до\n```\n<a>\n```\nпосле")
    assert "<pre>" in s
    assert "&lt;a&gt;" in s
    assert "до" in s
    assert "после" in s


def test_format_inline_code() -> None:
    s = format_llm_text_for_telegram_html("код `x<y` конец")
    assert "<code>x&lt;y</code>" in s


def test_format_link_https() -> None:
    s = format_llm_text_for_telegram_html("[тут](https://example.com/path?q=1)")
    assert '<a href="https://example.com/path?q=1">' in s
    assert "тут" in s


def test_format_list_bullet() -> None:
    s = format_llm_text_for_telegram_html("- один\n- два")
    assert "• один" in s
    assert "• два" in s


# --- Кликабельные термины (inline t.me deep-links) ------------------------------------


def test_term_link_injects_deeplink_anchor() -> None:
    s = format_llm_text_for_telegram_html(
        "Твоя Луна во Льве заметна.",
        term_links=[{"term": "Луна во Льве", "payload": "mdlnav_t0"}],
        bot_username="mandala_bot",
    )
    assert '<a href="https://t.me/mandala_bot?start=mdlnav_t0">Луна во Льве</a>' in s
    # Остальной текст сохранён.
    assert "Твоя" in s and "заметна" in s


def test_term_link_degrades_without_bot_username() -> None:
    # Без username термин остаётся обычным текстом — безопасная деградация, без падения.
    s = format_llm_text_for_telegram_html(
        "Луна во Льве.",
        term_links=[{"term": "Луна во Льве", "payload": "mdlnav_t0"}],
        bot_username=None,
    )
    assert "<a href=" not in s
    assert "Луна во Льве" in s


def test_term_link_skips_term_absent_from_text() -> None:
    s = format_llm_text_for_telegram_html(
        "Речь про Солнце.",
        term_links=[{"term": "Луна во Льве", "payload": "mdlnav_t0"}],
        bot_username="mandala_bot",
    )
    assert "<a href=" not in s
    assert "Солнце" in s


def test_term_link_case_insensitive_fallback_preserves_original_case() -> None:
    s = format_llm_text_for_telegram_html(
        "Про ЛУНУ во Льве речь.",
        term_links=[{"term": "луну во льве", "payload": "mdlnav_t1"}],
        bot_username="mandala_bot",
    )
    assert '<a href="https://t.me/mandala_bot?start=mdlnav_t1">ЛУНУ во Льве</a>' in s


def test_term_link_coexists_with_markdown_bold() -> None:
    s = format_llm_text_for_telegram_html(
        "**Важно:** Меркурий ретроградный сейчас.",
        term_links=[{"term": "Меркурий ретроградный", "payload": "mdlnav_t0"}],
        bot_username="mandala_bot",
    )
    assert "<b>Важно:</b>" in s
    assert '<a href="https://t.me/mandala_bot?start=mdlnav_t0">Меркурий ретроградный</a>' in s
