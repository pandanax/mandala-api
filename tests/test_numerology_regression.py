"""Регресс-тест точности движка пифагорейской нумерологии (имя + дата).

Зеркалит подход ``tests/test_destiny_matrix_regression.py``: фиксирует, что чистая
арифметика движка воспроизводит ядровые числа по РЕАЛИЗОВАННОЙ стандартной школе
точь-в-точь, и снапшотит их, чтобы они не менялись молча.

Реализованная конструкция (см. докстринг ``mandala.astro.numerology``):

* свод сложением цифр, мастер-числа **11/22/33 сохраняются**;
* жизненный путь — метод Декоза (свод дня, месяца, года по отдельности → сумма → свод);
* кириллическая таблица Пифагора (позиция буквы 33-алфавита mod 9), Ё=7, Ъ/Ь пропускаются;
* гласные кириллицы: а е ё и о у ы э ю я (й — согласная);
* латиница — стандартная таблица A=1…I=9, J=1…, гласные A E I O U.

Опорный пример посчитан вручную (см. комментарии) — «Иван Иванов», 17.03.1992.
Движок офлайн и детерминирован (никаких эфемерид/сети).
"""

from __future__ import annotations

import pytest

from mandala.astro.numerology import (
    CYRILLIC_LETTER_VALUES,
    LATIN_LETTER_VALUES,
    MASTER_NUMBERS,
    compute_numerology,
    is_master,
    numerology_to_system_text,
    reduce_number,
)


def test_reduction_keeps_master_numbers() -> None:
    """Свод сложением цифр; 11/22/33 сохраняются (не сводятся до 2/4/6)."""
    assert reduce_number(38) == 11  # 3+8 (мастер, сохраняется)
    assert reduce_number(39) == 3  # 3+9 = 12 → 3
    assert reduce_number(22) == 22
    assert reduce_number(33) == 33
    assert reduce_number(29) == 11  # 2+9 = 11
    assert reduce_number(5) == 5
    # Без сохранения мастера — всегда до 1..9.
    assert reduce_number(11, keep_master=False) == 2
    assert reduce_number(22, keep_master=False) == 4


def test_cyrillic_table_columns() -> None:
    """Опорные столбцы русской таблицы Пифагора (позиция mod 9)."""
    assert CYRILLIC_LETTER_VALUES["а"] == 1
    assert CYRILLIC_LETTER_VALUES["и"] == 1
    assert CYRILLIC_LETTER_VALUES["с"] == 1
    assert CYRILLIC_LETTER_VALUES["ё"] == 7  # Ё — отдельная буква (не «е» = 6)
    assert CYRILLIC_LETTER_VALUES["е"] == 6
    assert CYRILLIC_LETTER_VALUES["з"] == 9
    assert CYRILLIC_LETTER_VALUES["я"] == 6


def test_latin_table_columns() -> None:
    """Стандартная латинская таблица Пифагора."""
    assert LATIN_LETTER_VALUES["a"] == 1
    assert LATIN_LETTER_VALUES["i"] == 9
    assert LATIN_LETTER_VALUES["j"] == 1
    assert LATIN_LETTER_VALUES["r"] == 9
    assert LATIN_LETTER_VALUES["s"] == 1
    assert LATIN_LETTER_VALUES["z"] == 8


def test_reference_ivan_ivanov_snapshot() -> None:
    """«Иван Иванов», 17.03.1992 — все ядровые числа посчитаны вручную.

    Жизненный путь: день 17→8, месяц 3→3, год 1992→21→3; 8+3+3=14→5.
    День рождения: 17→8.
    Буквы «иваниванов»: и1 в3 а1 н6 и1 в3 а1 н6 о7 в3 (сумма 32→5) = ВЫРАЖЕНИЕ 5.
    Гласные (и,а,и,а,о = 1+1+1+1+7=11) → ДУША 11 (мастер).
    Согласные (в,н,в,н,в = 3+6+3+6+3=21→3) → ЛИЧНОСТЬ 3.
    Зрелость: путь 5 + выражение 5 = 10 → 1.
    """
    d = compute_numerology("Иван Иванов", "17.03.1992")
    n = d["numbers"]
    assert d["alphabet"] == "cyrillic"
    assert d["has_name"] is True
    assert n["life_path"] == 5
    assert n["birthday"] == 8
    assert n["expression"] == 5
    assert n["soul_urge"] == 11
    assert n["personality"] == 3
    assert n["maturity"] == 1
    # Свойство пифагорейской школы: выражение = свод(душа_сумма + личность_сумма).
    # (11 как душа — уже мастер; проверяем, что мастер долетел до сохранённого числа.)
    assert is_master(n["soul_urge"])


