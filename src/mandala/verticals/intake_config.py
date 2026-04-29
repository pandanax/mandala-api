"""Публичная точка: цепочка шагов анкеты для ``vertical_id`` (данные из JSON).

Файл по умолчанию: ``mandala/verticals/intake_steps.json``; переопределение —
переменная окружения ``MANDALA_INTAKE_STEPS_PATH`` (как ``LLM_VERTICAL_OVERRIDES_PATH`` для LLM).

Роутинг «текст vs изображение» после анкеты — ``mandala.domain.handler`` и
``mandala.services.intent_router`` (тикет 14).
"""

from __future__ import annotations

from collections.abc import Sequence

from mandala.verticals.intake_loader import IntakeStep, load_intake_steps_registry

_registry: dict[str, tuple[IntakeStep, ...]] | None = None


def clear_intake_steps_cache() -> None:
    """Сбросить кэш реестра (тесты / смена JSON на лету)."""
    global _registry
    _registry = None


def intake_steps_for_vertical(vertical_id: str) -> Sequence[IntakeStep] | None:
    """Цепочка шагов для вертикали или ``None``, если анкета не задана (сразу диалог / LLM)."""
    global _registry
    if _registry is None:
        _registry = load_intake_steps_registry()
    reg = _registry
    steps = reg.get(vertical_id.strip())
    if not steps:
        return None
    return steps


__all__ = ["IntakeStep", "clear_intake_steps_cache", "intake_steps_for_vertical"]
