"""Планировщик утренней рассылки: тик, доставка, идемпотентность, старт/стоп (офлайн)."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from mandala.adapters.telegram import daily_forecast_scheduler as sch
from mandala.repositories.daily_forecast import DailyForecastRecipient
from mandala.services import daily_forecast as df
from mandala.verticals.client_knowledge import AGENT_CARD_DAILY_FORECAST_LAST_SENT


def _msk(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 7, 29, h, mi, tzinfo=df.MSK)


# --- фейки БД/сети ------------------------------------------------------------------


class _FakeConn:
    pass


class _FakeEngine:
    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    @contextlib.contextmanager
    def begin(self) -> Any:
        yield _FakeConn()


class _FakeRepo:
    """Отдаёт получателей из store; идемпотентность видна через общий store."""

    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def list_recipients(
        self, *, vertical_id: str, channel: str = "telegram"
    ) -> list[DailyForecastRecipient]:
        out = []
        for uid, ac in self._store["recipients"].items():
            out.append(
                DailyForecastRecipient(
                    user_id=uid,
                    external_user_id=str(self._store["chat_ids"][uid]),
                    agent_card=dict(ac),
                )
            )
        return out


class _FakeProfiles:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def merge_agent_card(self, uid: UUID, patch: dict[str, Any]) -> None:
        self._store["recipients"][uid].update(patch)


class _RecordingApi:
    def __init__(self, token: str, sent: list[dict[str, Any]]) -> None:
        self.token = token
        self._sent = sent

    def __enter__(self) -> _RecordingApi:
        return self

    def __exit__(self, *a: object) -> None:
        pass

    def send_message(
        self, *, chat_id: int, text: str, reply_markup: Any = None, parse_mode: str | None = None
    ) -> dict[str, Any]:
        self._sent.append({"chat_id": chat_id, "text": text, "token": self.token})
        return {"message_id": 1}


class _StubLLM:
    def __init__(self) -> None:
        self.closed = False

    def complete(self, messages: Any, *, max_tokens: int | None = None, **kw: Any) -> str:
        return "Лови день! ☀️"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def scheduler_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    store: dict[str, Any] = {"recipients": {}, "chat_ids": {}, "sent": []}
    monkeypatch.setattr(sch, "DailyForecastRepository", lambda conn: _FakeRepo(store))
    monkeypatch.setattr(sch, "ProfileRepository", lambda conn: _FakeProfiles(store))
    return store


def _seed(store: dict[str, Any], *, chat_id: int, agent_card: dict[str, Any]) -> UUID:
    uid = uuid4()
    store["recipients"][uid] = dict(agent_card)
    store["chat_ids"][uid] = chat_id
    return uid


def _run_tick(store: dict[str, Any], now: datetime, llm: _StubLLM) -> int:
    return sch.run_daily_forecast_tick(
        now=now,
        engine=_FakeEngine(store),  # type: ignore[arg-type]
        token_map={"astrology": "TOK-astro"},
        make_api=lambda token: _RecordingApi(token, store["sent"]),  # type: ignore[arg-type,return-value]
        make_llm=lambda vid: llm,
    )


# --- тик: доставка на chat_id + токен вертикали -------------------------------------


def test_tick_sends_to_external_user_id_with_vertical_token(
    scheduler_store: dict[str, Any],
) -> None:
    _seed(scheduler_store, chat_id=555, agent_card={"birth_date": "17.03.1992"})
    llm = _StubLLM()
    sent = _run_tick(scheduler_store, _msk(10, 0), llm)

    assert sent == 1
    assert len(scheduler_store["sent"]) == 1
    rec = scheduler_store["sent"][0]
    assert rec["chat_id"] == 555  # = external_user_id
    assert rec["token"] == "TOK-astro"  # токен вертикали
    assert "Лови день" in rec["text"]
    assert llm.closed is True  # клиент закрыт после батча


def test_tick_marks_last_sent(scheduler_store: dict[str, Any]) -> None:
    uid = _seed(scheduler_store, chat_id=1, agent_card={"birth_date": "01.01.1990"})
    _run_tick(scheduler_store, _msk(10, 0), _StubLLM())
    assert scheduler_store["recipients"][uid][AGENT_CARD_DAILY_FORECAST_LAST_SENT] == "2026-07-29"


def test_tick_idempotent_two_ticks_one_send(scheduler_store: dict[str, Any]) -> None:
    _seed(scheduler_store, chat_id=7, agent_card={"birth_date": "01.01.1990"})
    n1 = _run_tick(scheduler_store, _msk(10, 0), _StubLLM())
    n2 = _run_tick(scheduler_store, _msk(10, 1), _StubLLM())  # тот же день, +1 мин
    assert (n1, n2) == (1, 0)
    assert len(scheduler_store["sent"]) == 1


def test_tick_skips_disabled_and_not_due(scheduler_store: dict[str, Any]) -> None:
    _seed(
        scheduler_store, chat_id=1, agent_card={"birth_date": "x", "daily_forecast_enabled": False}
    )
    _seed(
        scheduler_store, chat_id=2, agent_card={"birth_date": "x", "daily_forecast_time": "12:00"}
    )
    sent = _run_tick(scheduler_store, _msk(10, 0), _StubLLM())  # 10:00 — второй ещё не «дозрел»
    assert sent == 0
    assert scheduler_store["sent"] == []


def test_tick_llm_failure_does_not_mark_last_sent(scheduler_store: dict[str, Any]) -> None:
    uid = _seed(scheduler_store, chat_id=9, agent_card={"birth_date": "x"})

    class _FailLLM:
        def complete(self, *a: Any, **k: Any) -> str:
            raise RuntimeError("down")

    sent = sch.run_daily_forecast_tick(
        now=_msk(10, 0),
        engine=_FakeEngine(scheduler_store),  # type: ignore[arg-type]
        token_map={"astrology": "TOK"},
        make_api=lambda token: _RecordingApi(token, scheduler_store["sent"]),  # type: ignore[arg-type,return-value]
        make_llm=lambda vid: _FailLLM(),
    )
    assert sent == 0
    assert scheduler_store["sent"] == []
    # last_sent НЕ проставлен — повторит в следующем тике в окне.
    assert AGENT_CARD_DAILY_FORECAST_LAST_SENT not in scheduler_store["recipients"][uid]


def test_tick_does_not_touch_quota(
    scheduler_store: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Рассылка бесплатна: QuotaService не инстанцируется в пути тика (баланс не трогаем)."""
    instantiated: list[Any] = []

    import mandala.services.quota as quota_mod

    class _SpyQuota:
        def __init__(self, *a: Any, **k: Any) -> None:
            instantiated.append(1)

    monkeypatch.setattr(quota_mod, "QuotaService", _SpyQuota)

    _seed(scheduler_store, chat_id=1, agent_card={"birth_date": "x"})
    _run_tick(scheduler_store, _msk(10, 0), _StubLLM())
    assert instantiated == []


