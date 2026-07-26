"""Математический расчёт «Карты судьбы» = Матрицы Судьбы (система Наталии Ладини).

Это ОТДЕЛЬНАЯ от астрологии система: она НЕ использует эфемериды/Swiss Ephemeris и
не зависит от времени и места рождения. Вся карта выводится из ЧИСЕЛ даты рождения
(день/месяц/год) арифметикой по 22 Старшим Арканам Таро («22 кода судьбы»).

Как и в натальной карте (см. :mod:`mandala.astro.natal_chart`), действует домашнее
правило: числа считает Python, а LLM только ИНТЕРПРЕТИРУЕТ готовые арканы — модель
никогда не считает и не выдумывает позиции. Значения арканов берутся из базы знаний
(RAG), а не из этого модуля — здесь только арифметика.

Правило приведения (сведение к аркану 1..22)
--------------------------------------------
Любое число > 22 сводится **сложением своих цифр** (26 → 2+6 = 8), повторно, пока не
попадёт в диапазон 1..22; число 22 сохраняется как есть (не сводится к 4). Это
доминирующая в практике конвенция: её используют примеры на официальном сайте метода
(matricaladini.ru: 36 → 3+6 = 9) и большинство массовых онлайн-калькуляторов
(lady.mail.ru, calculatorov.ru, letu.ru, destiny-matrix.cc и др.). Существует вариант
«вычитать 22» (26 → 4) у части «классических» школ — он даёт другие производные точки;
мы его НЕ используем и фиксируем выбор здесь и в регресс-тесте.

Точность (регресс-тест ``tests/test_destiny_matrix_regression.py``)
Ядро октаграммы (день/месяц/год/карма/центр + углы родового квадрата) воспроизводит
двух независимых референсных калькуляторов точь-в-точь:

* 07.01.1987 → день 7, месяц 1, год 7, карма 15, центр 3 (destiny-matrix.cc);
* 29.01.1991 → день 11, месяц 1, год 20, карма 5, центр 10 (letu.ru).

Производные линии (предназначение/деньги/отношения/чакры/родовые) в открытой
литературе не имеют единого канона (источники это прямо признают и монетизируют точную
арифметику), поэтому реализована ОДНА явно задокументированная стандартная схема
построения — см. формулы ниже. Она детерминирована и зафиксирована снапшотом в тесте.
"""

from __future__ import annotations

from typing import Any

# Названия 22 Старших Арканов в конвенции Матрицы Судьбы (Марсельская нумерация:
# 8 — Справедливость, 11 — Сила; 22 — Шут). Значения арканов — в базе знаний (RAG).
ARCANA_NAMES: dict[int, str] = {
    1: "Маг",
    2: "Верховная Жрица",
    3: "Императрица",
    4: "Император",
    5: "Иерофант",
    6: "Влюблённые",
    7: "Колесница",
    8: "Справедливость",
    9: "Отшельник",
    10: "Колесо Фортуны",
    11: "Сила",
    12: "Повешенный",
    13: "Смерть",
    14: "Умеренность",
    15: "Дьявол",
    16: "Башня",
    17: "Звезда",
    18: "Луна",
    19: "Солнце",
    20: "Суд",
    21: "Мир",
    22: "Шут",
}

# 7 чакр снизу вверх (анатомический порядок) и сверху вниз (как в карте здоровья).
CHAKRAS_TOP_DOWN: tuple[str, ...] = (
    "Сахасрара",
    "Аджна",
    "Вишудха",
    "Анахата",
    "Манипура",
    "Свадхистана",
    "Муладхара",
)


def _digit_sum(n: int) -> int:
    """Сумма десятичных цифр неотрицательного числа."""
    return sum(int(ch) for ch in str(abs(n)))


def reduce_to_arcana(n: int) -> int:
    """Свести число к аркану 1..22 сложением цифр (22 сохраняется).

    Пример: 26 → 8, 30 → 3, 37 → 10, 44 → 8. Числа 1..22 возвращаются без изменений.
    """
    value = n
    while value > 22:
        value = _digit_sum(value)
    return 22 if value == 0 else value


