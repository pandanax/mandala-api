"""Nutrition food diary: capture, validated LLM estimate, persistence and reports."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import Connection

from mandala.domain import OutboundMessage
from mandala.llm import ChatMessage, TextCompletionClient
from mandala.llm.factory import create_text_client_for_vertical
from mandala.repositories import FoodDiaryEntry, FoodDiaryRepository, MessageRepository
from mandala.repositories.profiles import ProfileRepository
from mandala.services.nutrition_safety import triage_nutrition
from mandala.services.quota import RESOURCE_TEXT_REPLY, QuotaService
from mandala.services.telegram_stars import build_packs_picker_message

logger = logging.getLogger(__name__)

FOOD_DIARY_TZ = ZoneInfo("Europe/Moscow")
KEY_MEAL_CAPTURE = "nutrition_meal_capture"
KEY_MEAL_DRAFT = "nutrition_meal_draft"
KEY_MEAL_EDIT_ID = "nutrition_meal_edit_id"

CMD_MEAL = "/meal"
CMD_FOOD_LOG = "/foodlog"
CMD_FOOD_WEEK = "/foodweek"
CMD_FOOD_EDIT = "/foodedit"
CMD_FOOD_DELETE = "/fooddelete"
CB_MEAL = "mdl_nut:meal"
CB_MEAL_CANCEL = "mdl_nut:meal:cancel"
CB_LOG_TODAY = "mdl_nut:log:today"
CB_LOG_WEEK = "mdl_nut:log:week"
CB_EDIT_PREFIX = "mdl_nut:fd:e:"
CB_DELETE_PREFIX = "mdl_nut:fd:d:"
CB_DELETE_CONFIRM_PREFIX = "mdl_nut:fd:dc:"

_DELETE_RE = re.compile(r"\b(?:удал\w*|убер\w*|сотр\w*)\b", re.IGNORECASE)
_EDIT_RE = re.compile(r"\b(?:измен\w*|исправ\w*|редакт\w*|пересчита\w*)\b", re.IGNORECASE)
_DIARY_NOUN_RE = re.compile(
    r"\b(?:запис\w*|позиц\w*|при[её]м\w*|завтрак\w*|обед\w*|ужин\w*|перекус\w*|дневник\w*)\b",
    re.IGNORECASE,
)
_INDEX_RE = re.compile(r"(?<!\d)(\d{1,3})(?:-?(?:й|я|е|ую|ое))?(?!\d)", re.IGNORECASE)

_MAX_MEAL_TEXT = 1200
_ESTIMATE_MAX_TOKENS = 4096
_QUOTA_MESSAGE = (
    "Для оценки КБЖУ нужно одно сообщение с баланса. Пополните баланс — сохранённый журнал "
    "и его просмотр останутся доступны бесплатно:"
)
_ESTIMATE_FAILURE = (
    "Не удалось надёжно оценить КБЖУ. Запись пока не сохранена — уточните продукты и примерные "
    "порции, например: «гречка 200 г, куриная грудка 150 г, салат без масла»."
)


class _EstimatedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=120)
    amount: str = Field(min_length=1, max_length=80)
    calories_kcal: float = Field(ge=0, le=5000)
    protein_g: float = Field(ge=0, le=500)
    fat_g: float = Field(ge=0, le=500)
    carbs_g: float = Field(ge=0, le=500)


class _EstimatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["ok", "needs_clarification"]
    items: list[_EstimatedItem] = Field(default_factory=list, max_length=20)
    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    clarifying_question: str | None = Field(default=None, max_length=500)


def _btn(label: str, callback_data: str) -> dict[str, str]:
    return {"text": label, "callback_data": callback_data}


def diary_nav_buttons() -> list[list[dict[str, str]]]:
    return [
        [_btn("➕ Записать еду", CB_MEAL), _btn("📒 Сегодня", CB_LOG_TODAY)],
        [_btn("📊 За 7 дней", CB_LOG_WEEK)],
    ]


def _capture_buttons() -> list[list[dict[str, str]]]:
    return [
        [_btn("✖️ Отмена", CB_MEAL_CANCEL)],
        [_btn("📒 Сегодня", CB_LOG_TODAY), _btn("📊 За 7 дней", CB_LOG_WEEK)],
    ]


def _entry_action_buttons(entries: list[FoodDiaryEntry]) -> list[list[dict[str, str]]]:
    return [
        [
            _btn(f"✏️ Изменить {index}", f"{CB_EDIT_PREFIX}{entry.id}"),
            _btn(f"🗑 Удалить {index}", f"{CB_DELETE_PREFIX}{entry.id}"),
        ]
        for index, entry in enumerate(entries, start=1)
    ]


def _log_buttons(entries: list[FoodDiaryEntry]) -> list[list[dict[str, str]]]:
    return _entry_action_buttons(entries) + diary_nav_buttons()


def _delete_confirmation(entry: FoodDiaryEntry) -> OutboundMessage:
    return OutboundMessage(
        text=(
            "🗑 **Удалить эту запись?**\n"
            f"{_safe_description(entry.raw_text)}\n"
            + _macro_line(entry.calories_kcal, entry.protein_g, entry.fat_g, entry.carbs_g)
        ),
        buttons=[
            [
                _btn("Удалить навсегда", f"{CB_DELETE_CONFIRM_PREFIX}{entry.id}"),
                _btn("Отмена", CB_LOG_TODAY),
            ]
        ],
    )


def _command_and_args(text: str | None) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return raw, ""
    head, _, args = raw.partition(" ")
    if "@" in head:
        head = head.split("@", 1)[0]
    return head.lower(), args.strip()


def _mutation_request(text: str | None) -> tuple[Literal["edit", "delete"], int | None] | None:
    raw = (text or "").strip()
    if not raw or raw.startswith("mdl"):
        return None
    action, args = _command_and_args(raw)
    if action in {CMD_FOOD_EDIT, CMD_FOOD_DELETE}:
        index_match = _INDEX_RE.search(args)
        return ("edit" if action == CMD_FOOD_EDIT else "delete", _index(index_match))
    intent: Literal["edit", "delete"] | None = None
    if _DELETE_RE.search(raw):
        intent = "delete"
    elif _EDIT_RE.search(raw):
        intent = "edit"
    if intent is None:
        return None
    index_match = _INDEX_RE.search(raw)
    if index_match is None and _DIARY_NOUN_RE.search(raw) is None:
        return None
    return intent, _index(index_match)


def _index(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _callback_entry_id(action: str, prefix: str) -> UUID | None:
    if not action.startswith(prefix):
        return None
    try:
        return UUID(action.removeprefix(prefix))
    except ValueError:
        return None


def is_food_diary_action(text: str | None, scenario_state: dict[str, Any]) -> bool:
    """Whether this turn belongs to the deterministic nutrition diary flow."""
    action, _args = _command_and_args(text)
    if action in {
        CMD_MEAL,
        CMD_FOOD_LOG,
        CMD_FOOD_WEEK,
        CMD_FOOD_EDIT,
        CMD_FOOD_DELETE,
        CB_MEAL,
        CB_MEAL_CANCEL,
        CB_LOG_TODAY,
        CB_LOG_WEEK,
    }:
        return True
    if action.startswith((CB_EDIT_PREFIX, CB_DELETE_PREFIX, CB_DELETE_CONFIRM_PREFIX)):
        return True
    if _mutation_request(text) is not None:
        return True
    if not bool(scenario_state.get(KEY_MEAL_CAPTURE)):
        return False
    raw = (text or "").strip()
    # Service commands and unrelated callbacks keep their normal routing even while capture waits.
    return bool(raw and not raw.startswith("/") and not raw.startswith("mdl"))


def handle_food_diary_action(
    conn: Connection,
    *,
    user_id: UUID,
    text: str | None,
    scenario_state: dict[str, Any],
    agent_card: dict[str, Any],
    llm_client: TextCompletionClient | None = None,
    now: datetime | None = None,
) -> list[OutboundMessage]:
    """Handle one diary command/callback/capture turn inside the current DB transaction."""
    action, args = _command_and_args(text)
    profiles = ProfileRepository(conn)
    current = now or datetime.now(tz=UTC)

    if action.startswith(CB_DELETE_CONFIRM_PREFIX):
        entry_id = _callback_entry_id(action, CB_DELETE_CONFIRM_PREFIX)
        deleted = bool(
            entry_id is not None
            and FoodDiaryRepository(conn).delete(user_id=user_id, entry_id=entry_id)
        )
        _clear_capture(profiles, user_id)
        notice = (
            "✅ Запись удалена из дневника." if deleted else "Запись уже удалена или не найдена."
        )
        return [_today_message(conn, user_id=user_id, now=current, notice=notice)]

    if action.startswith(CB_DELETE_PREFIX):
        entry_id = _callback_entry_id(action, CB_DELETE_PREFIX)
        entry = (
            FoodDiaryRepository(conn).get_by_id(user_id=user_id, entry_id=entry_id)
            if entry_id is not None
            else None
        )
        if entry is None:
            return [
                _today_message(
                    conn,
                    user_id=user_id,
                    now=current,
                    notice="Запись уже удалена или не найдена.",
                )
            ]
        return [_delete_confirmation(entry)]

    if action.startswith(CB_EDIT_PREFIX):
        entry_id = _callback_entry_id(action, CB_EDIT_PREFIX)
        entry = (
            FoodDiaryRepository(conn).get_by_id(user_id=user_id, entry_id=entry_id)
            if entry_id is not None
            else None
        )
        if entry is None:
            return [
                _today_message(
                    conn,
                    user_id=user_id,
                    now=current,
                    notice="Запись уже удалена или не найдена.",
                )
            ]
        profiles.merge_scenario_state(
            user_id,
            {KEY_MEAL_CAPTURE: True, KEY_MEAL_DRAFT: "", KEY_MEAL_EDIT_ID: str(entry.id)},
        )
        return [
            OutboundMessage(
                text=(
                    "✏️ **Изменение записи**\n"
                    f"Сейчас: {_safe_description(entry.raw_text)}\n\n"
                    "Отправьте полное исправленное описание еды и порций. "
                    "Я заново оценю КБЖУ и обновлю эту же запись."
                ),
                buttons=_capture_buttons(),
            )
        ]

    if action in {CMD_FOOD_LOG, CB_LOG_TODAY}:
        _clear_capture(profiles, user_id)
        return [_today_message(conn, user_id=user_id, now=current)]
    if action in {CMD_FOOD_WEEK, CB_LOG_WEEK}:
        _clear_capture(profiles, user_id)
        return [_week_message(conn, user_id=user_id, now=current)]
    if action == CB_MEAL_CANCEL:
        _clear_capture(profiles, user_id)
        return [
            OutboundMessage(
                text="Хорошо, запись отменена.",
                buttons=diary_nav_buttons(),
            )
        ]
    if action in {CMD_MEAL, CB_MEAL} and not args:
        profiles.merge_scenario_state(
            user_id,
            {KEY_MEAL_CAPTURE: True, KEY_MEAL_DRAFT: "", KEY_MEAL_EDIT_ID: ""},
        )
        return [
            OutboundMessage(
                text=(
                    "Что вы съели? Напишите продукты и примерные порции одним сообщением.\n\n"
                    "Например: «овсянка 250 г, банан, кофе с 100 мл молока»."
                ),
                buttons=_capture_buttons(),
            )
        ]

    mutation = _mutation_request(text)
    if mutation is not None and (
        not bool(scenario_state.get(KEY_MEAL_CAPTURE)) or action in {CMD_FOOD_EDIT, CMD_FOOD_DELETE}
    ):
        intent, index = mutation
        entries = _today_entries(conn, user_id=user_id, now=current)
        if index is None or index > len(entries):
            instruction = (
                "Не смог определить номер записи. Выберите нужную позицию кнопкой ниже."
                if entries
                else "В сегодняшнем дневнике пока нет записей для изменения."
            )
            return [_today_message(conn, user_id=user_id, now=current, notice=instruction)]
        entry = entries[index - 1]
        callback = (
            f"{CB_EDIT_PREFIX}{entry.id}" if intent == "edit" else f"{CB_DELETE_PREFIX}{entry.id}"
        )
        return handle_food_diary_action(
            conn,
            user_id=user_id,
            text=callback,
            scenario_state=scenario_state,
            agent_card=agent_card,
            llm_client=llm_client,
            now=current,
        )

    meal_text = args if action == CMD_MEAL else (text or "").strip()
    previous = str(scenario_state.get(KEY_MEAL_DRAFT) or "").strip()
    estimate_text = _merge_capture_text(previous, meal_text)
    edit_state = scenario_state.get(KEY_MEAL_EDIT_ID)
    edit_id = None if action == CMD_MEAL else _state_entry_id(edit_state)
    if action != CMD_MEAL and edit_state and edit_id is None:
        _clear_capture(profiles, user_id)
        return [
            _today_message(
                conn,
                user_id=user_id,
                now=current,
                notice="Не удалось определить редактируемую запись; изменения не сохранены.",
            )
        ]
    return _estimate_and_record(
        conn,
        user_id=user_id,
        raw_user_text=meal_text,
        estimate_text=estimate_text,
        agent_card=agent_card,
        llm_client=llm_client,
        now=current,
        edit_id=edit_id,
    )


def _estimate_and_record(
    conn: Connection,
    *,
    user_id: UUID,
    raw_user_text: str,
    estimate_text: str,
    agent_card: dict[str, Any],
    llm_client: TextCompletionClient | None,
    now: datetime,
    edit_id: UUID | None,
) -> list[OutboundMessage]:
    profiles = ProfileRepository(conn)
    clean = " ".join(raw_user_text.split())
    if len(clean) < 2 or len(clean) > _MAX_MEAL_TEXT:
        return [
            OutboundMessage(
                text=(
                    f"Опишите еду текстом от 2 до {_MAX_MEAL_TEXT} символов и укажите "
                    "примерные порции."
                ),
                buttons=_capture_buttons(),
            )
        ]

    repo = FoodDiaryRepository(conn)
    if edit_id is not None and repo.get_by_id(user_id=user_id, entry_id=edit_id) is None:
        _clear_capture(profiles, user_id)
        return [
            _today_message(
                conn,
                user_id=user_id,
                now=now,
                notice="Запись уже удалена или не найдена; изменения не сохранены.",
            )
        ]

    verdict = triage_nutrition(clean, agent_card)
    if verdict.level == "refer":
        _clear_capture(profiles, user_id)
        return [OutboundMessage(text=verdict.response, buttons=diary_nav_buttons())]

    quota = QuotaService(conn)
    if not quota.can_consume(
        user_id=user_id,
        vertical_id="nutrition",
        resource=RESOURCE_TEXT_REPLY,
    ):
        return [build_packs_picker_message(text=_QUOTA_MESSAGE)]

    owned = llm_client is None
    client = llm_client or create_text_client_for_vertical("nutrition")
    try:
        raw_reply = client.complete(
            _estimate_prompt(estimate_text),
            temperature=0,
            max_tokens=_ESTIMATE_MAX_TOKENS,
        )
    except Exception:  # noqa: BLE001 — provider errors degrade without losing the draft
        logger.warning("food diary estimate failed user_id=%s", user_id, exc_info=True)
        return [OutboundMessage(text=_ESTIMATE_FAILURE, buttons=_capture_buttons())]
    finally:
        if owned:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    try:
        payload = parse_meal_estimate(raw_reply)
    except (ValueError, ValidationError):
        logger.warning("food diary estimate returned invalid payload user_id=%s", user_id)
        return [OutboundMessage(text=_ESTIMATE_FAILURE, buttons=_capture_buttons())]

    messages = MessageRepository(conn)
    if payload.status == "needs_clarification":
        question = (payload.clarifying_question or "").strip()
        if not question:
            question = "Уточните, пожалуйста, примерный вес или объём порции."
        profiles.merge_scenario_state(
            user_id,
            {
                KEY_MEAL_CAPTURE: True,
                KEY_MEAL_DRAFT: estimate_text[:_MAX_MEAL_TEXT],
                KEY_MEAL_EDIT_ID: str(edit_id) if edit_id is not None else "",
            },
        )
        messages.insert(
            user_id=user_id,
            vertical_id="nutrition",
            role="user",
            content_text=clean,
            content_kind="text",
            content_meta={"food_diary": "clarification"},
        )
        messages.insert(
            user_id=user_id,
            vertical_id="nutrition",
            role="assistant",
            content_text=question,
            content_kind="text",
            content_meta={"food_diary": "clarification"},
        )
        quota.consume(user_id=user_id, vertical_id="nutrition", resource=RESOURCE_TEXT_REPLY)
        return [OutboundMessage(text=question, buttons=_capture_buttons())]

    items = [item.model_dump() for item in payload.items]
    totals = _totals(payload.items)
    assumptions = [str(item).strip() for item in payload.assumptions if str(item).strip()]
    if edit_id is None:
        repo.insert(
            user_id=user_id,
            eaten_at=_as_utc(now),
            raw_text=estimate_text[:_MAX_MEAL_TEXT],
            items=items,
            calories_kcal=totals[0],
            protein_g=totals[1],
            fat_g=totals[2],
            carbs_g=totals[3],
            confidence=payload.confidence,
            assumptions=assumptions,
        )
    elif not repo.update(
        user_id=user_id,
        entry_id=edit_id,
        raw_text=estimate_text[:_MAX_MEAL_TEXT],
        items=items,
        calories_kcal=totals[0],
        protein_g=totals[1],
        fat_g=totals[2],
        carbs_g=totals[3],
        confidence=payload.confidence,
        assumptions=assumptions,
    ):
        _clear_capture(profiles, user_id)
        return [
            _today_message(
                conn,
                user_id=user_id,
                now=now,
                notice="Запись уже удалена или не найдена; изменения не сохранены.",
            )
        ]
    _clear_capture(profiles, user_id)
    confirmation = _confirmation_text(estimate_text, payload, totals, updated=edit_id is not None)
    messages.insert(
        user_id=user_id,
        vertical_id="nutrition",
        role="user",
        content_text=clean,
        content_kind="text",
        content_meta={"food_diary": "meal"},
    )
    messages.insert(
        user_id=user_id,
        vertical_id="nutrition",
        role="assistant",
        content_text=confirmation,
        content_kind="text",
        content_meta={"food_diary": "estimate"},
    )
    quota.consume(user_id=user_id, vertical_id="nutrition", resource=RESOURCE_TEXT_REPLY)
    if edit_id is not None:
        return [_today_message(conn, user_id=user_id, now=now, notice=confirmation)]
    return [OutboundMessage(text=confirmation, buttons=diary_nav_buttons())]


def _estimate_prompt(description: str) -> list[ChatMessage]:
    system = (
        "Ты оцениваешь уже съеденную еду для личного пищевого дневника. Верни ТОЛЬКО один JSON "
        "объект без markdown, пояснений и служебных маркеров. Не рассчитывай суточную норму и не "
        "давай медицинских советов. Калории означают kcal. Для каждого продукта оцени КБЖУ. "
        "Если ключевой размер порции отсутствует и разумный диапазон изменит калорийность более "
        "чем примерно на 30%, верни status=needs_clarification и один короткий вопрос. Иначе явно "
        "перечисли допущения. Схема: "
        '{"status":"ok|needs_clarification","items":[{"name":"...","amount":"...",'
        '"calories_kcal":0,"protein_g":0,"fat_g":0,"carbs_g":0}],'
        '"confidence":"low|medium|high","assumptions":["..."],'
        '"clarifying_question":null}. Числа неотрицательные, items обязателен для status=ok.'
    )
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=description),
    ]


def parse_meal_estimate(raw: str) -> _EstimatePayload:
    """Parse and validate a model estimate; totals are always derived from validated items."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("estimate JSON object not found")
    value = json.loads(text[start : end + 1])
    payload = _EstimatePayload.model_validate(value)
    if payload.status == "ok" and not payload.items:
        raise ValueError("ok estimate has no items")
    if payload.status == "needs_clarification" and not (payload.clarifying_question or "").strip():
        raise ValueError("clarification has no question")
    calories, protein, fat, carbs = _totals(payload.items)
    if calories > 10000 or max(protein, fat, carbs) > 1000:
        raise ValueError("estimate total out of bounds")
    return payload


