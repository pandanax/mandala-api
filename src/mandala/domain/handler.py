"""Точка входа доменной обработки входящих событий (тикеты 6, 8)."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from mandala.domain.contracts import InboundEvent, OutboundMessage
from mandala.repositories import ProfileRepository
from mandala.services.user_identity import UserIdentityService


def handle_inbound(event: InboundEvent, conn: Connection) -> list[OutboundMessage]:
    """Обработать входящее событие и вернуть исходящие сообщения.

    Тикет 8: резолвинг пользователя по ``(vertical_id, channel, external_user_id)``,
    план по умолчанию ``free``; загрузка строки ``client_profiles``.

    ``conn`` — открытое соединение SQLAlchemy в **активной транзакции** (например
    ``with engine.begin() as conn``), чтобы резолвинг и чтение профиля были согласованы.

    TODO(тикет 12+): ``mandala.services.quota.QuotaService`` и сохранение в ``messages``.
    """
    uid = UserIdentityService(conn).get_or_create_user(
        vertical_id=event.vertical_id,
        channel=event.channel,
        external_user_id=event.external_user_id,
        locale=event.locale,
    )
    profiles = ProfileRepository(conn)
    profiles.ensure_row(user_id=uid, vertical_id=event.vertical_id)
    profile = profiles.get_by_user_id(uid)
    if profile is None:
        msg = "client_profiles: ensure_row не создал строку"
        raise RuntimeError(msg)

    card_keys = len(profile.agent_card)
    scenario_keys = len(profile.scenario_state)
    return [
        OutboundMessage(
            text=(
                f"Вертикаль «{event.vertical_id}», канал «{event.channel}». "
                f"Пользователь зарезолвен, профиль загружен "
                f"(agent_card: {card_keys} ключей, scenario_state: {scenario_keys} ключей)."
            )
        )
    ]
