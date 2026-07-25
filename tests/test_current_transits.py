"""Тест: форматирование текущих транзитов для системного промпта (P0.3 fix)."""

from __future__ import annotations

from mandala.astro.natal_chart import current_transits_to_system_text


def test_current_transits_to_system_text_basic_structure() -> None:
    """Функция форматирования транзитов возвращает блок с заголовком и телом."""
    fake_transits = {
        "date": "25.07.2026",
        "planets": {
            "Солнце": {"sign": "Лев", "degree": 2.5, "retrograde": False},
            "Луна": {"sign": "Рак", "degree": 18.3, "retrograde": False},
            "Меркурий": {"sign": "Лев", "degree": 10.1, "retrograde": True},
        },
    }
    text = current_transits_to_system_text(fake_transits)

    assert "25.07.2026" in text
    assert "Солнце" in text
    assert "Лев" in text
    assert "(Rx)" in text  # ретроградный Меркурий помечен
    assert "ТРАНЗИТ" in text.upper()
    assert "Не говори" in text or "не говори" in text


def test_current_transits_to_system_text_no_planets() -> None:
    """Пустой dict planets не вызывает ошибки."""
    text = current_transits_to_system_text({"date": "01.01.2026", "planets": {}})
    assert "01.01.2026" in text
    assert isinstance(text, str)


def test_current_transits_retrograde_flag() -> None:
    """Ретроградные планеты помечаются (Rx), прямые — нет."""
    fake_transits = {
        "date": "01.01.2026",
        "planets": {
            "Марс": {"sign": "Козерог", "degree": 5.0, "retrograde": True},
            "Венера": {"sign": "Рыбы", "degree": 20.0, "retrograde": False},
        },
    }
    text = current_transits_to_system_text(fake_transits)
    lines = text.splitlines()
    mars_line = next((ln for ln in lines if "Марс" in ln), "")
    venus_line = next((ln for ln in lines if "Венера" in ln), "")
    assert "(Rx)" in mars_line
    assert "(Rx)" not in venus_line
