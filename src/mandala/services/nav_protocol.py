"""Структурированная навигация из ответа LLM: короткое сообщение + кнопки + термины.

Бот ведёт себя как навигатор-робот, а не собеседник: каждый ответ — короткое
сообщение и динамический набор переходов «следующий шаг», который генерирует сама
модель под текущий контекст. Формат вывода модели (в самом конце ответа, после
опционального agent-card блока ``---mandala---``):

    <короткое сообщение пользователю>
    ---mandala-nav---
    {"buttons":[{"label":"🌙 Сон","q":"…"}],
     "terms":[{"term":"…","q":"…"}]}

- ``buttons`` — inline-кнопки навигации «куда дальше». Это те самые контекстные
  предложения модели «что разобрать следующим», вынесенные в кнопки (а НЕ прозой в
  тексте). ``label`` — КОРОТКАЯ метка перехода: иконка + 1–2 слова (напр. «🌙 Сон»),
  ``q`` — запрос от лица пользователя, продолжающий эту ветку (выполняется при нажатии).
  Даже если модель прислала длинную подпись, рендер детерминированно ужимает её до
  «иконка + 1–2 слова» (:func:`_shorten_label`) — пользователь всегда видит короткую
  кнопку. Если модель всё же написала пункты прозой без JSON — :func:`extract_prose_nav`
  вытащит их в кнопки (и тоже ужмёт метки).
- ``terms`` — 2–5 самых базовых/ключевых сущностей ответа (например «Луна во Льве»).
  Они выносятся ИНЛАЙН-КНОПКАМИ под сообщением (label = термин, callback → объяснение
  термина в контексте); остальные термины остаются обычным текстом. Инлайн-ТЕКСТ
  кликабельным в Telegram сделать нельзя (авто-линкуются только слэш-команды), а
  Telegram-deep-link ``t.me/<bot>?start=<payload>`` НЕ доставляет start-payload в уже
  открытый чат (returning users) — iOS на повторном тапе открывает чат без payload,
  Desktop показывает кнопку START, WebK payload не поддерживает вовсе
  (bugs.telegram.org/c/8830, tdesktop#27064), поэтому клик по такой «ссылке-термину»
  оборачивался голым ``/start`` = мягкий рестарт анкеты. Единственный НАДЁЖНЫЙ механизм —
  inline-callback кнопка ``mdl:nav:<id>`` (``callback_query`` доставляется всегда).

Из-за лимита Telegram на ``callback_data`` (≤64 байта) полный текст запроса ``q`` в кнопку
не влезает. Поэтому :func:`assign_ids` присваивает каждому переходу короткий id
(кнопки навигации — ``n0``…, кнопки-термины — ``t0``…) и строит карту ``id -> q``
(``nav_map``), которую вызывающий код сохраняет в ``agent_card``. Кнопки несут только
``mdl:nav:n0`` / ``mdl:nav:t0``; на клике :func:`resolve_nav_action` достаёт из ``nav_map``
полный запрос и запускает обычный ход LLM.

Парсер деградирует безопасно: при отсутствии маркера или битом/пустом JSON возвращает
``(текст, None)`` — сообщение показывается без навигации, ничего не падает.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mandala.verticals.client_knowledge import MANDALA_AGENT_CARD_MARKER

# Маркер блока навигации в конце ответа модели (отдельная строка). Это КАНОНИЧЕСКАЯ
# форма — её мы просим у модели и печатаем сами. Для РАЗБОРА ответа используется
# толерантный :data:`_NAV_MARKER_RE`, т.к. слабая модель иногда оборачивает маркер в
# markdown-выделение (``**---mandala-nav---**``) или добавляет пробелы/лишние дефисы.
NAV_MARKER = "---mandala-nav---"

# Толерантное распознавание маркера: ядро ``mandala-nav`` уникально и не встречается в
# обычном тексте, поэтому вокруг него допускаем markdown-эмфазис (``*``/``_``/`` ` ``/``~``),
# 2+ дефиса с каждой стороны и внутренние пробелы. Так nav-блок доходит до кнопок, даже
# если модель слегка исказила оформление маркера.
_NAV_MARKER_RE = re.compile(r"[*_`~]{0,3}-{2,}\s*mandala-nav\s*-{2,}[*_`~]{0,3}")

# Префикс callback_data для кнопок навигации (Telegram ≤64 байта).
NAV_CALLBACK_PREFIX = "mdl:nav:"

# Префикс start-payload для deep-link кликабельных терминов (Telegram ≤64 симв.).
NAV_DEEPLINK_PREFIX = "mdlnav_"

# Ограничения на размер: защищают от «полотна» и от переполнения лимитов Telegram.
_MAX_BUTTONS = 8
_MAX_TERMS = 8
# Кнопки навигации — это КОРОТКИЕ метки «иконка + 1–2 слова» (решение капитана): не
# «полотно» вроде «Ночное восстановление: что говорит карта о сне», а «🌙 Сон». Даже
# если модель прислала длинную подпись, :func:`_shorten_label` детерминированно ужимает
# её до этого формата (кап по символам + не более 2 слов + сохранённая ведущая иконка).
_MAX_LABEL_CHARS = 24
# Не более 2 слов (помимо ведущей иконки) в метке кнопки навигации.
_MAX_LABEL_WORDS = 2
_MAX_TERM_CHARS = 48
# Кап для полной подписи кнопки-термина «📖 <термин>» — термины и так короткие (2–5
# ключевых сущностей), их НЕ ужимаем как nav-метки; лишь страхуем от переполнения.
_MAX_TERM_LABEL_CHARS = 64
_MAX_QUERY_CHARS = 400
# По одной кнопке навигации в ряду: вертикальный список читается как «куда двигаться
# дальше», а не как тесная сетка (сами подписи короткие — «иконка + 1–2 слова»).
_BUTTONS_PER_ROW = 1

# Кликабельные термины — НАДЁЖНЫМИ inline-callback кнопками (инлайн-текст в Telegram
# кликабельным не сделать, а deep-link не доходит до returning-users; см. модульный
# docstring). Показываем только 2–5 самых базовых/ключевых терминов (решение капитана):
# остальные остаются обычным текстом, чтобы клавиатура не разрасталась. Подписи-термины
# короче nav-кнопок → по две в ряд.
_TERM_BUTTON_PREFIX = "📖 "
_TERM_BUTTONS_PER_ROW = 2
_MAX_TERM_BUTTONS = 5


@dataclass(frozen=True)
class NavOption:
    """Одна кнопка навигации «следующий шаг»."""

    label: str
    query: str


@dataclass(frozen=True)
class NavTerm:
    """Ключевой термин ответа → отдельная inline-callback кнопка «объясни термин»."""

    term: str
    query: str


@dataclass(frozen=True)
class NavSpec:
    """Разобранный блок навигации: кнопки + термины."""

    buttons: tuple[NavOption, ...]
    terms: tuple[NavTerm, ...]


@dataclass(frozen=True)
class NavRender:
    """Готовые к рендеру данные навигации.

    ``nav_map`` — карта ``id -> query`` для сохранения в ``agent_card`` (см. модульный
    docstring). ``buttons`` — ряды inline-клавиатуры: сначала кнопки навигации «куда
    дальше» (``n*``), затем кнопки-термины «объясни термин» (``t*``, до 5 штук).
    """

    nav_map: dict[str, str]
    buttons: list[list[dict[str, str]]]


def _coerce_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


# Ведущая эмодзи-иконка кнопки: один и более emoji/символ (с variation-selector'ами и
# ZWJ-склейками) в самом начале метки. Захватываем её отдельно, чтобы при ужатии подписи
# всегда сохранить иконку (напр. «🔄», «⚡️», «⬅️», «🪐»), а слова считать по остатку.
_LEADING_ICON_RE = re.compile(
    "^(?:["
    "\U0001f000-\U0001faff"  # эмодзи-блоки (🌙 🔄 🪐 💰 …)
    "←-⇿"  # стрелки (⬅ живёт в 2B00, но ← тоже сюда)
    "⌀-➿"  # misc technical + dingbats (⚡ ⌛ ✅ …)
    "⬀-⯿"  # доп. символы и стрелки (⬅ ⬆ ⭐ …)
    "️⃣‍"  # variation selector, keycap, ZWJ (склейки эмодзи)
    "™ℹ〰〽㊗㊙"  # ™ ℹ 〰 〽 ㊗ ㊙
    "]+)"
)
# Хвостовая пунктуация, которую снимаем перед добавлением «…».
_TRAIL_PUNCT = " ,;:.!?—–-·"


def _shorten_label(label: str) -> str:
    """Ужать подпись кнопки навигации до «иконка + 1–2 слова» (детерминированный бэкстоп).

    Даже если модель (или прозаический fallback) прислала длинный заголовок, пользователь
    должен увидеть КОРОТКУЮ метку: ведущая эмодзи-иконка (если есть) сохраняется всегда,
    от остального текста берётся не более :data:`_MAX_LABEL_WORDS` слов и общий кап
    :data:`_MAX_LABEL_CHARS` символов, обрезка — по границе слова (без обрыва посреди
    слова). «…» добавляется ТОЛЬКО если реально что-то отсечено. Пустой/битый вход
    деградирует безопасно (возвращается исходная строка обрезанная по капу).
    """
    s = (label or "").strip()
    if not s:
        return s
    m = _LEADING_ICON_RE.match(s)
    icon = m.group(0).strip() if m else ""
    rest = (s[m.end() :] if m else s).strip()
    words = rest.split()

    prefix = f"{icon} " if icon else ""
    budget = _MAX_LABEL_CHARS - len(prefix)
    if budget < 4:  # необычно длинная «иконка» — не ужимаем под неё, берём общий кап
        prefix = ""
        budget = _MAX_LABEL_CHARS

    if not words:  # метка была только иконкой
        return icon or s[:_MAX_LABEL_CHARS]

    kept: list[str] = []
    for word in words[:_MAX_LABEL_WORDS]:
        candidate = " ".join([*kept, word])
        if kept and len(candidate) > budget:
            break
        kept.append(word)
    truncated = len(kept) < len(words)
    text = " ".join(kept)
    if len(kept) == 1 and len(text) > budget:  # единственное слишком длинное слово
        text = words[0][:budget].rstrip()
        truncated = True

    text = text.rstrip(_TRAIL_PUNCT)
    if truncated and text:
        text = f"{text}…"
    result = f"{prefix}{text}".strip()
    return result or s[:_MAX_LABEL_CHARS]


def _query_of(item: Mapping[str, object]) -> str:
    """Запрос перехода: основной ключ ``q``, запасной — ``query``."""
    return (_coerce_str(item.get("q")) or _coerce_str(item.get("query")))[:_MAX_QUERY_CHARS]


def _strip_code_fences(s: str) -> str:
    """Снять markdown-ограждение ``` ``` ``` ``` (в т.ч. ``` ```json ```) вокруг тела блока."""
    t = s.strip()
    if not t.startswith("```"):
        return t
    first_nl = t.find("\n")
    if first_nl != -1:  # выкинуть строку открывающего ограждения (``` или ```json)
        t = t[first_nl + 1 :]
    t = t.rstrip()
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def _first_json_object(s: str) -> str | None:
    """Первый сбалансированный объект ``{...}`` в строке (с учётом строк-литералов).

    Позволяет игнорировать текст ДО и ПОСЛЕ JSON (слабая модель любит дописать после
    nav-JSON прощальную фразу — «Надеюсь, это поможет!»), не ломая разбор.
    """
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _loads_nav_object(tail: str) -> dict[str, object] | None:
    """Толерантно разобрать JSON-объект nav-блока (``None``, если объект не извлечь).

    Реальные ответы слабой модели (astrology = deepseek-v4-flash) редко бывают идеально
    чистым однострочным JSON: встречаются markdown-ограждение ``` ```json ```, текст после
    закрывающей ``}`` и висячие запятые. Строгий ``json.loads`` по всему хвосту отвергает
    такой блок целиком — и навигация схлопывается в единственную фолбэк-кнопку. Пробуем по
    очереди: строгий разбор → снятие ограждения → первый сбалансированный ``{...}`` →
    удаление висячих запятых. Настоящий мусор (``{не json,,,}``) по-прежнему даёт ``None``.
    """
    candidates: list[str] = [tail]
    stripped = _strip_code_fences(tail)
    if stripped and stripped != tail:
        candidates.append(stripped)
    obj = _first_json_object(stripped)
    if obj is not None:
        candidates.append(obj)
        no_trailing_commas = re.sub(r",(\s*[}\]])", r"\1", obj)
        if no_trailing_commas != obj:
            candidates.append(no_trailing_commas)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _parse_nav_json(tail: str) -> NavSpec | None:
    """Разобрать JSON-тело блока навигации; ``None`` при любой невалидности."""
    if not tail:
        return None
    parsed = _loads_nav_object(tail)
    if not isinstance(parsed, dict):
        return None

    buttons: list[NavOption] = []
    raw_buttons = parsed.get("buttons")
    if isinstance(raw_buttons, list):
        for item in raw_buttons:
            if len(buttons) >= _MAX_BUTTONS:
                break
            if not isinstance(item, Mapping):
                continue
            label = _shorten_label(_coerce_str(item.get("label")))
            query = _query_of(item)
            if label and query:
                buttons.append(NavOption(label=label, query=query))

    terms: list[NavTerm] = []
    raw_terms = parsed.get("terms")
    if isinstance(raw_terms, list):
        for item in raw_terms:
            if len(terms) >= _MAX_TERMS:
                break
            if not isinstance(item, Mapping):
                continue
            term = _coerce_str(item.get("term"))[:_MAX_TERM_CHARS]
            query = _query_of(item)
            if term and query:
                terms.append(NavTerm(term=term, query=query))

    if not buttons and not terms:
        return None
    return NavSpec(buttons=tuple(buttons), terms=tuple(terms))


def split_llm_nav_suffix(reply: str) -> tuple[str, NavSpec | None]:
    """Отделить хвост ``---mandala-nav---`` + JSON от текста для пользователя.

    Возвращает ``(текст_для_чата, NavSpec | None)``. При отсутствии маркера — исходный
    текст и ``None``. При наличии маркера блок всегда срезается из текста (даже если
    JSON битый), чтобы не показать пользователю сырой служебный блок.

    Защита от перепутанного порядка: если после nav-блока по ошибке идёт agent-card
    блок ``---mandala---``, он переносится обратно в head — чтобы его смог обработать
    :func:`mandala.verticals.client_knowledge.split_llm_agent_card_suffix`.
    """
    if not reply or "mandala-nav" not in reply:
        return reply, None
    matches = list(_NAV_MARKER_RE.finditer(reply))
    if not matches:
        return reply, None
    m = matches[-1]  # последний блок — фактический nav-хвост ответа
    head = reply[: m.start()].rstrip()
    tail = reply[m.end() :].strip()

    carry = ""
    if MANDALA_AGENT_CARD_MARKER in tail:
        cut = tail.find(MANDALA_AGENT_CARD_MARKER)
        carry = tail[cut:]
        tail = tail[:cut].strip()

    spec = _parse_nav_json(tail)
    if carry:
        head = f"{head}\n{carry}".strip()
    cleaned = head if head else reply
    return cleaned, spec


# --- Fallback: прозаический список «куда дальше» → кнопки навигации --------------------
#
# Модель послабее (astrology = deepseek-v4-flash) иногда игнорирует служебный nav-JSON и
# пишет пункты «что разобрать дальше» буллетами прямо в тексте. Тогда мы вытаскиваем этот
# завершающий блок буллетов в :class:`NavSpec` и убираем строки из видимого текста — чтобы
# переходы жили ТОЛЬКО в кнопках, а не дублировались прозой (главное требование UX).

# Строка-буллет: •, -, –, —, *, ‣, ▪, ·, », →, «1.»/«1)», keycap-эмодзи «1️⃣».
_BULLET_RE = re.compile(r"^\s*(?:[•\-–—*‣▪·»]|→|\d+[.)]|[0-9]️?⃣)\s+(?P<item>.+\S)\s*$")
# Подсказки, что строка НАД блоком — это заголовок «куда дальше».
_NEXT_STEP_CUES = (
    "дальше",
    "куда",
    "продолж",
    "могу рассказать",
    "могу разобрать",
    "что ещё",
    "что еще",
    "хотите",
    "выбер",
    "направлен",
    "по темам",
)
# Подсказки, что пункт — это «назад / к темам».
_BACK_CUES = ("назад", "вернут", "к темам", "к другим", "другим темам", "обратно")
_BACK_EMOJI = "⬅"
# Запрос для кнопки-возврата (общий).
_BACK_QUERY = "Какие ещё темы можно разобрать по моей натальной карте?"


def _core_phrase(item: str) -> str:
    """Убрать ведущие эмодзи/пунктуацию из пункта, оставив смысловое начало заголовка."""
    core = re.sub(r"^[^\w]+", "", item, flags=re.UNICODE).strip()
    return core or item.strip()


def _is_back_item(item: str) -> bool:
    low = item.lower()
    return _BACK_EMOJI in item or any(cue in low for cue in _BACK_CUES)


def _prose_item_to_option(item: str) -> NavOption:
    label = _shorten_label(item.strip())
    if _is_back_item(item):
        query = _BACK_QUERY
    else:
        query = f"Расскажи подробнее: {_core_phrase(item)}"
    return NavOption(label=label, query=query[:_MAX_QUERY_CHARS])


def extract_prose_nav(text: str) -> tuple[str, NavSpec | None]:
    """Fallback-разбор: вынести прозаический список «куда дальше» из текста в кнопки.

    Возвращает ``(текст_без_блока, NavSpec)`` если в конце сообщения найден уверенный блок
    буллетов-переходов; иначе ``(text, None)`` — текст не трогаем. Никогда не вырезает весь
    текст (сообщение не должно стать пустым) и требует ≥2 пунктов.
    """
    if not text or not text.strip():
        return text, None
    lines = text.rstrip().split("\n")

    # Максимальный завершающий блок строк-буллетов (снизу вверх).
    run: list[str] = []
    i = len(lines) - 1
    while i >= 0:
        if lines[i].strip() == "":
            if run:
                break
            i -= 1
            continue
        m = _BULLET_RE.match(lines[i])
        if m is None:
            break
        run.append(m.group("item").strip())
        i -= 1
    run.reverse()
    if len(run) < 2:
        return text, None

    # Строка непосредственно над блоком — возможный заголовок «Куда дальше:».
    heading_idx = i
    heading_is_cue = False
    if heading_idx >= 0:
        h = lines[heading_idx].strip().lower()
        if h and ((h.endswith(":") and len(h) <= 60) or any(cue in h for cue in _NEXT_STEP_CUES)):
            heading_is_cue = True

    body_lines = lines[:heading_idx] if heading_is_cue else lines[: heading_idx + 1]
    body_text = "\n".join(body_lines).rstrip()
    if not body_text.strip():
        # Всё сообщение — сам блок: вырезать нечего показать пользователю → не трогаем.
        return text, None

    has_back = any(_is_back_item(it) for it in run)
    prose_above = any(len(ln.strip()) >= 30 and _BULLET_RE.match(ln) is None for ln in body_lines)
    topic_like = all(not it.rstrip().endswith((".", "!")) for it in run)
    confident = heading_is_cue or has_back or (prose_above and topic_like and 2 <= len(run) <= 5)
    if not confident:
        return text, None

    buttons = tuple(_prose_item_to_option(it) for it in run[:_MAX_BUTTONS])
    return body_text, NavSpec(buttons=buttons, terms=())


def assign_ids(spec: NavSpec) -> NavRender:
    """Присвоить переходам короткие id и собрать ``nav_map`` + ряды inline-кнопок.

    Кнопки навигации «куда дальше» (``n*``) идут первыми, за ними — кнопки-термины
    «объясни термин» (``t*``, до 2–5 штук, см. :func:`build_term_buttons`).
    """
    nav_map: dict[str, str] = {}
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for i, opt in enumerate(spec.buttons):
        nav_id = f"n{i}"
        nav_map[nav_id] = opt.query
        row.append({"text": opt.label, "callback_data": f"{NAV_CALLBACK_PREFIX}{nav_id}"})
        if len(row) >= _BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Термины — надёжными inline-callback кнопками ПОД кнопками навигации (инлайн-текст
    # кликабельным не сделать; deep-link не доходит до returning-users). Кап 2–5 терминов.
    term_rows, term_nav_map = build_term_buttons((t.term, t.query) for t in spec.terms)
    nav_map.update(term_nav_map)
    rows.extend(term_rows)

    return NavRender(nav_map=nav_map, buttons=rows)


def build_term_buttons(
    terms: Iterable[tuple[str, str]],
) -> tuple[list[list[dict[str, str]]], dict[str, str]]:
    """``(термин, запрос-объяснение)`` пары → ряды callback-кнопок + ``nav_map``.

    Каждому термину присваивается id ``t<i>``; кнопка несёт ``mdl:nav:<id>`` и резолвится
    :func:`resolve_nav_action` (``callback_query`` доставляется Telegram всегда). Берём
    только первые :data:`_MAX_TERM_BUTTONS` (2–5) самых базовых/ключевых терминов —
    остальные остаются обычным текстом. Единый билдер для LLM-пути (:func:`assign_ids`) и
    детерминированного ``/matrix`` (:mod:`mandala.services.term_linkify`). Пустые термин/
    запрос молча пропускаются (безопасная деградация).
    """
    nav_map: dict[str, str] = {}
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for term, query in terms:
        if len(nav_map) >= _MAX_TERM_BUTTONS:
            break
        term_s = (term or "").strip()
        query_s = (query or "").strip()
        if not term_s or not query_s:
            continue
        nav_id = f"t{len(nav_map)}"
        nav_map[nav_id] = query_s[:_MAX_QUERY_CHARS]
        label = f"{_TERM_BUTTON_PREFIX}{term_s}"[:_MAX_TERM_LABEL_CHARS]
        row.append({"text": label, "callback_data": f"{NAV_CALLBACK_PREFIX}{nav_id}"})
        if len(row) >= _TERM_BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows, nav_map


def resolve_nav_action(text: str | None, nav_map: Mapping[str, str] | None) -> str | None:
    """Если ``text`` — клик по навигации, вернуть сохранённый запрос из ``nav_map``.

    Распознаёт два источника:
    - callback кнопки навигации: ``mdl:nav:<id>``;
    - deep-link кликабельного термина: ``/start mdlnav_<id>`` (Telegram присылает так
      после клика по ссылке ``t.me/<bot>?start=mdlnav_<id>``).

    Возвращает ``None``, если это не навигация или id отсутствует в карте (устаревшая
    ссылка после сброса) — вызывающий код обрабатывает такой ввод обычным путём.
    """
    if not text or not nav_map:
        return None
    raw = text.strip()

    nav_id: str | None = None
    if raw.startswith(NAV_CALLBACK_PREFIX):
        nav_id = raw[len(NAV_CALLBACK_PREFIX) :].strip()
    elif raw.startswith("/start"):
        parts = raw.split(maxsplit=1)
        if len(parts) == 2:
            payload = parts[1].strip()
            if "@" in payload:  # /start@botname payload — на всякий случай
                payload = payload.split("@", 1)[0]
            if payload.startswith(NAV_DEEPLINK_PREFIX):
                nav_id = payload[len(NAV_DEEPLINK_PREFIX) :].strip()

    if not nav_id:
        return None
    query = nav_map.get(nav_id)
    if isinstance(query, str) and query.strip():
        return query.strip()
    return None
