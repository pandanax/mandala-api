"""RAG: база знаний per ``vertical_id`` и векторный поиск вне OLTP Postgres (тикет 16)."""

from mandala.rag.factory import create_kb_search_from_env
from mandala.rag.protocol import KbSearchPort

__all__ = ["KbSearchPort", "create_kb_search_from_env"]