def _totals(items: list[_EstimatedItem]) -> tuple[float, float, float, float]:
    return tuple(  # type: ignore[return-value]
        round(sum(float(getattr(item, field)) for item in items), 2)
        for field in ("calories_kcal", "protein_g", "fat_g", "carbs_g")
    )


def _merge_capture_text(previous: str, current: str) -> str:
    """Join clarification text without duplicating an already-complete description."""
    old = " ".join(previous.split()).strip()
    new = " ".join(current.split()).strip()
    if not old:
        return new
    if not new:
        return old
    old_folded = old.casefold()
    new_folded = new.casefold()
    if old_folded in new_folded:
        return new
    if new_folded in old_folded:
        return old
    return f"{old}; {new}"


def _state_entry_id(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


def _clear_capture(profiles: ProfileRepository, user_id: UUID) -> None:
    profiles.merge_scenario_state(
        user_id,
        {KEY_MEAL_CAPTURE: False, KEY_MEAL_DRAFT: "", KEY_MEAL_EDIT_ID: ""},
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=FOOD_DIARY_TZ).astimezone(UTC)
    return value.astimezone(UTC)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=FOOD_DIARY_TZ)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _today_entries(conn: Connection, *, user_id: UUID, now: datetime) -> list[FoodDiaryEntry]:
    local_day = _as_utc(now).astimezone(FOOD_DIARY_TZ).date()
    start, end = _day_bounds(local_day)
    return FoodDiaryRepository(conn).list_between(user_id=user_id, start=start, end=end)


