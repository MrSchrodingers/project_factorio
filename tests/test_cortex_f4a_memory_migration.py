from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from factorio_ai_lab.cortex.experiment_ledger import (
    ExecutiveExperimentLedger,
    build_episode_record,
    observed_episode_from_repair_row,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/run_cortex_f4a_memory_migration.py"


def _module():
    spec = importlib.util.spec_from_file_location("f4a_migration", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_executive_ledger(path: Path) -> None:
    row = {
        "run_id": "r1",
        "generation": 1,
        "stage": "Logistic science",
        "symptom": "fuel_starved:no_fuel",
        "action_key": "resupply:insert_fuel_from_world_container",
        "executed": True,
        "targets": ["u1"],
        "outcome": {
            "prediction": {
                "metric": "fuel_starved_entities",
                "direction": "decrease",
            },
            "before": 3.0,
            "after": 1.0,
            "verdict": "held",
            "reward": 1.0,
        },
    }
    episode = observed_episode_from_repair_row(
        row,
        source="runs/repairs.jsonl",
        source_sha256="repairs-sha",
        row_index=4,
    )
    with ExecutiveExperimentLedger(path) as ledger:
        assert ledger.append(build_episode_record(episode)) == "inserted"


def test_f4a_migration_separates_memory_types_and_provenance(
    tmp_path: Path,
) -> None:
    module = _module()
    runs = tmp_path / "runs"
    ledger_dir = runs / "ledger"
    ledger_dir.mkdir(parents=True)
    executive = ledger_dir / "cortex_executive_episodes.sqlite3"
    _write_executive_ledger(executive)

    knowledge = runs / "knowledge.jsonl"
    knowledge.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {
                    "at": "2026-09-25T00:00:00+00:00",
                    "stage": "smelting",
                    "lesson": "Iron output increased.",
                    "next_hypothesis": "Repeat the layout.",
                    "facts": {"output": 10},
                    "evidence_keys": ["output"],
                    "source": "llm_verified",
                    "verification": {"verified": True},
                },
                {
                    "at": "2026-09-25T01:00:00+00:00",
                    "stage": "smelting",
                    "lesson": "Iron output increased.",
                    "next_hypothesis": "Repeat the layout.",
                    "facts": {"output": 11},
                    "evidence_keys": ["output"],
                    "source": "llm_verified",
                    "verification": {"verified": True},
                },
                {
                    "at": "2026-09-25T02:00:00+00:00",
                    "stage": "mining",
                    "lesson": "Fallback fact.",
                    "next_hypothesis": "Retry.",
                    "facts": {"output": 2},
                    "source": "deterministic_fallback",
                    "verification": {"verified": True},
                },
                {
                    "stage": "ignored",
                    "lesson": "Unverified.",
                    "next_hypothesis": "Ignore.",
                    "verification": {"verified": False},
                },
            ]
        ),
        encoding="utf-8",
    )

    counterexamples = runs / "counterexamples.jsonl"
    counterexamples.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {
                    "at": "2026-09-25T00:00:00+00:00",
                    "run_id": "o1",
                    "phase": "route_buffer",
                    "stage": "Electric mining transition",
                    "signature": "same-signature",
                    "detail": "shortfall one",
                    "repair": {"deterministic_repairs": [{"reason": "x"}]},
                },
                {
                    "at": "2026-09-25T01:00:00+00:00",
                    "run_id": "o2",
                    "phase": "route_buffer",
                    "stage": "Electric mining transition",
                    "signature": "same-signature",
                    "detail": "shortfall two",
                    "repair": {"deterministic_repairs": [{"reason": "x"}]},
                },
            ]
        ),
        encoding="utf-8",
    )

    memory = ledger_dir / "cortex_cognitive_memory.sqlite3"
    revision = {"commit": "sha", "branch": "research/cortex-v1", "dirty": False}
    payload = module.build_migration(
        root=tmp_path,
        executive_ledger_path=executive,
        knowledge_path=knowledge,
        counterexamples_path=counterexamples,
        memory_path=memory,
        revision=revision,
    )

    assert payload["status"] == "pass"
    snapshot = payload["store"]["snapshot"]
    assert snapshot["episodic"] == {
        "items": 1,
        "occurrences": 1,
        "qualified_occurrences": 1,
    }
    assert snapshot["procedural"] == {
        "items": 1,
        "occurrences": 1,
        "qualified_occurrences": 1,
    }
    assert snapshot["semantic"]["items"] == 2
    assert snapshot["semantic"]["occurrences"] == 3
    assert snapshot["semantic"]["qualified_occurrences"] == 2
    assert snapshot["counterexample"]["items"] == 1
    assert snapshot["counterexample"]["occurrences"] == 2
    assert payload["sources"]["knowledge"]["unverified_rows"] == 1
    assert payload["working_memory"]["evicted_oldest"] is True
    assert payload["store"]["procedures"][0]["support_count"] == 1
    assert (
        payload["store"]["procedures"][0]["confidence"]["method"]
        == "wilson_lower_95"
    )
    assert payload["store"]["batch_manifest"]["occurrence_count"] == 7
    assert payload["world_mutation"] is False
    assert payload["factorio_rcon_used"] is False
    assert payload["continuous_authority"] is False


def test_f4a_migration_is_idempotent_for_same_source_snapshot(
    tmp_path: Path,
) -> None:
    module = _module()
    runs = tmp_path / "runs"
    ledger_dir = runs / "ledger"
    ledger_dir.mkdir(parents=True)
    executive = ledger_dir / "cortex_executive_episodes.sqlite3"
    _write_executive_ledger(executive)
    knowledge = runs / "knowledge.jsonl"
    knowledge.write_text(
        json.dumps(
            {
                "stage": "s",
                "lesson": "l",
                "next_hypothesis": "h",
                "facts": {"x": 1},
                "evidence_keys": ["x"],
                "verification": {"verified": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    counterexamples = runs / "counterexamples.jsonl"
    counterexamples.write_text("", encoding="utf-8")
    memory = ledger_dir / "memory.sqlite3"
    revision = {"commit": "sha", "branch": "b", "dirty": False}

    first = module.build_migration(
        root=tmp_path,
        executive_ledger_path=executive,
        knowledge_path=knowledge,
        counterexamples_path=counterexamples,
        memory_path=memory,
        revision=revision,
    )
    second = module.build_migration(
        root=tmp_path,
        executive_ledger_path=executive,
        knowledge_path=knowledge,
        counterexamples_path=counterexamples,
        memory_path=memory,
        revision=revision,
    )

    assert first["run_id"] == second["run_id"]
    assert first["store"]["batch_manifest"] == second["store"]["batch_manifest"]
    assert second["store"]["total_inserted"] == 0
    assert second["store"]["total_already_present"] == 3


def test_f4a_migration_refuses_dirty_revision(tmp_path: Path) -> None:
    module = _module()
    with pytest.raises(RuntimeError, match="clean source tree"):
        module.build_migration(
            root=tmp_path,
            executive_ledger_path=tmp_path / "a",
            knowledge_path=tmp_path / "b",
            counterexamples_path=tmp_path / "c",
            memory_path=tmp_path / "d",
            revision={"commit": "sha", "branch": "b", "dirty": True},
        )


def test_f4a_artifact_write_is_fail_closed(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "artifact.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already exists"):
        module._write_artifact(path, {"status": "pass"})