def _arc(n: int) -> dict[str, Any]:
    """Аркан как ``{"n": число, "name": имя}`` (для читаемого JSON и промпта)."""
    return {"n": n, "name": ARCANA_NAMES.get(n, "?")}


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


def compute_destiny_matrix(birth_date: str) -> dict[str, Any]:
    """Рассчитать Матрицу Судьбы из даты рождения 'DD.MM.YYYY'.

    Возвращает структуру со всеми аркана-позициями (каждая — ``{"n", "name"}``):

    Личностный (диагональный) квадрат — портрет и личные задачи:
        ``day`` (левый), ``month`` (верх), ``year`` (право), ``karma`` (низ),
        ``comfort_zone`` (центр = зона комфорта / главная энергия).
    Родовой (прямой) квадрат — программы рода: ``ancestral_square`` (4 угла =
        суммы соседних углов диагонали).
    Диагонали личностного квадрата: ``sky`` (Небо = месяц+карма, вертикаль,
        духовное) и ``earth`` (Земля = день+год, горизонталь, материальное).
    Предназначение: ``purpose`` = личное/социальное/духовное/итог.
    Родовые линии: ``ancestral`` = мужская/женская линии и их итоги.
    Каналы: ``money_line`` и ``relationship_line`` (по 3 энергии).
    Карта здоровья: ``chakras`` (7 чакр × физика/энергия/эмоции) + ``chakra_totals``.
    """
    day, month, year = _parse_birth_date(birth_date)

    # --- Ядро октаграммы (проверено по референсным калькуляторам) ---
    a = reduce_to_arcana(day)  # левый угол — день рождения
    b = reduce_to_arcana(month)  # верхний угол — месяц
    c = reduce_to_arcana(_digit_sum(year))  # правый угол — сумма цифр года
    k = reduce_to_arcana(a + b + c)  # нижний угол — кармическая задача
    center = reduce_to_arcana(a + b + c + k)  # центр — зона комфорта / главная энергия

    # --- Родовой (прямой) квадрат: углы = суммы соседних углов диагонали ---
    nw = reduce_to_arcana(a + b)  # верх-лево (день+месяц)
    ne = reduce_to_arcana(b + c)  # верх-право (месяц+год)
    se = reduce_to_arcana(c + k)  # низ-право (год+карма)
    sw = reduce_to_arcana(k + a)  # низ-лево (карма+день)

    # --- Диагонали личностного квадрата (личные линии) ---
    sky = reduce_to_arcana(b + k)  # Небо: вертикаль (месяц+карма) — духовное «для себя»
    earth = reduce_to_arcana(a + c)  # Земля: горизонталь (день+год) — материальное

    # --- Родовые линии (диагонали прямого квадрата через центр) ---
    male_total = reduce_to_arcana(nw + se)  # отцовская: верх-лево ↔ низ-право
    female_total = reduce_to_arcana(ne + sw)  # материнская: верх-право ↔ низ-лево

    # --- Предназначение ---
    personal_purpose = reduce_to_arcana(sky + earth)  # личное (проработка до ~40 лет)
    social_purpose = reduce_to_arcana(male_total + female_total)  # социальное (род, ~40–60)
    spiritual_purpose = reduce_to_arcana(personal_purpose + social_purpose)  # духовное (60+)

    # --- Денежный канал (материальный низ: угол год+карма → центр) ---
    money_flow = reduce_to_arcana(se + center)
    money_total = reduce_to_arcana(se + center + k)
    # --- Канал отношений (точка входа у кармического хвоста: угол карма+день → центр) ---
    love_flow = reduce_to_arcana(sw + center)
    love_total = reduce_to_arcana(sw + center + a)

    return {
        "birth_date": f"{day:02d}.{month:02d}.{year:04d}",
        "day": _arc(a),
        "month": _arc(b),
        "year": _arc(c),
        "karma": _arc(k),
        "comfort_zone": _arc(center),
        "ancestral_square": {
            "day_month": _arc(nw),
            "month_year": _arc(ne),
            "year_karma": _arc(se),
            "karma_day": _arc(sw),
        },
        "diagonals": {"sky": _arc(sky), "earth": _arc(earth)},
        "purpose": {
            "sky": _arc(sky),
            "earth": _arc(earth),
            "personal": _arc(personal_purpose),
            "social": _arc(social_purpose),
            "spiritual": _arc(spiritual_purpose),
            "summary": _arc(spiritual_purpose),
        },
        "ancestral": {
            "male_line": [_arc(nw), _arc(se)],
            "male_total": _arc(male_total),
            "female_line": [_arc(ne), _arc(sw)],
            "female_total": _arc(female_total),
        },
        "money_line": {
            "entry": _arc(se),
            "flow": _arc(money_flow),
            "core": _arc(center),
            "total": _arc(money_total),
        },
        "relationship_line": {
            "entry": _arc(sw),
            "flow": _arc(love_flow),
            "core": _arc(center),
            "total": _arc(love_total),
        },
        "chakras": _compute_chakras(a, b, c, k, center),
        "chakra_totals": _chakra_totals(_compute_chakras(a, b, c, k, center)),
    }


