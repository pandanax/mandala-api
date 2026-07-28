"""Текст ответа LLM → HTML для Telegram ``parse_mode=HTML``.

Также: разбивка длинных текстов на части ≤ ``TELEGRAM_MAX_TEXT_CHARS`` символов,
чтобы не превышать лимит Telegram sendMessage.
"""

from __future__ import annotations

import html
import re

# Telegram sendMessage/caption лимиты (официально 4096/1024).
# Берём с запасом на HTML-теги, которые добавляются при форматировании.
TELEGRAM_MAX_TEXT_CHARS = 3900

# Блоки ```…```: опциональная «языковая» строка после открывающих ```
_FENCE = re.compile(r"```(?:[^\n`]*\n)?(.*?)```", re.DOTALL)


def split_text_for_telegram(text: str, max_chars: int = TELEGRAM_MAX_TEXT_CHARS) -> list[str]:
    """Разбить текст на части ≤ ``max_chars`` символов по абзацам, строкам, словам.

    Гарантирует, что каждая часть не превышает лимит. Не режет внутри слова.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf = ""

    def flush(piece: str) -> str:
        nonlocal buf
        if buf:
            chunks.append(buf)
        return piece

    for para in re.split(r"\n\n+", text):
        candidate = (buf + "\n\n" + para).lstrip("\n") if buf else para
        if len(candidate) <= max_chars:
            buf = candidate
            continue
        # buf не вмещает этот абзац — сбрасываем накопленное
        if buf:
            chunks.append(buf)
            buf = ""
        if len(para) <= max_chars:
            buf = para
            continue
        # Абзац сам по себе слишком длинный — делим по строкам
        for line in para.split("\n"):
            candidate_line = (buf + "\n" + line).lstrip("\n") if buf else line
            if len(candidate_line) <= max_chars:
                buf = candidate_line
                continue
            if buf:
                chunks.append(buf)
                buf = ""
            # Строка длиннее лимита — режем по словам
            while len(line) > max_chars:
                split_at = max_chars
                space = line.rfind(" ", max_chars // 2, max_chars)
                if space > 0:
                    split_at = space
                chunks.append(line[:split_at].rstrip())
                line = line[split_at:].lstrip()
            buf = line

    if buf:
        chunks.append(buf)

    return [c for c in chunks if c.strip()]


def format_llm_text_for_telegram_html(text: str) -> str:
    """Грубое приближение markdown к HTML по правилам Bot API.

    Не полноценный парсер: покрывает типичный вывод LLM (**жирный**, списки, заголовки,
    `` `inline` `` и блоки кода). При сомнении текст экранируется. Кликабельные термины
    инлайн-текстом НЕ делаются (в Telegram это невозможно) — они выносятся отдельными
    inline-callback кнопками под сообщением (см. :mod:`mandala.services.nav_protocol`).
    """
    if not text:
        return text
    chunks: list[str] = []
    pos = 0
    for m in _FENCE.finditer(text):
        if m.start() > pos:
            chunks.append(_md_chunk_to_html(text[pos : m.start()]))
        inner = m.group(1) or ""
        chunks.append("<pre>" + html.escape(inner) + "</pre>")
        pos = m.end()
    if pos < len(text):
        chunks.append(_md_chunk_to_html(text[pos:]))
    return "".join(chunks)


def _md_chunk_to_html(s: str) -> str:
    out: list[str] = []
    last = 0
    for m in re.finditer(r"`([^`\n]+)`", s):
        if m.start() > last:
            out.append(_md_plain_to_html(s[last : m.start()]))
        out.append("<code>" + html.escape(m.group(1)) + "</code>")
        last = m.end()
    tail = s[last:]
    if tail:
        out.append(_md_plain_to_html(tail))
    return "".join(out)


def _md_plain_to_html(s: str) -> str:
    if not s:
        return s
    protected: list[str] = []

    def stash_bold(inner: str) -> str:
        protected.append("<b>" + html.escape(inner) + "</b>")
        return f"\x00{len(protected) - 1}\x00"

    def bullet_line(m: re.Match[str]) -> str:
        indent = m.group(1) or ""
        rest = m.group(2)
        return f"{indent}• {rest}"

    s = re.sub(r"^([ \t]*)[-*]\s+(.+)$", bullet_line, s, flags=re.MULTILINE)

    def heading(m: re.Match[str]) -> str:
        return stash_bold(m.group(1).strip())

    s = re.sub(r"^#{1,6}\s+(.+)$", heading, s, flags=re.MULTILINE)

    def link_repl(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2).strip()
        if not re.match(r"https?://", url, re.I):
            return m.group(0)
        safe_url = html.escape(url, quote=True)
        safe_label = html.escape(label)
        protected.append(f'<a href="{safe_url}">{safe_label}</a>')
        return f"\x00{len(protected) - 1}\x00"

    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, s)

    s = re.sub(r"\*\*((?:.|\n)+?)\*\*", lambda m: stash_bold(m.group(1)), s)
    s = re.sub(r"__(?!_)(.+?)(?<!_)__", lambda m: stash_bold(m.group(1)), s, flags=re.DOTALL)

    body = html.escape(s)
    for i, fragment in enumerate(protected):
        body = body.replace(f"\x00{i}\x00", fragment)
    return body
