"""Food diary: structured estimation, capture state, persistence contract and reports."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

import mandala.services.food_diary as diary
from mandala.llm import TextCompletionClient
from mandala.repositories.food_diary import FoodDiaryEntry


def _entry(
    *,
    raw: str,
    eaten_at: datetime,
    kcal: float,
    protein: float = 10,
    fat: float = 5,
    carbs: float = 20,
) -> FoodDiaryEntry:
    uid = uuid4()
    return FoodDiaryEntry(
        id=uuid4(),
        user_id=uid,
        eaten_at=eaten_at,
        raw_text=raw,
        items=[],
        calories_kcal=kcal,
        protein_g=protein,
        fat_g=fat,
        carbs_g=carbs,
        confidence="medium",
        assumptions=[],
        created_at=eaten_at,
    )


def _ok_json() -> str:
    return """{
      "status": "ok",
      "items": [
        {"name":"гречка","amount":"200 г","calories_kcal":220,
         "protein_g":8,"fat_g":2,"carbs_g":44},
        {"name":"куриная грудка","amount":"150 г","calories_kcal":248,
         "protein_g":46,"fat_g":5,"carbs_g":0}
      ],
      "confidence":"high",
      "assumptions":["вес готовых продуктов"],
      "clarifying_question":null
    }"""


def test_parse_estimate_accepts_json_fence_and_validates_items() -> None:
    payload = diary.parse_meal_estimate(f"```json\n{_ok_json()}\n```")
    assert payload.status == "ok"
    assert len(payload.items) == 2
    assert sum(item.calories_kcal for item in payload.items) == 468


def test_parse_estimate_requires_real_clarification_question() -> None:
    raw = '{"status":"needs_clarification","items":[],"clarifying_question":null}'
    with pytest.raises(ValueError):
        diary.parse_meal_estimate(raw)


def test_parse_estimate_rejects_out_of_bounds_numbers() -> None:
    raw = (
        '{"status":"ok","items":[{"name":"еда","amount":"1",'
        '"calories_kcal":99999,"protein_g":1,"fat_g":1,"carbs_g":1}]}'
    )
    with pytest.raises(ValueError):
        diary.parse_meal_estimate(raw)


def test_diary_action_routes_commands_and_pending_plain_text_only() -> None:
    assert diary.is_food_diary_action("/meal", {})
    assert diary.is_food_diary_action("/meal@my_bot овсянка 200 г", {})
    assert diary.is_food_diary_action("mdl_nut:log:week", {})
    state = {diary.KEY_MEAL_CAPTURE: True}
    assert diary.is_food_diary_action("банан и йогурт", state)
    assert not diary.is_food_diary_action("/reset", state)
    assert not diary.is_food_diary_action("mdl_nut:plan", state)


def test_daily_and_weekly_render_use_moscow_calendar_days() -> None:
    # 21:30 UTC = 00:30 next day in Moscow.
    first = _entry(raw="каша", eaten_at=datetime(2026, 8, 30, 21, 30, tzinfo=UTC), kcal=300)
    second = _entry(raw="суп", eaten_at=datetime(2026, 8, 31, 9, 0, tzinfo=UTC), kcal=400)
    daily = diary.render_daily_log([first, second], date(2026, 8, 31))
    assert "00:30" in daily
    assert "12:00" in daily
    assert "700 ккал" in daily

    weekly = diary.render_weekly_log(
        [first, second],
        start_day=date(2026, 8, 25),
        end_day=date(2026, 8, 31),
    )
    assert "31.08" in weekly
    assert "2 зап." in weekly
    assert "700 ккал" in weekly
    assert "дни без записей не означают отсутствие еды" in weekly


class _Profiles:
    patches: list[dict[str, Any]] = []

    def __init__(self, _conn: object) -> None:
        pass

    def merge_scenario_state(self, _uid: UUID, patch: dict[str, Any]) -> None:
        self.patches.append(dict(patch))


class _DiaryRepo:
    inserted: list[dict[str, Any]] = []
    listed: list[FoodDiaryEntry] = []

    def __init__(self, _conn: object) -> None:
        pass

    def insert(self, **kwargs: Any) -> UUID:
        self.inserted.append(dict(kwargs))
        return uuid4()

    def list_between(self, **_kwargs: Any) -> list[FoodDiaryEntry]:
        return list(self.listed)


class _Messages:
    inserted: list[dict[str, Any]] = []

    def __init__(self, _conn: object) -> None:
        pass

    def insert(self, **kwargs: Any) -> UUID:
        self.inserted.append(dict(kwargs))
        return uuid4()


class _Quota:
    can_calls = 0
    consume_calls = 0

    def __init__(self, _conn: object) -> None:
        pass

    def can_consume(self, **_kwargs: Any) -> bool:
        type(self).can_calls += 1
        return True

    def consume(self, **_kwargs: Any) -> object:
        type(self).consume_calls += 1
        return SimpleNamespace(allowed=True)


def _install_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    _Profiles.patches = []
    _DiaryRepo.inserted = []
    _DiaryRepo.listed = []
    _Messages.inserted = []
    _Quota.can_calls = 0
    _Quota.consume_calls = 0
    monkeypatch.setattr(diary, "ProfileRepository", _Profiles)
    monkeypatch.setattr(diary, "FoodDiaryRepository", _DiaryRepo)
    monkeypatch.setattr(diary, "MessageRepository", _Messages)
    monkeypatch.setattr(diary, "QuotaService", _Quota)


def test_capture_then_estimate_persists_validated_totals(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(monkeypatch)
    uid = uuid4()
    llm = MagicMock()
    llm.complete.return_value = _ok_json()

    prompt = diary.handle_food_diary_action(
        cast(Any, object()),
        user_id=uid,
        text="/meal",
        scenario_state={"intake_complete": True},
        agent_card={"age": "41"},
        llm_client=cast(TextCompletionClient, llm),
    )
    assert "Что вы съели" in (prompt[0].text or "")
    assert _Profiles.patches[-1][diary.KEY_MEAL_CAPTURE] is True

    out = diary.handle_food_diary_action(
        cast(Any, object()),
        user_id=uid,
        text="гречка 200 г и куриная грудка 150 г",
        scenario_state={"intake_complete": True, diary.KEY_MEAL_CAPTURE: True},
        agent_card={"age": "41"},
        llm_client=cast(TextCompletionClient, llm),
        now=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
    )

    assert "Записал в дневник" in (out[0].text or "")
    assert len(_DiaryRepo.inserted) == 1
    saved = _DiaryRepo.inserted[0]
    assert saved["calories_kcal"] == 468
    assert saved["protein_g"] == 54
    assert saved["confidence"] == "high"
    assert _Profiles.patches[-1][diary.KEY_MEAL_CAPTURE] is False
    assert len(_Messages.inserted) == 2
    assert _Quota.can_calls == 1
    assert _Quota.consume_calls == 1


def test_clarification_keeps_draft_and_does_not_save(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(monkeypatch)
    llm = MagicMock()
    llm.complete.return_value = (
        '{"status":"needs_clarification","items":[],"confidence":"low",'
        '"assumptions":[],"clarifying_question":"Какой был объём тарелки супа?"}'
    )
    out = diary.handle_food_diary_action(
        cast(Any, object()),
        user_id=uuid4(),
        text="суп",
        scenario_state={diary.KEY_MEAL_CAPTURE: True},
        agent_card={"age": "41"},
        llm_client=cast(TextCompletionClient, llm),
    )
    assert "объём" in (out[0].text or "")
    assert not _DiaryRepo.inserted
    assert _Profiles.patches[-1][diary.KEY_MEAL_CAPTURE] is True
    assert _Profiles.patches[-1][diary.KEY_MEAL_DRAFT] == "суп"
    assert _Quota.consume_calls == 1


def test_reading_log_is_free_and_clears_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fakes(monkeypatch)
    _DiaryRepo.listed = [
        _entry(raw="яблоко", eaten_at=datetime(2026, 8, 31, 8, 0, tzinfo=UTC), kcal=80)
    ]
    out = diary.handle_food_diary_action(
        cast(Any, object()),
        user_id=uuid4(),
        text="/foodlog",
        scenario_state={diary.KEY_MEAL_CAPTURE: True},
        agent_card={"age": "41"},
        now=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
    )
    assert "яблоко" in (out[0].text or "")
    assert _Quota.can_calls == 0
    assert _Quota.consume_calls == 0
    assert _Profiles.patches[-1][diary.KEY_MEAL_CAPTURE] is False


def test_domain_routes_diary_before_conversational_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    import mandala.domain.handler as handler_mod
    from mandala.domain import InboundEvent, OutboundMessage

    uid = uuid4()

    class _HandlerProfiles:
        def __init__(self, _conn: object) -> None:
            pass

        def ensure_row(self, **_kwargs: object) -> None:
            return None

        def get_by_user_id(self, _uid: UUID) -> object:
            return SimpleNamespace(
                agent_card={"age": "41"},
                scenario_state={"intake_complete": True},
            )

    class _Identity:
        def __init__(self, _conn: object) -> None:
            pass

        def get_or_create_user(self, **_kwargs: object) -> UUID:
            return uid

    monkeypatch.setattr(handler_mod, "ProfileRepository", _HandlerProfiles)
    monkeypatch.setattr(handler_mod, "UserIdentityService", _Identity)
    monkeypatch.setattr(handler_mod, "handle_intake_before_llm", lambda *a, **kw: None)
    monkeypatch.setattr(handler_mod, "is_food_diary_action", lambda *a, **kw: True)
    monkeypatch.setattr(
        handler_mod,
        "handle_food_diary_action",
        lambda *a, **kw: [OutboundMessage(text="diary routed")],
    )
    monkeypatch.setattr(
        handler_mod,
        "handle_inbound_text_llm",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("diary leaked into chat LLM")),
    )

    out = handler_mod.handle_inbound(
        InboundEvent(
            vertical_id="nutrition",
            channel="telegram",
            external_user_id="42",
            text="/meal",
        ),
        cast(Any, object()),
    )
    assert out[0].text == "diary routed"
    assert out[0].buttons, "ensure_nav must preserve navigation on diary responses"