def _compute_chakras(a: int, b: int, c: int, k: int, center: int) -> list[dict[str, Any]]:
    """Карта здоровья: 7 чакр × (физика, энергия, эмоции).

    Задокументированная стандартная схема (единого канона в открытой литературе нет):
    вертикальная (духовная) ось месяц↔карма даёт столбец «Энергия», горизонтальная
    (материальная) ось год↔день — столбец «Физика», сердце (Анахата) = центр/зона
    комфорта на обеих осях; «Эмоции» = приведённая сумма (физика+энергия) строки.
    Чакры сверху вниз: Сахасрара → Муладхара.
    """
    # Столбец «Энергия» (духовная вертикаль): полюса месяц (верх) и карма (низ).
    e_saha = b
    e_anah = center
    e_mula = k
    e_ajna = reduce_to_arcana(e_saha + e_anah)
    e_vish = reduce_to_arcana(e_ajna + e_anah)
    e_mani = reduce_to_arcana(e_anah + e_mula)
    e_svad = reduce_to_arcana(e_mani + e_mula)
    energy = [e_saha, e_ajna, e_vish, e_anah, e_mani, e_svad, e_mula]

    # Столбец «Физика» (материальная горизонталь): полюса год (право) и день (лево).
    p_saha = c
    p_anah = center
    p_mula = a
    p_ajna = reduce_to_arcana(p_saha + p_anah)
    p_vish = reduce_to_arcana(p_ajna + p_anah)
    p_mani = reduce_to_arcana(p_anah + p_mula)
    p_svad = reduce_to_arcana(p_mani + p_mula)
    physics = [p_saha, p_ajna, p_vish, p_anah, p_mani, p_svad, p_mula]

    rows: list[dict[str, Any]] = []
    for name, phys, ener in zip(CHAKRAS_TOP_DOWN, physics, energy, strict=True):
        rows.append(
            {
                "chakra": name,
                "physics": _arc(phys),
                "energy": _arc(ener),
                "emotions": _arc(reduce_to_arcana(phys + ener)),
            }
        )
    return rows


def _chakra_totals(chakras: list[dict[str, Any]]) -> dict[str, Any]:
    """Нижняя ячейка карты здоровья: «общий знаменатель» каждого столбца."""
    phys = reduce_to_arcana(sum(row["physics"]["n"] for row in chakras))
    ener = reduce_to_arcana(sum(row["energy"]["n"] for row in chakras))
    emo = reduce_to_arcana(sum(row["emotions"]["n"] for row in chakras))
    return {"physics": _arc(phys), "energy": _arc(ener), "emotions": _arc(emo)}


def _fmt(arc: dict[str, Any]) -> str:
    """'7 (Колесница)' — аркан для промпта."""
    return f"{arc['n']} ({arc['name']})"


