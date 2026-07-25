"""Тест: city not found при расчёте натальной карты возвращает сообщение пользователю (P0 fix)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from mandala.services.scenario_intake import _try_calculate_and_save_natal_chart

# calculate_natal_chart импортируется внутри функции; патчим в исходном модуле.
_NATAL_PATCH = "mandala.astro.natal_chart.calculate_natal_chart"


def test_city_not_found_returns_user_message() -> None:
    """Если геокодинг не нашёл город — возвращается сообщение для показа пользователю."""
    conn = MagicMock()
    uid = uuid4()
    profiles = MagicMock()
    agent_card = {
        "birth_date": "17.03.1992",
        "birth_time": "12:00",
        "birth_place": "Урюпинск_Xyz_Notexist",
    }

    with patch(_NATAL_PATCH, side_effect=ValueError("City not found: 'Урюпинск_Xyz_Notexist'")):
        result = _try_calculate_and_save_natal_chart(
            conn=conn,
            user_id=uid,
            agent_card=agent_card,
            profiles=profiles,
        )

    assert result is not None
    assert "Урюпинск_Xyz_Notexist" in result
    assert "/reset" in result
    profiles.merge_agent_card.assert_not_called()


def test_geocoding_failed_returns_user_message() -> None:
    """Если Nominatim недоступен (Geocoding failed) — тоже возвращается сообщение."""
    conn = MagicMock()
    uid = uuid4()
    profiles = MagicMock()
    agent_card = {
        "birth_date": "17.03.1992",
        "birth_time": "unknown",
        "birth_place": "Тестгород",
    }

    err = ValueError("Geocoding failed for 'Тестгород': Connection refused")
    with patch(_NATAL_PATCH, side_effect=err):
        result = _try_calculate_and_save_natal_chart(
            conn=conn,
            user_id=uid,
            agent_card=agent_card,
            profiles=profiles,
        )

    assert result is not None
    assert "/reset" in result


def test_other_error_returns_none() -> None:
    """При произвольной ошибке (не геокодинг) — возвращается None (тихий failsafe)."""
    conn = MagicMock()
    uid = uuid4()
    profiles = MagicMock()
    agent_card = {
        "birth_date": "17.03.1992",
        "birth_time": "12:00",
        "birth_place": "Москва",
    }

    with patch(_NATAL_PATCH, side_effect=RuntimeError("kerykeion internal error")):
        result = _try_calculate_and_save_natal_chart(
            conn=conn,
            user_id=uid,
            agent_card=agent_card,
            profiles=profiles,
        )

    assert result is None


def test_success_returns_none() -> None:
    """При успешном расчёте — возвращается None."""
    conn = MagicMock()
    uid = uuid4()
    profiles = MagicMock()
    agent_card = {
        "birth_date": "17.03.1992",
        "birth_time": "12:00",
        "birth_place": "Москва",
    }
    fake_chart = {"sun_sign": "Рыбы", "moon_sign": "Козерог", "planets": {}}

    with patch(_NATAL_PATCH, return_value=fake_chart):
        result = _try_calculate_and_save_natal_chart(
            conn=conn,
            user_id=uid,
            agent_card=agent_card,
            profiles=profiles,
        )

    assert result is None
    profiles.merge_agent_card.assert_called_once()