def _today_message(
    conn: Connection,
    *,
    user_id: UUID,
    now: datetime,
    notice: str | None = None,
) -> OutboundMessage:
    local_day = _as_utc(now).astimezone(FOOD_DIARY_TZ).date()
    entries = _today_entries(conn, user_id=user_id, now=now)
    report = render_daily_log(entries, local_day)
    text = f"{notice}\n\n{report}" if notice else report
    return OutboundMessage(text=text, buttons=_log_buttons(entries))


def _week_message(conn: Connection, *, user_id: UUID, now: datetime) -> OutboundMessage:
    end_day = _as_utc(now).astimezone(FOOD_DIARY_TZ).date()
    start_day = end_day - timedelta(days=6)
    start, _unused = _day_bounds(start_day)
    _end_start, end = _day_bounds(end_day)
    entries = FoodDiaryRepository(conn).list_between(user_id=user_id, start=start, end=end)
    return OutboundMessage(
        text=render_weekly_log(entries, start_day=start_day, end_day=end_day),
        buttons=diary_nav_buttons(),
    )


def _safe_description(value: str, limit: int = 140) -> str:
    clean = " ".join(value.replace("*", "").replace("_", "").split())
    return clean if len(clean) <= limit else f"{clean[: limit - 1].rstrip()}…"


