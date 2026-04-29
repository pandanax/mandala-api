"""CLI: ``python -m mandala.index_kb --vertical <slug>`` (тикет 16)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from mandala.llm.config import LlmEnvSettings
from mandala.rag.chunking import chunk_text
from mandala.rag.config import RagEnvSettings
from mandala.rag.embeddings import OpenAICompatibleEmbeddingClient
from mandala.rag.kb_paths import default_kb_root, iter_kb_source_files, vertical_kb_dir
from mandala.rag.qdrant_store import QdrantVerticalKbStore

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Индексация файлов KB вертикали в Qdrant (чанки + эмбеддинги).",
    )
    parser.add_argument(
        "--vertical",
        required=True,
        help="slug вертикали; читается каталог kb/{vertical}/ (*.md, *.txt)",
    )
    parser.add_argument(
        "--kb-root",
        type=Path,
        default=None,
        help="Корень KB (подкаталоги = slug); иначе MANDALA_KB_ROOT или verticals/kb в пакете",
    )
    parser.add_argument(
        "--recreate-collection",
        action="store_true",
        help="Удалить коллекцию Qdrant и создать заново перед записью",
    )
    args = parser.parse_args()

    cfg = RagEnvSettings.from_env()
    if cfg.backend != "qdrant" or not cfg.qdrant_url.strip():
        print(
            "Нужны MANDALA_RAG_BACKEND=qdrant и непустой QDRANT_URL (см. README).",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        llm = LlmEnvSettings.from_env()
    except ValueError as e:
        print(f"LLM env для эмбеддингов: {e}", file=sys.stderr)
        sys.exit(2)

    kb_root = args.kb_root or cfg.kb_root or default_kb_root()
    vdir = vertical_kb_dir(args.vertical, kb_root=kb_root)
    files = iter_kb_source_files(vdir)
    if not files:
        print(f"Нет файлов .md/.txt в {vdir}", file=sys.stderr)
        sys.exit(1)

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
    if args.recreate_collection:
        store.recreate_collection()
    else:
        store.ensure_collection()

    total_points = 0
    for path in files:
        rel = str(path.relative_to(vdir)).replace("\\", "/")
        body = path.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_text(
            body,
            chunk_chars=cfg.chunk_chars,
            overlap=cfg.chunk_overlap,
        )
        n = store.upsert_chunks(
            vertical_id=args.vertical,
            source_path=rel,
            chunk_texts=chunks,
        )
        logger.info("indexed %s: %s chunks -> %s points", rel, len(chunks), n)
        total_points += n

    embed.close()
    qdrant.close()
    print(
        f"OK vertical={args.vertical} files={len(files)} "
        f"points={total_points} collection={cfg.collection}"
    )


if __name__ == "__main__":
    main()
