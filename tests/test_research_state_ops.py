from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_seed_protocol_is_frozen_and_disjoint() -> None:
    config = json.loads((ROOT / "configs" / "cortex_baseline_v1.json").read_text())
    exploratory = config["exploratory_seeds"]
    confirmatory = config["confirmatory_seeds"]

    assert len(exploratory) == 5
    assert len(confirmatory) == 10
    assert not set(exploratory) & set(confirmatory)
    assert config["rules"]["g37_is_excluded_from_confirmatory_baseline"] is True


def test_selection_reset_is_dry_run_by_default(tmp_path, capsys) -> None:
    module = _load_script("reset_selection_state.py")
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "evolution_champion.json").write_text(
        json.dumps({"generation": 37, "run_id": "g37"}) + "\n"
    )

    assert [p.name for p in module.plan(tmp_path)] == ["evolution_champion.json"]
    assert (runs / "evolution_champion.json").exists()


def test_reset_preserves_history_and_retires_selection_files(tmp_path, monkeypatch) -> None:
    module = _load_script("reset_selection_state.py")
    monkeypatch.setattr(module, "_evolution_active", lambda: False)
    monkeypatch.setattr(
        module,
        "code_revision",
        lambda: {"commit": "abc1234", "dirty": False},
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "evolution_champion.json").write_text(
        json.dumps({"generation": 37, "run_id": "g37", "selected_at": "then"}) + "\n"
    )
    (runs / "evolution_history.jsonl").write_text("{}\n")

    record = module.apply_reset(tmp_path, reason="test")

    assert not (runs / "evolution_champion.json").exists()
    assert (runs / "evolution_history.jsonl").exists()
    assert record["retired_champion"]["generation"] == 37
    assert (runs / "baseline_reset.json").exists()