def _macro_line(calories: float, protein: float, fat: float, carbs: float) -> str:
    return (
        f"≈ {_number(calories)} ккал · Б {_number(protein)} г · "
        f"Ж {_number(fat)} г · У {_number(carbs)} г"
    )


def _number(value: float) -> str:
    rounded = float(round(value, 1))
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.1f}"


def _sum_entries(entries: list[FoodDiaryEntry]) -> tuple[float, float, float, float]:
    return (
        sum(entry.calories_kcal for entry in entries),
        sum(entry.protein_g for entry in entries),
        sum(entry.fat_g for entry in entries),
        sum(entry.carbs_g for entry in entries),
    )


def _confirmation_text(
    raw_text: str,
    payload: _EstimatePayload,
    totals: tuple[float, float, float, float],
    *,
    updated: bool = False,
) -> str:
    title = "✅ **Запись обновлена**" if updated else "✅ **Записал в дневник**"
    lines = [title, _safe_description(raw_text), "", _macro_line(*totals)]
    assumptions = [str(item).strip() for item in payload.assumptions if str(item).strip()]
    if assumptions:
        lines.extend(["", "Допущения: " + "; ".join(assumptions[:3])])
    if payload.confidence == "low":
        lines.extend(["", "Точность низкая: порции описаны приблизительно."])
    lines.extend(["", "Калории и БЖУ — ориентировочная оценка, а не лабораторное измерение."])
    return "\n".join(lines)


