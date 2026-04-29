"""Минимальные тесты каркаса (тикет 1)."""

from __future__ import annotations

import mandala


def test_version() -> None:
    assert mandala.__version__ == "0.1.0"
