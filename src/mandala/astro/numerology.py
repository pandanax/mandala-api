"""Классическая (пифагорейская) нумерология по ИМЕНИ + дате рождения.

Это ОТДЕЛЬНАЯ возможность внутри вертикали ``astrology`` — как «Карта судьбы»
(:mod:`mandala.astro.destiny_matrix`), но с другим входом: помимо даты она использует
**полное имя** (буквы имени → числа по таблице Пифагора). Никаких эфемерид, времени и
места — только арифметика по буквам имени и цифрам даты.

Действует то же домашнее правило проекта, что и для натальной карты и Матрицы Судьбы:
**числа считает Python, а LLM только ИНТЕРПРЕТИРУЕТ** готовые числа. Значения чисел
(1..9, 11, 22, 33) берутся из базы знаний (RAG), а не из этого модуля — здесь только
арифметика.

Реализованная конструкция (одна документированная стандартная школа)
------------------------------------------------------------------
В открытой литературе у пифагорейской нумерологии нет единого канона в мелких деталях
(метод свода даты, обработка Ё/Ъ/Ь, трактовка Y), поэтому — как и в Матрице Судьбы —
реализована ОДНА явно задокументированная стандартная схема, зафиксированная снапшотом
в ``tests/test_numerology_regression.py``.

* **Свод (редукция).** Число сводится к одной цифре сложением своих цифр; **мастер-числа
  11, 22, 33 НЕ сводятся** (это доминирующая в пифагорейской школе конвенция). Пример:
  38 → 3+8 = 11 (мастер, сохраняется); 39 → 3+9 = 12 → 3.

* **Число жизненного пути** (Life Path) — по методу Х. Декоза (наиболее цитируемый):
  отдельно сводим день, месяц и год (каждый — с сохранением мастер-чисел), затем
  складываем три результата и снова сводим. Существует альтернатива «сложить все цифры
  даты сразу» — при мастер-числах она может расходиться; мы её НЕ используем и фиксируем
  выбор здесь и в тесте.

* **Число выражения / судьбы** (Expression) — сумма числовых значений ВСЕХ букв полного
  имени, сведённая.
* **Число души** (Soul Urge / Heart's Desire) — сумма значений ГЛАСНЫХ имени.
* **Число личности** (Personality) — сумма значений СОГЛАСНЫХ имени.
* **Число дня рождения** (Birthday) — день месяца, сведённый (11/22 сохраняются).
* **Число зрелости** (Maturity) — жизненный путь + выражение, сведённое.

Таблица соответствия букв — кириллица
-------------------------------------
Используется **доминирующая опубликованная русская таблица Пифагора**: 33 буквы алфавита
(включая Ё) раскладываются по 9 столбцам, значение буквы = ``((позиция-1) mod 9) + 1``.
Это даёт классические столбцы: ``1 = А И С Ъ``, ``2 = Б Й Т Ы``, … ``9 = З Р Щ``.

* **Гласные** (для числа души): а, е, ё, и, о, у, ы, э, ю, я. **Й — согласная.**
* **Ъ и Ь** — знаки, не обозначают звук: формально в 33-буквенной таблице им выпадают
  значения (Ъ→1, Ь→3), но по распространённой практике для чисел ИМЕНИ мы их
  **пропускаем (значение 0 во всех числах имени)**. Следствие: число выражения = свод
  (число души + число личности), т.к. каждая значащая буква — либо гласная, либо согласная.
* **Ё** трактуется как отдельная буква (позиция 7 → значение 7), а не как «е».

Если имя введено **латиницей** — стандартная латинская таблица Пифагора
(``A=1…I=9, J=1…R=9, S=1…Z=8``), гласные ``A E I O U`` (Y — согласная). Регистр,
пробелы, дефисы и любые не-буквы игнорируются.
"""

from __future__ import annotations

from typing import Any

# Мастер-числа — не сводятся к одной цифре (сохраняются как самостоятельная энергия).
MASTER_NUMBERS: frozenset[int] = frozenset({11, 22, 33})

# --- Кириллица: 33-буквенный алфавит (включая Ё), значение = ((поз-1) mod 9) + 1 ---
_CYRILLIC_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
CYRILLIC_LETTER_VALUES: dict[str, int] = {
    ch: (i % 9) + 1 for i, ch in enumerate(_CYRILLIC_ALPHABET)
}
# Гласные кириллицы (для «числа души»); й — согласная.
CYRILLIC_VOWELS: frozenset[str] = frozenset("аеёиоуыэюя")
# Знаки без звука — пропускаем во всех числах имени (значение 0).
CYRILLIC_SKIP: frozenset[str] = frozenset("ъь")

