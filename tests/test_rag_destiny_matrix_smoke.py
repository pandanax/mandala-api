"""Офлайн smoke-тест RAG на реальной базе знаний «Карты судьбы» (Матрица Судьбы).

Проверяет весь конвейер поиска БЕЗ сети и внешнего эмбеддинг-API: реальные ``*.md``
из ``kb/astrology/destiny_matrix`` → чанкинг → индексация в Qdrant (``:memory:``) →
фильтр по ``vertical_id`` → сбор блока для system-промпта. Вместо сетевого эмбеддера —
детерминированный хеширующий bag-of-words (косинус растёт при пересечении слов), поэтому
запрос по теме находит релевантный чанк. Это доказывает, что извлечённый контекст реально
доходит до промпта (см. ``mandala.services.text_reply`` + ``build_kb_context_block``).
"""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Sequence

from qdrant_client import QdrantClient

from mandala.rag.chunking import chunk_text
from mandala.rag.kb_paths import iter_kb_source_files, vertical_kb_dir
from mandala.rag.prompt_injection import build_kb_context_block
from mandala.rag.qdrant_store import QdrantVerticalKbStore

_DIM = 512
_WORD_RE = re.compile(r"\w+", re.UNICODE)


class _HashingEmbedder:
    """Детерминированный bag-of-words эмбеддер (без сети): хеш слова → индекс, L2-норма."""

    def embed_texts(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * _DIM
            for word in _WORD_RE.findall(text.lower()):
                vec[zlib.crc32(word.encode("utf-8")) % _DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


def _build_store() -> QdrantVerticalKbStore:
    store = QdrantVerticalKbStore(
        QdrantClient(":memory:"),
        collection="mandala_kb_dm_smoke",
        vector_size=_DIM,
        embed_client=_HashingEmbedder(),  # type: ignore[arg-type]
    )
    store.ensure_collection()
    return store


def _index_astrology_kb(store: QdrantVerticalKbStore) -> int:
    vdir = vertical_kb_dir("astrology")
    total = 0
    for path in iter_kb_source_files(vdir):
        body = path.read_text(encoding="utf-8")
        chunks = chunk_text(body, chunk_chars=1200, overlap=200)
        rel = str(path.relative_to(vdir)).replace("\\", "/")
        total += store.upsert_chunks(vertical_id="astrology", source_path=rel, chunk_texts=chunks)
    return total


def test_real_kb_indexes_and_retrieval_feeds_prompt() -> None:
    """Реальная KB индексируется, запрос находит релевантный чанк, блок для промпта непуст."""
    store = _build_store()
    n_points = _index_astrology_kb(store)
    assert n_points > 10, "ожидаем десятки чанков из реальной базы знаний"

    hits = store.search_by_text(
        vertical_id="astrology",
        query="что означает денежный канал и линия денег в матрице судьбы",
        limit=5,
    )
    assert hits, "поиск должен вернуть чанки"
    joined = "\n".join(hits).lower()
    assert "денеж" in joined or "деньг" in joined

    block = build_kb_context_block(hits, max_chars=8000)
    assert block  # непустой блок реально попадёт в system-промпт
    assert "базы знаний" in block.lower()


def test_retrieval_finds_specific_arcana_and_purpose() -> None:
    """Тематические запросы попадают в нужные разделы KB (арканы, предназначение)."""
    store = _build_store()
    _index_astrology_kb(store)

    fool = store.search_by_text(
        vertical_id="astrology",
        query="22 аркан Шут свобода спонтанность доверие",
        limit=5,
    )
    assert any("шут" in h.lower() for h in fool)

    purpose = store.search_by_text(
        vertical_id="astrology",
        query="личное социальное духовное предназначение небо земля",
        limit=5,
    )
    assert any("предназначен" in h.lower() for h in purpose)


def test_vertical_filter_excludes_other_verticals() -> None:
    """Чанк другой вертикали не попадает в выдачу astrology (изоляция RAG)."""
    store = _build_store()
    _index_astrology_kb(store)
    store.upsert_chunks(
        vertical_id="therapy",
        source_path="only.md",
        chunk_texts=["УНИКАЛЬНЫЙ_ТЕРАПИЯ_МАРКЕР про матрицу судьбы и предназначение"],
    )
    hits = store.search_by_text(
        vertical_id="astrology",
        query="матрица судьбы предназначение",
        limit=10,
    )
    assert all("УНИКАЛЬНЫЙ_ТЕРАПИЯ_МАРКЕР" not in h for h in hits)