def test_tick_bad_chat_id_skipped(scheduler_store: dict[str, Any]) -> None:
    uid = _seed(scheduler_store, chat_id=1, agent_card={"birth_date": "x"})
    scheduler_store["chat_ids"][uid] = "not-a-number"
    sent = _run_tick(scheduler_store, _msk(10, 0), _StubLLM())
    assert sent == 0


# --- глобальный рубильник + старт/стоп ----------------------------------------------


def test_globally_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANDALA_DAILY_FORECAST_ENABLED", raising=False)
    assert sch.daily_forecast_globally_enabled() is True
    monkeypatch.setenv("MANDALA_DAILY_FORECAST_ENABLED", "0")
    assert sch.daily_forecast_globally_enabled() is False
    monkeypatch.setenv("MANDALA_DAILY_FORECAST_ENABLED", "off")
    assert sch.daily_forecast_globally_enabled() is False
    monkeypatch.setenv("MANDALA_DAILY_FORECAST_ENABLED", "1")
    assert sch.daily_forecast_globally_enabled() is True


def test_start_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _main() -> None:
        monkeypatch.setenv("MANDALA_DAILY_FORECAST_ENABLED", "0")
        task = sch.start_daily_forecast_scheduler(lambda: None)  # type: ignore[arg-type,return-value]
        assert task is None
        await sch.stop_daily_forecast_scheduler(None)

    asyncio.run(_main())


def test_start_and_stop_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MANDALA_DAILY_FORECAST_ENABLED", "1")

    async def _main() -> None:
        task = sch.start_daily_forecast_scheduler(
            lambda: _FakeEngine({"recipients": {}})  # type: ignore[arg-type,return-value]
        )
        assert task is not None
        await asyncio.sleep(0)  # дать циклу стартовать
        await sch.stop_daily_forecast_scheduler(task)
        assert task.cancelled() or task.done()

    asyncio.run(_main())