def render_daily_log(entries: list[FoodDiaryEntry], day: date) -> str:
    lines = [f"📒 **Пищевой дневник за {day:%d.%m.%Y}**", ""]
    if not entries:
        return "\n".join(lines + ["Записей пока нет. Добавьте первый приём пищи кнопкой ниже."])
    for index, entry in enumerate(entries, start=1):
        local_time = entry.eaten_at.astimezone(FOOD_DIARY_TZ).strftime("%H:%M")
        lines.append(f"{index}. **{local_time}** — {_safe_description(entry.raw_text)}")
        lines.append(
            "   " + _macro_line(entry.calories_kcal, entry.protein_g, entry.fat_g, entry.carbs_g)
        )
    lines.extend(["", "**Итого:** " + _macro_line(*_sum_entries(entries))])
    lines.extend(["", "Все значения ориентировочные и зависят от фактических порций и состава."])
    return "\n".join(lines)


def render_weekly_log(
    entries: list[FoodDiaryEntry],
    *,
    start_day: date,
    end_day: date,
) -> str:
    lines = [f"📊 **Пищевой дневник: {start_day:%d.%m}–{end_day:%d.%m.%Y}**", ""]
    if not entries:
        return "\n".join(lines + ["За эти 7 дней записей пока нет."])
    grouped: dict[date, list[FoodDiaryEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.eaten_at.astimezone(FOOD_DIARY_TZ).date()].append(entry)
    for offset in range(7):
        day = start_day + timedelta(days=offset)
        day_entries = grouped.get(day, [])
        if not day_entries:
            lines.append(f"• **{day:%d.%m}** — нет записей")
            continue
        lines.append(
            f"• **{day:%d.%m}** — {len(day_entries)} зап. · "
            + _macro_line(*_sum_entries(day_entries))
        )
    totals = _sum_entries(entries)
    logged_days = max(len(grouped), 1)
    lines.extend(["", "**Итого за период:** " + _macro_line(*totals)])
    lines.append(f"**Среднее за день с записями:** ≈ {_number(totals[0] / logged_days)} ккал")
    lines.extend(["", "Все значения ориентировочные; дни без записей не означают отсутствие еды."])
    return "\n".join(lines)
