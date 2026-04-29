"""Хранение и поиск чанков в Qdrant с фильтром ``vertical_id`` (тикет 16)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from mandala.rag.embeddings import OpenAICompatibleEmbeddingClient


class QdrantVerticalKbStore:
    """Коллекция одна на деплой; изоляция вертикалей — payload + ``Filter`` при поиске."""

    __slots__ = ("_collection", "_embed", "_qdrant", "_vector_size")

    def __init__(
        self,
        qdrant: QdrantClient,
        *,
        collection: str,
        vector_size: int,
        embed_client: OpenAICompatibleEmbeddingClient,
    ) -> None:
        self._qdrant = qdrant
        self._collection = collection
        self._vector_size = vector_size
        self._embed = embed_client

    def ensure_collection(self) -> None:
        """Создать коллекцию, если отсутствует (cosine, размер из конфига)."""
        names = self._qdrant.get_collections().collections
        if any(c.name == self._collection for c in names):
            return
        self._qdrant.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=self._vector_size, distance=Distance.COSINE),
        )

    def recreate_collection(self) -> None:
        """Удалить коллекцию при наличии и создать заново."""
        names = {c.name for c in self._qdrant.get_collections().collections}
        if self._collection in names:
            self._qdrant.delete_collection(collection_name=self._collection)
        self.ensure_collection()

    def upsert_chunks(
        self,
        *,
        vertical_id: str,
        source_path: str,
        chunk_texts: Sequence[str],
        batch_size: int = 32,
    ) -> int:
        """Записать чанки одного файла; вернуть число точек."""
        vid = vertical_id.strip()
        texts = [t for t in chunk_texts if t.strip()]
        if not texts:
            return 0
        self.ensure_collection()
        total = 0
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            vectors = self._embed.embed_texts(batch)
            if len(vectors) != len(batch):
                msg = "embedding batch size mismatch"
                raise RuntimeError(msg)
            points: list[PointStruct] = []
            for local_idx, (vec, body) in enumerate(zip(vectors, batch, strict=True)):
                chunk_index = start + local_idx
                pid = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mandala-kb:{vid}:{source_path}:{chunk_index}",
                    )
                )
                points.append(
                    PointStruct(
                        id=pid,
                        vector=vec,
                        payload={
                            "vertical_id": vid,
                            "source_path": source_path,
                            "chunk_index": chunk_index,
                            "text": body,
                        },
                    )
                )
            self._qdrant.upsert(collection_name=self._collection, points=points)
            total += len(points)
        return total

    def search_by_text(self, *, vertical_id: str, query: str, limit: int) -> list[str]:
        """Эмбеддинг запроса + ANN с обязательным фильтром ``vertical_id``."""
        q = (query or "").strip()
        if not q:
            return []
        (qv,) = self._embed.embed_texts([q])
        flt = Filter(
            must=[
                FieldCondition(key="vertical_id", match=MatchValue(value=vertical_id.strip())),
            ],
        )
        resp = self._qdrant.query_points(
            collection_name=self._collection,
            query=qv,
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        out: list[str] = []
        for h in resp.points:
            pl = h.payload or {}
            t = pl.get("text")
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
        return out

    def search_by_vector(
        self,
        *,
        vertical_id: str,
        query_vector: Sequence[float],
        limit: int,
    ) -> list[str]:
        """Поиск по готовому вектору (юнит-тесты изоляции без вызова embeddings API)."""
        flt = Filter(
            must=[
                FieldCondition(key="vertical_id", match=MatchValue(value=vertical_id.strip())),
            ],
        )
        resp = self._qdrant.query_points(
            collection_name=self._collection,
            query=list(query_vector),
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        out: list[str] = []
        for h in resp.points:
            pl = h.payload or {}
            t = pl.get("text")
            if isinstance(t, str) and t.strip():
                out.append(t.strip())
        return out
