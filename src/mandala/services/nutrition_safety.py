"""Deterministic pre-LLM safety triage for the adult general-wellness nutrition bot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SafetyLevel = Literal["allowed", "limited", "refer"]


@dataclass(frozen=True, slots=True)
class NutritionSafetyVerdict:
    level: SafetyLevel
    reason: str
    response: str


_EMERGENCY = ("не могу дышать", "потеря сознания", "обморок", "кровь в рвоте", "анафилак")
_HARMFUL = (
    "вызвать рвоту",
    "вызывать рвоту",
    "слабительн",
    "не есть неделю",
    "голодать",
    "сухое голодание",
    "очищение организма",
    "10 кг за неделю",
    "экстремально похуд",
)
_REFER = (
    "беремен",
    "кормлю грудью",
    "лактац",
    "анорек",
    "булим",
    "рпп",
    "отмени лекар",
    "изменить дозу",
    "инсулин",
    "лечебная диета",
)
_LIMITED = ("аллерг", "диабет", "болезнь почек", "почечн", "хроническ", "лекарств")


def triage_nutrition(text: str, profile: dict[str, Any] | None = None) -> NutritionSafetyVerdict:
    profile_text = " ".join(str(v) for v in (profile or {}).values() if isinstance(v, str))
    haystack = f"{text} {profile_text}".lower()
    age = (profile or {}).get("age")
    if isinstance(age, str) and age.strip().isdigit() and int(age) < 18:
        return _refer(
            "minor",
            "Этот помощник предназначен только для взрослых 18+. Обсудите питание с "
            "родителем или законным представителем и педиатром.",
        )
    if any(x in haystack for x in _EMERGENCY):
        return _refer(
            "emergency",
            "Это может требовать срочной медицинской помощи. Позвоните 112 или немедленно "
            "обратитесь в ближайшее отделение неотложной помощи.",
        )
    if any(x in haystack for x in _HARMFUL):
        return _refer(
            "harmful_weight_control",
            "Я не помогаю с голоданием, рвотой, слабительными или экстремальным похудением — "
            "это опасно. Обратитесь к врачу или специалисту по расстройствам пищевого поведения.",
        )
    if any(x in haystack for x in _REFER):
        return _refer(
            "medical_scope",
            "Здесь нужен персональный совет врача или квалифицированного диетолога. Я не буду "
            "составлять лечебную диету, менять лекарства или дозировки; после консультации "
            "могу помочь с общими бытовыми привычками.",
        )
    if any(x in haystack for x in _LIMITED):
        return NutritionSafetyVerdict(
            "limited",
            "health_context",
            "Только общая образовательная информация; персональный план согласуйте с лечащим "
            "врачом или диетологом.",
        )
    return NutritionSafetyVerdict("allowed", "general_wellness", "")


def _refer(reason: str, response: str) -> NutritionSafetyVerdict:
    return NutritionSafetyVerdict("refer", reason, response)
