"""Пути к сырью KB на диске (тикет 16)."""

from __future__ import annotations

from pathlib import Path

import mandala.verticals as verticals_pkg


def default_kb_root() -> Path:
    """Каталог ``verticals/kb`` внутри пакета ``mandala.verticals``."""
    return Path(verticals_pkg.__file__).resolve().parent / "kb"


def vertical_kb_dir(vertical_id: str, *, kb_root: Path | None = None) -> Path:
    """``{kb_root}/{vertical_id}`` — в ``kb_root`` лежат подкаталоги по slug вертикали."""
    base = kb_root if kb_root is not None else default_kb_root()
    return (base / vertical_id.strip()).resolve()


def iter_kb_source_files(vertical_dir: Path) -> list[Path]:
    """Рекурсивно собрать ``.md`` и ``.txt``."""
    if not vertical_dir.is_dir():
        return []
    out: list[Path] = []
    for pattern in ("**/*.md", "**/*.txt"):
        for p in vertical_dir.glob(pattern):
            if p.is_file():
                out.append(p)
    return sorted(out)
