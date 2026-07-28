"""Юнит-тесты кошелькового ``QuotaService``: промо-обход и списание с баланса (моки)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from mandala.services.quota import (
    REASON_INSUFFICIENT_BALANCE,
    RESOURCE_IMAGE_GENERATION,
    RESOURCE_TEXT_REPLY,
    QuotaService,
)


def _mock_conn() -> MagicMock:
    return MagicMock()


# is_promo_active импортируется внутри метода `from mandala.services.promo import ...`;
# патчим в исходном модуле, где функция определена.
_PROMO_PATCH = "mandala.services.promo.is_promo_active"
_WALLET_PATCH = "mandala.services.quota.WalletRepository"


def test_consume_allows_when_promo_active() -> None:
    """Промо («вечный пакет») → allowed=True без списания баланса."""
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


def test_consume_skips_wallet_when_promo_active() -> None:
    """При активном промо кошелёк не трогаем (безлимит)."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=True),
        patch(_WALLET_PATCH) as mock_wallet,
    ):
        QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_TEXT_REPLY,
        )

    mock_wallet.assert_not_called()


def test_consume_deducts_wallet_without_promo() -> None:
    """Без промо текстовый ответ атомарно списывает 1 сообщение с баланса."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=False),
        patch(_WALLET_PATCH) as mock_wallet,
    ):
        mock_wallet.return_value.try_consume.return_value = 4  # новый баланс после списания
        result = QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_TEXT_REPLY,
        )

    assert result.allowed is True
    mock_wallet.return_value.try_consume.assert_called_once()


def test_consume_denies_on_empty_balance() -> None:
    """Баланс 0 → try_consume вернул None → отказ (insufficient_balance)."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=False),
        patch(_WALLET_PATCH) as mock_wallet,
    ):
        mock_wallet.return_value.try_consume.return_value = None
        result = QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_TEXT_REPLY,
        )

    assert result.allowed is False
    assert result.reason == REASON_INSUFFICIENT_BALANCE


def test_image_not_charged_from_wallet_without_promo() -> None:
    """Картинки кошельком не тарифицируются: без промо — отказ, try_consume не вызывается."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=False),
        patch(_WALLET_PATCH) as mock_wallet,
    ):
        result = QuotaService(conn).consume(
            user_id=uid,
            vertical_id="astrology",
            resource=RESOURCE_IMAGE_GENERATION,
        )

    assert result.allowed is False
    mock_wallet.return_value.try_consume.assert_not_called()


def test_can_consume_reads_balance_without_promo() -> None:
    """can_consume без промо: True при балансе > 0, False при 0/отсутствии строки."""
    conn = _mock_conn()
    uid = uuid4()

    with (
        patch(_PROMO_PATCH, return_value=False),
        patch(_WALLET_PATCH) as mock_wallet,
    ):
        mock_wallet.return_value.get_balance.return_value = 3
        assert (
            QuotaService(conn).can_consume(
                user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY
            )
            is True
        )
        mock_wallet.return_value.get_balance.return_value = 0
        assert (
            QuotaService(conn).can_consume(
                user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY
            )
            is False
        )
        mock_wallet.return_value.get_balance.return_value = None
        assert (
            QuotaService(conn).can_consume(
                user_id=uid, vertical_id="astrology", resource=RESOURCE_TEXT_REPLY
            )
            is False
        )
