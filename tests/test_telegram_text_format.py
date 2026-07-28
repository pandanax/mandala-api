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
