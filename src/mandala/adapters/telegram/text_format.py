"""Текст ответа LLM → HTML для Telegram ``parse_mode=HTML``.

Также: разбивка длинных текстов на части ≤ ``TELEGRAM_MAX_TEXT_CHARS`` символов,
чтобы не превышать лимит Telegram sendMessage.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

# Telegram sendMessage/caption лимиты (официально 4096/1024).
# Берём с запасом на HTML-теги, которые добавляются при форматировании.
TELEGRAM_MAX_TEXT_CHARS = 3900

# Блоки ```…```: опциональная «языковая» строка после открывающих ```
_FENCE = re.compile(r"```(?:[^\n`]*\n)?(.*?)```", re.DOTALL)

# База deep-link для кликабельных терминов (t.me/<bot>?start=<payload>).
_TME_BASE = "https://t.me"
# Служебный разделитель плейсхолдера ссылки (не встречается в обычном тексте и не
# трогается ни markdown-регэкспами, ни html.escape).
_LINK_SENTINEL = "\x02"


def _term_deeplink_html(bot_username: str, payload: str, label: str) -> str:
    url = f"{_TME_BASE}/{bot_username}?start={payload}"
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>'


def _inject_term_placeholders(
    text: str,
    term_links: Sequence[dict[str, str]],
    bot_username: str,
    out_links: list[str],
) -> str:
    """Заменить каждый термин в тексте на защищённый плейсхолдер ``\\x02i\\x02``.

    Готовый ``<a>``-HTML копится в ``out_links`` и подставляется обратно уже после
    markdown→HTML конвертации. Термин, которого нет в тексте, пропускается (деградация).
    """
    for tl in term_links:
        term = (tl.get("term") or "").strip()
        payload = (tl.get("payload") or "").strip()
        if not term or not payload:
            continue
        idx = text.find(term)
        if idx == -1:
            low = text.lower()
            j = low.find(term.lower())
            if j == -1:
                continue
            idx = j
            term = text[idx : idx + len(term)]  # сохранить исходный регистр подстроки
        out_links.append(_term_deeplink_html(bot_username, payload, term))
        placeholder = f"{_LINK_SENTINEL}{len(out_links) - 1}{_LINK_SENTINEL}"
        text = text[:idx] + placeholder + text[idx + len(term) :]
    return text


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


def format_llm_text_for_telegram_html(
    text: str,
    *,
    term_links: Sequence[dict[str, str]] | None = None,
    bot_username: str | None = None,
) -> str:
    """Грубое приближение markdown к HTML по правилам Bot API.

    Не полноценный парсер: покрывает типичный вывод LLM (**жирный**, списки, заголовки,
    `` `inline` `` и блоки кода). При сомнении текст экранируется.

    ``term_links`` + ``bot_username`` (оба заданы) → термины в тексте становятся
    кликабельными inline deep-link ссылками ``t.me/<bot>?start=<payload>``. Если
    ``bot_username`` не определён — термины остаются обычным текстом (безопасная деградация).
    """
    if not text:
        return text
    link_html: list[str] = []
    if term_links and bot_username:
        text = _inject_term_placeholders(text, term_links, bot_username, link_html)
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
    body = "".join(chunks)
    for i, fragment in enumerate(link_html):
        body = body.replace(f"{_LINK_SENTINEL}{i}{_LINK_SENTINEL}", fragment)
    return body


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
