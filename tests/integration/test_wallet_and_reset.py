"""Интеграция: ``/reset`` сохраняет баланс кошелька и промо, но чистит анкету/историю.

Ключевое требование пакетной модели: пакеты (баланс) и «вечный пакет» (промо) остаются с
пользователем после ``/reset``; сбрасывается только профиль (анкета/история/agent_card).
Нужен ``DATABASE_URL``.
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from mandala.db.engine import create_engine_from_env
from mandala.repositories import MessageRepository, ProfileRepository, WalletRepository
from mandala.services.promo import is_promo_active
from mandala.services.user_identity import UserIdentityService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL"),
        reason="DATABASE_URL не задан — интеграционные тесты пропущены",
    ),
]


@pytest.fixture
def engine() -> Engine:
    return create_engine_from_env()


def _make_user(conn: Connection, ext: str, *, vertical: str = "astrology") -> UUID:
    uid = UserIdentityService(conn).get_or_create_user(
        vertical_id=vertical, channel="telegram", external_user_id=ext
    )
    ProfileRepository(conn).ensure_row(user_id=uid, vertical_id=vertical)
    return uid


def _set_balance(conn: Connection, uid: UUID, value: int) -> None:
    conn.execute(
        text("UPDATE users SET message_balance = :b WHERE id = :uid"),
        {"b": value, "uid": uid},
    )


def test_reset_preserves_balance_and_clears_profile(engine: Engine) -> None:
    ext = f"reset-bal-{uuid4()}"
    vertical = "astrology"
    with engine.begin() as conn:
        uid = _make_user(conn, ext, vertical=vertical)
        _set_balance(conn, uid, 12)
        pr = ProfileRepository(conn)
        pr.merge_agent_card(
            uid,
            {"full_name": "Тест", "birth_date": "07.01.1987", "intake_complete": True},
        )
        MessageRepository(conn).insert(
            user_id=uid, vertical_id=vertical, role="user", content_text="hi", content_kind="text"
        )

    with engine.begin() as conn:
        ProfileRepository(conn).reset_session(uid)
        MessageRepository(conn).delete_for_user_vertical(user_id=uid, vertical_id=vertical)

    with engine.begin() as conn:
        # Баланс кошелька сохранился (лежит в users, не в agent_card).
        assert WalletRepository(conn).get_balance(user_id=uid, vertical_id=vertical) == 12
        prof = ProfileRepository(conn).get_by_user_id(uid)
        assert prof is not None
        # Анкета вычищена.
        assert "full_name" not in prof.agent_card
        assert "birth_date" not in prof.agent_card
        assert "intake_complete" not in prof.agent_card
        # История сообщений вычищена.
        n = conn.execute(
            text("SELECT count(*)::int FROM messages WHERE user_id = :uid"),
            {"uid": uid},
        ).scalar_one()
        assert n == 0


def test_reset_preserves_active_promo(engine: Engine) -> None:
    ext = f"reset-promo-{uuid4()}"
    vertical = "astrology"
    with engine.begin() as conn:
        uid = _make_user(conn, ext, vertical=vertical)
        _set_balance(conn, uid, 0)
        ProfileRepository(conn).merge_agent_card(
            uid,
            {"activated_promo": "TESTME", "full_name": "Тест", "intake_complete": True},
        )

    with engine.begin() as conn:
        assert is_promo_active(user_id=uid, vertical_id=vertical, conn=conn) is True
        ProfileRepository(conn).reset_session(uid)

    with engine.begin() as conn:
        # Промо («вечный пакет») пережил reset…
        assert is_promo_active(user_id=uid, vertical_id=vertical, conn=conn) is True
        prof = ProfileRepository(conn).get_by_user_id(uid)
        assert prof is not None
        # …а анкета — нет: в agent_card остался только ключ промо.
        assert prof.agent_card == {"activated_promo": "TESTME"}
