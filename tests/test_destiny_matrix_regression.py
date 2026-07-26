"""Регресс-тест точности движка «Карты судьбы» (Матрица Судьбы, система Ладини).

Зеркалит подход ``tests/test_evgenia_natal_regression.py``: фиксирует, что чистая
арифметика движка воспроизводит ЯДРО октаграммы двух независимых референсных
калькуляторов ТОЧЬ-В-ТОЧЬ, и снапшотит производные линии, чтобы они не менялись молча.

Референсы (день/месяц/год/карма/центр):

* 07.01.1987 → 7 / 1 / 7 / 15 / 3, углы родового квадрата 8 / 8 / 22 / 22
  (калькулятор destiny-matrix.cc; центр 30 → 3 подтверждает свод «сложением цифр»,
  а не «вычитанием 22», где было бы 8);
* 29.01.1991 → 11 / 1 / 20 / 5 / 10 (калькулятор letu.ru; день 29 → 11 и карма
  32 → 5, центр 37 → 10 — тоже свод сложением цифр).

Движок офлайн и детерминирован: Матрица Судьбы выводится ТОЛЬКО из даты рождения
(нумерология 22 арканов), без эфемерид, времени и места — в отличие от натальной карты.
"""

from __future__ import annotations

from mandala.astro.destiny_matrix import (
    ARCANA_NAMES,
    CHAKRAS_TOP_DOWN,
    compute_destiny_matrix,
    destiny_matrix_to_system_text,
    reduce_to_arcana,
)


def test_reduction_rule_is_digit_sum_not_subtract_22() -> None:
    """Свод к аркану 1..22 — сложением цифр; 22 сохраняется (не 4)."""
    assert reduce_to_arcana(26) == 8  # 2+6 (вычитание дало бы 4)
    assert reduce_to_arcana(30) == 3
    assert reduce_to_arcana(37) == 10
    assert reduce_to_arcana(44) == 8
    assert reduce_to_arcana(22) == 22  # сохраняется
    assert reduce_to_arcana(15) == 15  # <=22 без изменений
    assert reduce_to_arcana(1) == 1


def _core(dm: dict[str, object]) -> tuple[int, int, int, int, int]:
    return (
        dm["day"]["n"],  # type: ignore[index]
        dm["month"]["n"],  # type: ignore[index]
        dm["year"]["n"],  # type: ignore[index]
        dm["karma"]["n"],  # type: ignore[index]
        dm["comfort_zone"]["n"],  # type: ignore[index]
    )


def test_reference_1987_core_matches_external_calculator() -> None:
    """07.01.1987: ядро октаграммы совпадает с референсным калькулятором."""
    dm = compute_destiny_matrix("07.01.1987")
    assert _core(dm) == (7, 1, 7, 15, 3)
    sq = dm["ancestral_square"]
    corners = (
        sq["day_month"]["n"],
        sq["month_year"]["n"],
        sq["year_karma"]["n"],
        sq["karma_day"]["n"],
    )
    assert corners == (8, 8, 22, 22)


def test_reference_1991_core_matches_external_calculator() -> None:
    """29.01.1991: день 29→11, карма 32→5, центр 37→10 — свод сложением цифр."""
    dm = compute_destiny_matrix("29.01.1991")
    assert _core(dm) == (11, 1, 20, 5, 10)


def test_year_digit_sum_step() -> None:
    """Год сначала сворачивается суммой всех цифр, затем сводится к аркану."""
    # 1988 → 1+9+8+8 = 26 → 8 (доминирующая конвенция; не 4).
    assert compute_destiny_matrix("01.01.1988")["year"]["n"] == 8
    # 2000 → 2, 1999 → 28 → 10.
    assert compute_destiny_matrix("01.01.2000")["year"]["n"] == 2
    assert compute_destiny_matrix("01.01.1999")["year"]["n"] == 10


