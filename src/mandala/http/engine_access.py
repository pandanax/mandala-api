"""Общий SQLAlchemy engine для HTTP-слоя (избегает циклических импортов app ↔ web_chat)."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from mandala.db.engine import create_engine_from_env

_engine: Engine | None = None


def get_engine() -> Engine:
    """Получить или создать SQLAlchemy engine по окружению."""
    global _engine
    if _engine is None:
        _engine = create_engine_from_env()
    return _engine
