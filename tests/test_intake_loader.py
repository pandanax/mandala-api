"""Загрузка JSON шагов анкеты (тикет 13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mandala.verticals.intake_config import clear_intake_steps_cache, intake_steps_for_vertical
from mandala.verticals.intake_loader import bundled_intake_steps_path, load_intake_steps_registry


def test_bundled_json_loads_astrology_and_therapy() -> None:
    reg = load_intake_steps_registry(path=bundled_intake_steps_path())
    assert "astrology" in reg and "therapy" in reg
    assert [s.field_key for s in reg["astrology"]] == ["birth_place", "birth_time"]
    assert [s.field_key for s in reg["therapy"]] == ["main_concern", "mood"]


def test_unknown_validator_kind_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text(
        '{"v": [{"field_key": "x", "prompt": "y", "validator": {"kind": "unknown_xyz"}}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown validator"):
        load_intake_steps_registry(path=p)


def test_intake_steps_for_vertical_uses_cache_and_env_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = tmp_path / "custom.json"
    payload = {
        "demo": [
            {
                "field_key": "q",
                "prompt": "Q?",
                "validator": {"kind": "min_len", "min_len": 2, "label": "q"},
            }
        ]
    }
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    clear_intake_steps_cache()
    try:
        monkeypatch.setenv("MANDALA_INTAKE_STEPS_PATH", str(p))
        clear_intake_steps_cache()
        steps = intake_steps_for_vertical("demo")
        assert steps is not None and len(steps) == 1
        assert steps[0].prompt == "Q?"
        err = steps[0].validate("a")
        assert err is not None
        assert steps[0].validate("ab") is None
    finally:
        monkeypatch.delenv("MANDALA_INTAKE_STEPS_PATH", raising=False)
        clear_intake_steps_cache()
