"""Тест: квота consume пропускает пользователей с активным промо-кодом (P0 fix)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from mandala.services.quota import RESOURCE_TEXT_REPLY, QuotaService


def _mock_conn() -> MagicMock:
    return MagicMock()


# is_promo_active загружается через `from mandala.services.promo import is_promo_active`
# внутри метода; патчим в исходном модуле, где функция определена.
_PROMO_PATCH = "mandala.services.promo.is_promo_active"


def test_consume_allows_when_promo_active() -> None:
    """consume возвращает allowed=True если промо активно."""
    conn = _mock_conn()
    uid = uuid4()

    with patch(_PROMO_PATCH, return_value=True):
        result = QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_TEXT_REPLY,
        )

    assert result.allowed is True
    assert result.reason is None


def test_consume_skips_plan_lookup_when_promo_active() -> None:
    """consume с промо не обращается к UsersRepository."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=True),
        patch("mandala.services.quota.UsersRepository") as mock_users,
    ):
        QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_TEXT_REPLY,
        )

    mock_users.assert_not_called()


def test_consume_proceeds_normally_without_promo() -> None:
    """consume без промо проверяет план и инкремент как обычно."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=False),
        patch("mandala.services.quota.UsersRepository") as mock_users,
    ):
        mock_users.return_value.fetch_current_plan_id.return_value = None
        result = QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_TEXT_REPLY,
        )

    assert result.allowed is False
    mock_users.assert_called_once()
