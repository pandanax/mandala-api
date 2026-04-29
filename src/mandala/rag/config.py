"""Переменные окружения для RAG и Qdrant (тикет 16)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field

_ENV_BACKEND = "MANDALA_RAG_BACKEND"
_ENV_QDRANT_URL = "QDRANT_URL"
_ENV_QDRANT_API_KEY = "QDRANT_API_KEY"
_ENV_COLLECTION = "MANDALA_QDRANT_COLLECTION"
_ENV_KB_ROOT = "MANDALA_KB_ROOT"
_ENV_TOP_K = "RAG_TOP_K"
_ENV_MAX_CTX = "RAG_MAX_CONTEXT_CHARS"
_ENV_CHUNK = "RAG_CHUNK_CHARS"
_ENV_OVERLAP = "RAG_CHUNK_OVERLAP"
_ENV_EMBED_MODEL = "LLM_EMBEDDING_MODEL"
_ENV_VECTOR_SIZE = "RAG_VECTOR_SIZE"


class RagEnvSettings(BaseModel):
    """Настройки RAG из окружения."""

    backend: str = Field(default="none", description="none | qdrant")
    qdrant_url: str = Field(default="", description="URL Qdrant, например http://localhost:6333")
    qdrant_api_key: str = Field(default="", description="Опционально для managed Qdrant")
    collection: str = Field(default="mandala_kb", min_length=1)
    kb_root: Path | None = Field(
        default=None,
        description="Корень каталогов KB; иначе пакет verticals/kb",
    )
    top_k: int = Field(default=5, ge=1, le=50)
    max_context_chars: int = Field(default=8000, ge=500, le=100_000)
    chunk_chars: int = Field(default=1200, ge=200, le=50_000)
    chunk_overlap: int = Field(default=200, ge=0, le=10_000)
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    vector_size: int = Field(default=1536, ge=2, le=16_384)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> RagEnvSettings:
        env = dict(environ if environ is not None else os.environ)
        kb_raw = env.get(_ENV_KB_ROOT, "").strip()
        embed_raw = env.get(_ENV_EMBED_MODEL, "text-embedding-3-small").strip()
        embedding_model = embed_raw or "text-embedding-3-small"
        return cls(
            backend=env.get(_ENV_BACKEND, "none").strip().lower() or "none",
            qdrant_url=env.get(_ENV_QDRANT_URL, "").strip(),
            qdrant_api_key=env.get(_ENV_QDRANT_API_KEY, "").strip(),
            collection=env.get(_ENV_COLLECTION, "mandala_kb").strip() or "mandala_kb",
            kb_root=Path(kb_raw).expanduser() if kb_raw else None,
            top_k=int(env.get(_ENV_TOP_K, "5")),
            max_context_chars=int(env.get(_ENV_MAX_CTX, "8000")),
            chunk_chars=int(env.get(_ENV_CHUNK, "1200")),
            chunk_overlap=int(env.get(_ENV_OVERLAP, "200")),
            embedding_model=embedding_model,
            vector_size=int(env.get(_ENV_VECTOR_SIZE, "1536")),
        )
