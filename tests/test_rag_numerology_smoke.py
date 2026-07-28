"""Офлайн smoke-тест RAG на реальной базе знаний нумерологии (по образцу matrix-smoke).

Проверяет, что реальные ``*.md`` из ``kb/astrology/numerology`` индексируются и что
тематические запросы (жизненный путь, мастер-числа, число души) находят нужные разделы —
БЕЗ сети и внешнего эмбеддинг-API (детерминированный bag-of-words эмбеддер). Доказывает,
что извлечённый контекст реально доходит до system-промпта.
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
        collection="mandala_kb_num_smoke",
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


def test_numerology_kb_files_are_indexed() -> None:
    """Файлы нумерологии присутствуют в дереве KB (индексатор их подхватывает)."""
    vdir = vertical_kb_dir("astrology")
    rels = {str(p.relative_to(vdir)).replace("\\", "/") for p in iter_kb_source_files(vdir)}
    num = {r for r in rels if r.startswith("numerology/")}
    assert len(num) >= 5, f"ожидались файлы numerology/*.md, найдено: {num}"


def test_numerology_retrieval_feeds_prompt() -> None:
    """Тематические запросы попадают в нужные разделы KB нумерологии, блок непуст."""
    store = _build_store()
    n_points = _index_astrology_kb(store)
    assert n_points > 10

    life_path = store.search_by_text(
        vertical_id="astrology",
        query="число жизненного пути пифагорейская нумерология главный урок",
        limit=5,
    )
    joined = "\n".join(life_path).lower()
    assert "жизненн" in joined and "пут" in joined

    master = store.search_by_text(
        vertical_id="astrology",
        query="мастер-числа 11 22 33 интуиция мастер-строитель",
        limit=5,
    )
    assert any("мастер" in h.lower() for h in master)

    block = build_kb_context_block(life_path, max_chars=8000)
    assert block
    assert "базы знаний" in block.lower()


def test_numerology_and_matrix_coexist_in_same_vertical() -> None:
    """Нумерология и Матрица Судьбы живут в одной вертикали и не вытесняют друг друга."""
    store = _build_store()
    _index_astrology_kb(store)

    num = store.search_by_text(
        vertical_id="astrology",
        query="число души по гласным имени пифагор",
        limit=5,
    )
    assert any("душ" in h.lower() for h in num)

    matrix = store.search_by_text(
        vertical_id="astrology",
        query="матрица судьбы аркан зона комфорта октаграмма",
        limit=5,
    )
    assert any("аркан" in h.lower() or "матриц" in h.lower() for h in matrix)
