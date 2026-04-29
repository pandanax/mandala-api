"""Доступ к PostgreSQL (SQLAlchemy + psycopg), тикет 5."""

from mandala.db.engine import create_engine_from_env

__all__ = ["create_engine_from_env"]
