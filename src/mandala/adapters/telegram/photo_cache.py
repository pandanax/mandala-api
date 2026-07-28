"""Сохранение Telegram ``file_id`` загруженных фото в ``agent_card`` (кэш переотправки).

Загрузка байтов фото (напр. колеса натальной карты) происходит в
:func:`mandala.adapters.telegram.outbound_send.deliver_outbound_messages` — уже ПОСЛЕ
закрытия основной доменной транзакции. Полученный ``file_id`` нужно сохранить, чтобы
следующий ``/natal`` слал фото мгновенно по ``file_id`` без перерисовки. Здесь —
маленькая отдельная короткая транзакция для этой записи (резолвинг пользователя тот же,
что в домене, идемпотентен).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy.engine import Engine

from mandala.domain.contracts import InboundEvent
from mandala.repositories import ProfileRepository
from mandala.services.user_identity import UserIdentityService

logger = logging.getLogger(__name__)


def persist_photo_file_ids(
    engine: Engine,
    event: InboundEvent,
    uploaded: Mapping[str, str],
) -> None:
    """Записать ``{agent_card_key: file_id}`` в ``agent_card`` пользователя события.

    Никогда не поднимает исключение наружу: кэш ``file_id`` — оптимизация, её сбой не
    должен ломать уже успешно доставленный ответ (просто следующий ``/natal`` перерисует).
    """
    if not uploaded:
        return
    try:
        with engine.begin() as conn:
            uid = UserIdentityService(conn).get_or_create_user(
                vertical_id=event.vertical_id,
                channel=event.channel,
                external_user_id=event.external_user_id,
                locale=event.locale,
            )
            ProfileRepository(conn).merge_agent_card(uid, dict(uploaded))
        logger.info("photo file_id cached keys=%s", sorted(uploaded))
    except Exception:  # noqa: BLE001 — кэш не критичен, ответ уже доставлен
        logger.warning("failed to persist photo file_id cache", exc_info=True)


__all__ = ["persist_photo_file_ids"]