def destiny_matrix_to_system_text(dm: dict[str, Any]) -> str:
    """Собрать блок Матрицы Судьбы для инжекции в system-промпт LLM.

    Как и блок натальной карты, это ДАННЫЕ для интерпретации: модель объясняет
    арканы (значения берёт из базы знаний), но не пересчитывает и не выдумывает числа.
    """
    square = dm["ancestral_square"]
    purpose = dm["purpose"]
    anc = dm["ancestral"]
    money = dm["money_line"]
    love = dm["relationship_line"]
    lines: list[str] = [
        f"=== РАССЧИТАННАЯ КАРТА СУДЬБЫ (Матрица Судьбы, дата {dm['birth_date']}) ===",
        "Это ОТДЕЛЬНАЯ от астрологии система (нумерология 22 Старших Арканов по дате "
        "рождения; НЕ эфемериды, НЕ знаки зодиака, НЕ дома). Не смешивай её с натальной "
        "картой.",
        "",
        "Личностный квадрат (портрет и личные задачи):",
        f"  День (портрет/личность): {_fmt(dm['day'])}",
        f"  Месяц (что дано, таланты рода): {_fmt(dm['month'])}",
        f"  Год (материальная карма, что взято из прошлого): {_fmt(dm['year'])}",
        f"  Кармическая задача (низ): {_fmt(dm['karma'])}",
        f"  ЗОНА КОМФОРТА / главная энергия (центр): {_fmt(dm['comfort_zone'])}",
        "",
        "Родовой квадрат (программы рода):",
        f"  Верх-лево (день+месяц): {_fmt(square['day_month'])}",
        f"  Верх-право (месяц+год): {_fmt(square['month_year'])}",
        f"  Низ-право (год+карма): {_fmt(square['year_karma'])}",
        f"  Низ-лево (карма+день): {_fmt(square['karma_day'])}",
        "",
        "Родовые линии:",
        f"  Мужская линия (род отца): {_fmt(anc['male_line'][0])} ↔ "
        f"{_fmt(anc['male_line'][1])} → итог {_fmt(anc['male_total'])}",
        f"  Женская линия (род матери): {_fmt(anc['female_line'][0])} ↔ "
        f"{_fmt(anc['female_line'][1])} → итог {_fmt(anc['female_total'])}",
        "",
        "Предназначение:",
        f"  Небо (духовное «для себя»): {_fmt(purpose['sky'])}",
        f"  Земля (материальное «для себя»): {_fmt(purpose['earth'])}",
        f"  Личное предназначение (до ~40 лет): {_fmt(purpose['personal'])}",
        f"  Социальное предназначение (род, ~40–60): {_fmt(purpose['social'])}",
        f"  Духовное предназначение (итог, 60+): {_fmt(purpose['spiritual'])}",
        "",
        "Денежный канал:",
        f"  Вход в материальный мир: {_fmt(money['entry'])}; поток: {_fmt(money['flow'])}; "
        f"итог: {_fmt(money['total'])}",
        "Канал отношений:",
        f"  Точка входа (у кармического хвоста): {_fmt(love['entry'])}; поток: "
        f"{_fmt(love['flow'])}; итог: {_fmt(love['total'])}",
        "",
        "Карта здоровья (чакры сверху вниз — физика / энергия / эмоции):",
    ]
    for row in dm["chakras"]:
        lines.append(
            f"  {row['chakra']}: физика {_fmt(row['physics'])}, "
            f"энергия {_fmt(row['energy'])}, эмоции {_fmt(row['emotions'])}"
        )
    totals = dm["chakra_totals"]
    lines.append(
        f"  Общий знаменатель: физика {_fmt(totals['physics'])}, "
        f"энергия {_fmt(totals['energy'])}, эмоции {_fmt(totals['emotions'])}"
    )
    lines.append("=== КОНЕЦ КАРТЫ СУДЬБЫ ===")
    lines.append(
        "Используй ТОЛЬКО эти рассчитанные арканы. Значения арканов (плюс/минус, "
        "предназначение) бери из базы знаний Матрицы Судьбы. Не пересчитывай числа и не "
        "выдумывай позиции. Не путай с астрологией: это разные системы."
    )
    return "\n".join(lines)