def test_full_derived_snapshot_1987() -> None:
    """Снапшот всех производных линий для 07.01.1987 (защита от молчаливого дрейфа)."""
    dm = compute_destiny_matrix("07.01.1987")

    assert dm["diagonals"]["sky"]["n"] == 16  # небо = месяц+карма
    assert dm["diagonals"]["earth"]["n"] == 14  # земля = день+год

    purpose = dm["purpose"]
    assert (purpose["personal"]["n"], purpose["social"]["n"], purpose["spiritual"]["n"]) == (
        3,
        6,
        9,
    )
    assert purpose["summary"]["n"] == purpose["spiritual"]["n"]

    anc = dm["ancestral"]
    assert anc["male_total"]["n"] == 3
    assert anc["female_total"]["n"] == 3

    assert dm["money_line"]["total"]["n"] == 4
    assert dm["relationship_line"]["total"]["n"] == 5

    # Карта здоровья: физика/энергия/эмоции сверху вниз.
    physics = [row["physics"]["n"] for row in dm["chakras"]]
    energy = [row["energy"]["n"] for row in dm["chakras"]]
    emotions = [row["emotions"]["n"] for row in dm["chakras"]]
    assert physics == [7, 10, 13, 3, 10, 17, 7]
    assert energy == [1, 4, 7, 3, 18, 6, 15]
    assert emotions == [8, 14, 20, 6, 10, 5, 22]
    totals = dm["chakra_totals"]
    assert (totals["physics"]["n"], totals["energy"]["n"], totals["emotions"]["n"]) == (13, 9, 13)


def test_all_positions_are_valid_arcana() -> None:
    """Каждое число карты — валидный аркан 1..22 для набора разноплановых дат."""
    for date in ("07.01.1987", "29.01.1991", "31.12.2024", "01.01.2000", "22.11.1978"):
        dm = compute_destiny_matrix(date)
        for value in _iter_arcana_numbers(dm):
            assert 1 <= value <= 22, f"{date}: аркан {value} вне 1..22"
            assert value in ARCANA_NAMES


def _iter_arcana_numbers(node: object) -> list[int]:
    """Рекурсивно собрать все ``{"n": int}`` из структуры карты."""
    out: list[int] = []
    if isinstance(node, dict):
        if set(node.keys()) == {"n", "name"} and isinstance(node["n"], int):
            out.append(node["n"])
        else:
            for v in node.values():
                out.extend(_iter_arcana_numbers(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_iter_arcana_numbers(v))
    return out


def test_matrix_depends_only_on_date_not_time_or_place() -> None:
    """Матрица Судьбы — нумерология даты: одинакова независимо от контекста (детерминизм)."""
    a = compute_destiny_matrix("07.01.1987")
    b = compute_destiny_matrix("07.01.1987")
    assert a == b
    # Другая дата — другая карта.
    assert compute_destiny_matrix("08.01.1987") != a


def test_chakra_table_has_seven_rows_top_down() -> None:
    """Карта здоровья — 7 чакр Сахасрара→Муладхара с тремя столбцами."""
    dm = compute_destiny_matrix("29.01.1991")
    names = [row["chakra"] for row in dm["chakras"]]
    assert names == list(CHAKRAS_TOP_DOWN)
    assert names[0] == "Сахасрара" and names[-1] == "Муладхара"
    for row in dm["chakras"]:
        assert set(row.keys()) == {"chakra", "physics", "energy", "emotions"}


def test_system_text_declares_system_and_forbids_mixing() -> None:
    """Блок для LLM называет систему и запрещает смешение с астрологией и выдумывание."""
    text = destiny_matrix_to_system_text(compute_destiny_matrix("07.01.1987"))
    assert "Матрица Судьбы" in text
    assert "ОТДЕЛЬНАЯ от астрологии" in text
    assert "Не смешивай" in text or "Не путай с астрологией" in text
    assert "базы знаний" in text  # значения арканов — из RAG, не из чисел
    assert "07.01.1987" in text


def test_invalid_birth_date_raises() -> None:
    """Некорректная дата → ValueError (не молчаливый мусор)."""
    import pytest

    for bad in ("", "1987", "32.01.1987", "07.13.1987", "abc"):
        with pytest.raises(ValueError):
            compute_destiny_matrix(bad)