# --- Латиница: стандартная таблица Пифагора A=1…I=9, J=1…R=9, S=1…Z=8 ---
LATIN_LETTER_VALUES: dict[str, int] = {chr(ord("a") + i): (i % 9) + 1 for i in range(26)}
LATIN_VOWELS: frozenset[str] = frozenset("aeiou")

# Человекочитаемые подписи чисел (роль, НЕ трактовка — значения дают из базы знаний).
NUMBER_LABELS: dict[str, str] = {
    "life_path": "Число жизненного пути",
    "expression": "Число выражения (судьбы)",
    "soul_urge": "Число души (сердечного желания)",
    "personality": "Число личности",
    "birthday": "Число дня рождения",
    "maturity": "Число зрелости",
}


def _digit_sum(n: int) -> int:
    """Сумма десятичных цифр неотрицательного числа."""
    return sum(int(ch) for ch in str(abs(n)))


def reduce_number(n: int, *, keep_master: bool = True) -> int:
    """Свести число к одной цифре сложением цифр; мастер-числа 11/22/33 сохраняются.

    Пример: ``reduce_number(38) == 11`` (мастер), ``reduce_number(39) == 3``,
    ``reduce_number(22) == 22``. При ``keep_master=False`` сводит до 1..9 всегда.
    """
    value = abs(n)
    while value > 9 and not (keep_master and value in MASTER_NUMBERS):
        value = _digit_sum(value)
    return value


def _letter_value(ch: str) -> int:
    """Числовое значение буквы (0 — если буква без значения: ъ/ь, не-буква)."""
    if ch in CYRILLIC_SKIP:
        return 0
    if ch in CYRILLIC_LETTER_VALUES:
        return CYRILLIC_LETTER_VALUES[ch]
    if ch in LATIN_LETTER_VALUES:
        return LATIN_LETTER_VALUES[ch]
    return 0


def _is_vowel(ch: str) -> bool:
    return ch in CYRILLIC_VOWELS or ch in LATIN_VOWELS


def _classify_alphabet(name: str) -> str:
    """'cyrillic' / 'latin' / 'mixed' / 'none' по значащим буквам имени."""
    cyr = sum(1 for ch in name if ch in CYRILLIC_LETTER_VALUES and ch not in CYRILLIC_SKIP)
    lat = sum(1 for ch in name if ch in LATIN_LETTER_VALUES)
    if cyr and lat:
        return "mixed"
    if cyr:
        return "cyrillic"
    if lat:
        return "latin"
    return "none"


def _parse_birth_date(birth_date: str) -> tuple[int, int, int]:
    """Разобрать 'DD.MM.YYYY' → (day, month, year); строгая валидация диапазонов."""
    try:
        parts = birth_date.strip().split(".")
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
    except Exception as exc:  # noqa: BLE001 — единая понятная ошибка на любой сбой парсинга
        raise ValueError(f"Invalid birth_date '{birth_date}', expected DD.MM.YYYY") from exc
    if not (1 <= day <= 31 and 1 <= month <= 12 and year >= 1):
        raise ValueError(f"birth_date out of range: {birth_date}")
    return day, month, year


def _life_path(day: int, month: int, year: int) -> int:
    """Жизненный путь по методу Декоза: свод дня, месяца, года по отдельности, затем свод суммы."""
    return reduce_number(reduce_number(day) + reduce_number(month) + reduce_number(year))


def _name_numbers(name: str) -> dict[str, int]:
    """Числа выражения/души/личности по буквам имени (0-суммы дают 0 — «нет данных»)."""
    letters = [ch for ch in name.lower() if _letter_value(ch) > 0]
    expression = sum(_letter_value(ch) for ch in letters)
    soul = sum(_letter_value(ch) for ch in letters if _is_vowel(ch))
    personality = sum(_letter_value(ch) for ch in letters if not _is_vowel(ch))
    return {
        "expression": reduce_number(expression) if expression else 0,
        "soul_urge": reduce_number(soul) if soul else 0,
        "personality": reduce_number(personality) if personality else 0,
    }


