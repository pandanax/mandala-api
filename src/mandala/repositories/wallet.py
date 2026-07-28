"""Репозиторий кошелька сообщений: баланс на ``users.message_balance`` (пакетная модель).

Баланс — целое число, ключ **(``user_id``, ``vertical_id``)**: одна строка ``users`` на пару
канал+вертикаль (мультибот), поэтому баланс естественно «свой» у каждого бота. Баланс не
привязан к периоду, **не сбрасывается по времени** и **не трогается** ``/reset`` (тот чистит
только ``client_profiles``).

Списание атомарно: ``UPDATE … SET message_balance = message_balance - :amt
WHERE … AND message_balance >= :amt`` — Postgres сериализует конфликтующие обновления одной
строки, условие не даёт уйти в минус даже при параллельных списаниях (дух прежнего
``UsageRepository.try_increment``). Зачисление пакета — атомарный ``+ :amt``.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection


class WalletRepository:
    """Чтение/атомарное изменение баланса сообщений на ``users``."""

    __slots__ = ("_conn",)

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get_balance(self, *, user_id: UUID, vertical_id: str) -> int | None:
        """Текущий баланс или ``None``, если строки ``users`` нет / вертикаль не совпала."""
        row = self._conn.execute(
            text(
                """
                SELECT message_balance
                FROM users
                WHERE id = :user_id
                  AND vertical_id = :vertical_id
                """
            ),
            {"user_id": user_id, "vertical_id": vertical_id},
        ).one_or_none()
        if row is None:
            return None
        return int(row[0])

    def try_consume(self, *, user_id: UUID, vertical_id: str, amount: int = 1) -> int | None:
        """Атомарно списать ``amount`` (по умолчанию 1), если хватает баланса.

        Возвращает новый баланс при успехе или ``None`` (баланса не хватило / нет строки).
        Без ухода в минус и без гонок (условие ``message_balance >= :amt`` в самом ``UPDATE``).
        """
        row = self._conn.execute(
            text(
                """
                UPDATE users
                SET message_balance = message_balance - :amt,
                    updated_at = now()
                WHERE id = :user_id
                  AND vertical_id = :vertical_id
                  AND message_balance >= :amt
                RETURNING message_balance
                """
            ),
            {"user_id": user_id, "vertical_id": vertical_id, "amt": amount},
        ).one_or_none()
        if row is None:
            return None
        return int(row[0])

    def add_balance(self, *, user_id: UUID, vertical_id: str, amount: int) -> int | None:
        """Атомарно зачислить ``amount`` сообщений на баланс (покупка пакета не сгорает).

        Возвращает новый баланс или ``None``, если строки ``users`` нет / вертикаль не совпала.
        Идемпотентность зачисления обеспечивается уровнем выше (журнал ``payment_transactions``).
        """
        row = self._conn.execute(
            text(
                """
                UPDATE users
                SET message_balance = message_balance + :amt,
                    updated_at = now()
                WHERE id = :user_id
                  AND vertical_id = :vertical_id
                RETURNING message_balance
                """
            ),
            {"user_id": user_id, "vertical_id": vertical_id, "amt": amount},
        ).one_or_none()
        if row is None:
            return None
        return int(row[0])
