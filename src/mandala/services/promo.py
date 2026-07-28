"""Промо-коды: активация безлимитного «вечного пакета» для пользователя.

Активное промо = **безлимит навсегда**: обходит кошелёк сообщений (списания нет,
см. ``mandala.services.quota``). Хранится в ``client_profiles.agent_card`` под ключом
``activated_promo`` — durable-место, которое **переживает ``/reset``**:
``ProfileRepository.reset_session`` осознанно сохраняет этот ключ (требование: после
``/reset`` промо остаётся с пользователем). ``is_promo_active`` читает именно оттуда.
Других видов промо не вводим.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.engine import Connection

from mandala.repositories.profiles import ProfileRepository

logger = logging.getLogger(__name__)

_AGENT_CARD_PROMO_KEY = "activated_promo"

VALID_PROMO_CODES: frozenset[str] = frozenset(
    {"MANDALA2025", "ASTRO_VIP", "FOREVER", "TESTME", "UNLIMITED"}
)


def activate_promo(*, code: str, user_id: UUID, vertical_id: str, conn: Connection) -> bool:
    """Активировать промо-код: сохранить в agent_card, вернуть True если код валидный."""
    normalized = code.strip().upper()
    if normalized not in VALID_PROMO_CODES:
        logger.info(
            "promo invalid code=%r user_id=%s vertical_id=%s", normalized, user_id, vertical_id
        )
        return False
    ProfileRepository(conn).merge_agent_card(user_id, {_AGENT_CARD_PROMO_KEY: normalized})
    logger.info(
        "promo activated code=%r user_id=%s vertical_id=%s", normalized, user_id, vertical_id
    )
    return True


def is_promo_active(*, user_id: UUID, vertical_id: str, conn: Connection) -> bool:
    """Проверить, есть ли активный промо-код у пользователя."""
    profile = ProfileRepository(conn).get_by_user_id(user_id)
    if profile is None:
        return False
    promo = profile.agent_card.get(_AGENT_CARD_PROMO_KEY)
    return isinstance(promo, str) and bool(promo.strip())