def compute_numerology(full_name: str, birth_date: str) -> dict[str, Any]:
    """Рассчитать пифагорейскую нумерологию из полного имени и даты 'DD.MM.YYYY'.

    Дата обязательна (иначе ``ValueError``); имя — опционально: без валидных букв
    числа имени (выражение/душа/личность/зрелость) не считаются (``None``) — модуль
    аккуратно деградирует до чисел, доступных от одной даты (жизненный путь, день).

    Возвращает структуру с числами (каждое — ``int`` из ``{1..9, 11, 22, 33}`` или
    ``None``), пригодную для сохранения в ``agent_card`` и рендера/инъекции в промпт.
    """
    day, month, year = _parse_birth_date(birth_date)
    name = (full_name or "").strip()
    alphabet = _classify_alphabet(name)

    numbers: dict[str, int | None] = {
        "life_path": _life_path(day, month, year),
        "birthday": reduce_number(day),
    }

    has_name = alphabet != "none"
    if has_name:
        nn = _name_numbers(name)
        numbers["expression"] = nn["expression"] or None
        numbers["soul_urge"] = nn["soul_urge"] or None
        numbers["personality"] = nn["personality"] or None
        expr = numbers["expression"]
        lp = numbers["life_path"]
        numbers["maturity"] = (
            reduce_number(lp + expr) if isinstance(expr, int) and isinstance(lp, int) else None
        )
    else:
        numbers["expression"] = None
        numbers["soul_urge"] = None
        numbers["personality"] = None
        numbers["maturity"] = None

    return {
        "full_name": name,
        "birth_date": f"{day:02d}.{month:02d}.{year:04d}",
        "system": "pythagorean",
        "alphabet": alphabet,
        "has_name": has_name,
        "numbers": numbers,
    }


def is_master(n: int | None) -> bool:
    """Является ли число мастер-числом (11/22/33)."""
    return isinstance(n, int) and n in MASTER_NUMBERS


def _fmt(n: int | None) -> str:
    """'11 (мастер-число)' / '5' / '—' — число для промпта/рендера."""
    if not isinstance(n, int):
        return "—"
    return f"{n} (мастер-число)" if is_master(n) else str(n)


def numerology_to_system_text(data: dict[str, Any]) -> str:
    """Собрать блок нумерологии для инжекции в system-промпт LLM (ДАННЫЕ для интерпретации).

    Как и блоки натальной карты и Матрицы Судьбы: модель объясняет числа (значения
    берёт из базы знаний), но не пересчитывает и не выдумывает числа.
    """
    numbers = data.get("numbers") or {}
    name = data.get("full_name") or ""
    header_name = f", имя «{name}»" if name else " (имя не указано)"
    lines: list[str] = [
        f"=== РАССЧИТАННАЯ НУМЕРОЛОГИЯ (пифагорейская, по имени и дате "
        f"{data.get('birth_date', '?')}{header_name}) ===",
        "Это ОТДЕЛЬНАЯ система (классическая нумерология: буквы имени и цифры даты → "
        "числа 1..9 и мастер-числа 11/22/33). НЕ эфемериды, НЕ знаки зодиака, НЕ арканы "
        "Матрицы Судьбы. Не смешивай её с натальной картой и Картой судьбы.",
        "",
        f"Число жизненного пути (главный урок и путь жизни): {_fmt(numbers.get('life_path'))}",
        f"Число дня рождения (врождённый дар): {_fmt(numbers.get('birthday'))}",
    ]
    if data.get("has_name"):
        lines.extend(
            [
                f"Число выражения/судьбы (потенциал по всем буквам имени): "
                f"{_fmt(numbers.get('expression'))}",
                f"Число души (сердечное желание, по гласным имени): "
                f"{_fmt(numbers.get('soul_urge'))}",
                f"Число личности (внешнее впечатление, по согласным имени): "
                f"{_fmt(numbers.get('personality'))}",
                f"Число зрелости (вектор второй половины жизни, путь+выражение): "
                f"{_fmt(numbers.get('maturity'))}",
            ]
        )
    else:
        lines.append(
            "Числа имени (выражение, душа, личность, зрелость) не рассчитаны: не указано имя. "
            "Мягко предложи указать полное имя для полного разбора."
        )
    lines.append("=== КОНЕЦ НУМЕРОЛОГИИ ===")
    lines.append(
        "Используй ТОЛЬКО эти рассчитанные числа. Значения чисел (характер, сильные "
        "стороны, задачи; отдельный смысл мастер-чисел 11/22/33) бери из базы знаний "
        "нумерологии. Не пересчитывай числа и не выдумывай. Не путай с астрологией и "
        "Матрицей Судьбы: это разные системы."
    )
    return "\n".join(lines)


__all__ = [
    "CYRILLIC_LETTER_VALUES",
    "CYRILLIC_VOWELS",
    "LATIN_LETTER_VALUES",
    "MASTER_NUMBERS",
    "NUMBER_LABELS",
    "compute_numerology",
    "is_master",
    "numerology_to_system_text",
    "reduce_number",
]
