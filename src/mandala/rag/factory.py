"""Сборка поиска KB из окружения (тикет 16)."""

from __future__ import annotations

import logging

from mandala.llm.config import LlmEnvSettings
from mandala.rag.config import RagEnvSettings
from mandala.rag.embeddings import OpenAICompatibleEmbeddingClient
from mandala.rag.protocol import KbSearchPort
from mandala.rag.qdrant_store import QdrantVerticalKbStore

logger = logging.getLogger(__name__)


class _QdrantKbSearchAdapter:
    __slots__ = ("_store",)

    def __init__(self, store: QdrantVerticalKbStore) -> None:
        self._store = store

    def search(self, *, vertical_id: str, query: str, limit: int) -> list[str]:
        return self._store.search_by_text(vertical_id=vertical_id, query=query, limit=limit)


def create_kb_search_from_env() -> KbSearchPort | None:
    """Если ``MANDALA_RAG_BACKEND=qdrant`` и задан ``QDRANT_URL`` — клиент Qdrant + эмбеддинги.

    Иначе ``None`` (текстовый ответ без RAG). Требуются валидные ``LLM_*`` для HTTP embeddings.
    """
    cfg = RagEnvSettings.from_env()
    if cfg.backend != "qdrant":
        return None
    if not cfg.qdrant_url.strip():
        logger.warning("MANDALA_RAG_BACKEND=qdrant but QDRANT_URL is empty; RAG disabled")
        return None
    try:
        llm = LlmEnvSettings.from_env()
    except ValueError as e:
        logger.warning("RAG needs LLM env for embeddings; disabled: %s", e)
        return None

    from qdrant_client import QdrantClient

    qdrant = QdrantClient(
        url=cfg.qdrant_url.strip(),
        api_key=cfg.qdrant_api_key.strip() or None,
    )
    embed = OpenAICompatibleEmbeddingClient(
        base_url=llm.base_url,
        api_key=llm.api_key,
        model=cfg.embedding_model,
    )
    store = QdrantVerticalKbStore(
        qdrant,
        collection=cfg.collection,
        vector_size=cfg.vector_size,
        embed_client=embed,
    )
    return _QdrantKbSearchAdapter(store)