def test_master_number_life_paths() -> None:
    """Жизненный путь может быть мастер-числом (11/22) и не сводится."""
    # 18.09.2011: день 18→9, месяц 9, год 2011→4; 9+9+4=22 (мастер).
    assert compute_numerology("", "18.09.2011")["numbers"]["life_path"] == 22
    # 17.02.1990: день 17→8, месяц 2, год 1990→19→10→1; 8+2+1=11 (мастер).
    assert compute_numerology("", "17.02.1990")["numbers"]["life_path"] == 11


def test_soft_signs_are_skipped_in_name_numbers() -> None:
    """Ъ и Ь не имеют значения в числах имени (пропускаются)."""
    # «Обь» = о(7) + б(2) + ь(0) → выражение 9; согласные б(2) → личность 2;
    # гласные о(7) → душа 7. Выражение(9) = свод(7+2).
    d = compute_numerology("Обь", "01.01.2000")["numbers"]
    assert d["expression"] == 9
    assert d["soul_urge"] == 7
    assert d["personality"] == 2


def test_yo_is_distinct_from_ye() -> None:
    """Ё (7) считается отдельно от Е (6) — влияет на числа имени."""
    a = compute_numerology("Алёна", "01.01.2000")["numbers"]["expression"]
    b = compute_numerology("Алена", "01.01.2000")["numbers"]["expression"]
    assert a != b  # ё=7 vs е=6 меняют сумму


def test_latin_fallback() -> None:
    """Латинское имя считается по латинской таблице (fallback)."""
    # «John», 01.01.2000: j1 o6 h8 n5 → выражение 20→2; гласная o → душа 6;
    # согласные j,h,n = 1+8+5=14→5 → личность 5; путь 4; зрелость 4+2=6.
    d = compute_numerology("John", "01.01.2000")
    assert d["alphabet"] == "latin"
    n = d["numbers"]
    assert (n["life_path"], n["expression"], n["soul_urge"], n["personality"], n["maturity"]) == (
        4,
        2,
        6,
        5,
        6,
    )


def test_case_spaces_hyphens_ignored() -> None:
    """Регистр, пробелы и дефисы не влияют на результат."""
    a = compute_numerology("Анна-Мария Ли", "07.01.1987")
    b = compute_numerology("  аннамарияли  ", "07.01.1987")
    assert a["numbers"] == b["numbers"]


def test_missing_name_degrades_gracefully() -> None:
    """Без имени доступны только числа от даты; числа имени — None (без падения)."""
    d = compute_numerology("", "17.03.1992")
    n = d["numbers"]
    assert d["has_name"] is False
    assert d["alphabet"] == "none"
    assert n["life_path"] == 5 and n["birthday"] == 8
    assert n["expression"] is None
    assert n["soul_urge"] is None
    assert n["personality"] is None
    assert n["maturity"] is None


def test_depends_on_name_and_date() -> None:
    """Детерминизм: та же пара — тот же результат; другое имя/дата — другой."""
    base = compute_numerology("Иван Иванов", "17.03.1992")
    assert compute_numerology("Иван Иванов", "17.03.1992") == base
    assert compute_numerology("Пётр Иванов", "17.03.1992") != base  # имя влияет
    assert compute_numerology("Иван Иванов", "18.03.1992") != base  # дата влияет


def test_all_numbers_are_valid() -> None:
    """Каждое число карты — 1..9 или мастер 11/22/33 для набора разноплановых входов."""
    valid = set(range(1, 10)) | set(MASTER_NUMBERS)
    for name, date in (
        ("Иван Иванов", "07.01.1987"),
        ("Мария", "29.01.1991"),
        ("John Smith", "31.12.2024"),
        ("", "01.01.2000"),
    ):
        for value in compute_numerology(name, date)["numbers"].values():
            assert value is None or value in valid


def test_system_text_declares_system_and_forbids_mixing() -> None:
    """Блок для LLM называет систему, запрещает смешение и опирается на базу знаний."""
    text = numerology_to_system_text(compute_numerology("Иван Иванов", "17.03.1992"))
    assert "НУМЕРОЛОГИЯ" in text
    assert "ОТДЕЛЬНАЯ" in text
    assert "Не смешивай" in text or "Не путай" in text
    assert "базы знаний" in text
    assert "17.03.1992" in text
    assert "жизненного пути" in text


def test_invalid_birth_date_raises() -> None:
    """Некорректная дата → ValueError (не молчаливый мусор)."""
    for bad in ("", "1987", "32.01.1987", "07.13.1987", "abc"):
        with pytest.raises(ValueError):
            compute_numerology("Иван", bad)
