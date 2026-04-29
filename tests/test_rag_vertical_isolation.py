"""Смоук-тест изоляции RAG по ``vertical_id`` (тикет 16)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from mandala.rag.qdrant_store import QdrantVerticalKbStore


def test_search_by_vector_filters_vertical_id() -> None:
    """Чанки другой вертикали с тем же геометрическим вектором не попадают в выдачу."""
    qdrant = QdrantClient(":memory:")
    embed = MagicMock()
    store = QdrantVerticalKbStore(
        qdrant,
        collection="mandala_kb_isolation_test",
        vector_size=4,
        embed_client=embed,
    )
    store.ensure_collection()
    p1 = str(uuid.uuid4())
    p2 = str(uuid.uuid4())
    vec = [1.0, 0.0, 0.0, 0.0]
    qdrant.upsert(
        collection_name="mandala_kb_isolation_test",
        points=[
            PointStruct(
                id=p1,
                vector=vec,
                payload={"vertical_id": "astrology", "text": "ASTRO_ONLY"},
            ),
            PointStruct(
                id=p2,
                vector=vec,
                payload={"vertical_id": "therapy", "text": "THERAPY_ONLY"},
            ),
        ],
    )
    astro = store.search_by_vector(
        vertical_id="astrology",
        query_vector=vec,
        limit=10,
    )
    assert astro == ["ASTRO_ONLY"]
    therapy = store.search_by_vector(
        vertical_id="therapy",
        query_vector=vec,
        limit=10,
    )
    assert therapy == ["THERAPY_ONLY"]
    qdrant.close()
