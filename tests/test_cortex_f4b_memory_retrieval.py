from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from factorio_ai_lab.cortex.memory import (
    CognitiveMemoryStore,
    MemoryItem,
    MemoryKind,
    MemoryOccurrence,
    ValidityScope,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/run_cortex_f4b_memory_retrieval.py"


def _module():
    spec = importlib.util.spec_from_file_location("f4b_replay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _occurrence(
    item: MemoryItem,
    *,
    locator: str,
    reward: float | None = None,
) -> MemoryOccurrence:
    return MemoryOccurrence(
        memory_id=item.memory_id,
        source="fixture",
        source_sha256="fixture-sha",
        source_locator=locator,
        observed_at="2026-01-01T00:00:00+00:00",
        payload={"locator": locator},
        batch_id="fixture-batch",
        qualified=True,
        reward=reward,
    )


def _build_memory(path: Path) -> None:
    output = MemoryItem(
        kind=MemoryKind.SEMANTIC,
        key="smelting-output",
        content={
            "lesson": "The probe furnace is actively smelting iron plates.",
            "next_hypothesis": "Increase iron plate output.",
        },
        scope=ValidityScope(stage="smelting_probe"),
    )
    error = MemoryItem(
        kind=MemoryKind.SEMANTIC,
        key="placement-error",
        content={
            "lesson": "Entity placement error.",
            "next_hypothesis": "Check existing entities at target position.",
        },
        scope=ValidityScope(stage="smelting_probe"),
    )
    fuel_semantic = MemoryItem(
        kind=MemoryKind.SEMANTIC,
        key="fuel-theory",
        content={"lesson": "Fuel is required by burner machines."},
    )
    procedure = MemoryItem(
        kind=MemoryKind.PROCEDURAL,
        key="fuel_starved:no_fuel|resupply:insert_fuel_from_world_container",
        content={
            "action_key": "resupply:insert_fuel_from_world_container",
        },
        scope=ValidityScope(symptom="fuel_starved:no_fuel"),
    )
    counter = MemoryItem(
        kind=MemoryKind.COUNTEREXAMPLE,
        key="route-shortfall",
        content={"signature": "route-shortfall"},
        scope=ValidityScope(
            stage="Electric mining transition",
            phase="route_buffer",
        ),
    )
    episode = MemoryItem(
        kind=MemoryKind.EPISODIC,
        key="episode-1",
        content={"action_key": "resupply"},
    )

    with CognitiveMemoryStore(path) as store:
        for index in range(3):
            store.ingest(
                output,
                _occurrence(output, locator=f"output:{index}"),
            )
        store.ingest(error, _occurrence(error, locator="error:0"))
        for index in range(4):
            store.ingest(
                fuel_semantic,
                _occurrence(fuel_semantic, locator=f"fuel:{index}"),
            )
        store.ingest(
            procedure,
            _occurrence(procedure, locator="procedure:0", reward=1.0),
        )
        store.ingest(
            procedure,
            _occurrence(procedure, locator="procedure:1", reward=0.0),
        )
        store.refresh_procedural_confidence(procedure.memory_id)
        store.ingest(counter, _occurrence(counter, locator="counter:0"))
        store.ingest(counter, _occurrence(counter, locator="counter:1"))
        store.ingest(episode, _occurrence(episode, locator="episode:0"))


def test_f4b_replay_is_hybrid_auditable_and_read_only(tmp_path: Path) -> None:
    module = _module()
    memory = tmp_path / "runs" / "ledger" / "cortex_cognitive_memory.sqlite3"
    memory.parent.mkdir(parents=True)
    _build_memory(memory)
    f4a = tmp_path / "runs" / "audits" / "cortex_f4a_memory_substrate_migration.json"
    f4a.parent.mkdir(parents=True)
    f4a.write_text('{"status":"pass"}\n', encoding="utf-8")

    payload = module.build_replay(
        root=tmp_path,
        memory_path=memory,
        revision={
            "commit": "sha",
            "branch": "research/cortex-v1",
            "dirty": False,
        },
    )

    assert payload["status"] == "pass"
    assert payload["source"]["database_before"] == payload["source"]["database_after"]
    retrievals = payload["retrievals"]
    output = retrievals["semantic-smelting-output"]["results"][0]
    error = retrievals["semantic-smelting-placement-error"]["results"][0]
    procedure = retrievals["cross-kind-fuel-procedure"]["results"][0]
    counters = retrievals["counterexample-electric-route-buffer"]["results"]
    assert output["memory_id"] != error["memory_id"]
    assert output["scope"]["stage"] == "smelting_probe"
    assert error["scope"]["stage"] == "smelting_probe"
    assert procedure["kind"] == "procedural"
    assert procedure["scope"]["symptom"] == "fuel_starved:no_fuel"
    assert all(
        row["scope"]["stage"] == "Electric mining transition"
        and row["scope"]["phase"] == "route_buffer"
        for row in counters
    )
    assert payload["consolidation"]["repeated_semantic_items"] >= 1
    assert payload["consolidation"]["repeated_counterexample_items"] >= 1
    assert payload["consolidation"]["procedural_confidence_items"] == 1
    assert (
        payload["decay_probe"]["low_support"]["weight"]
        < payload["decay_probe"]["high_support"]["weight"]
    )
    assert payload["world_mutation"] is False
    assert payload["factorio_rcon_used"] is False
    assert payload["continuous_authority"] is False


def test_f4b_replay_refuses_dirty_revision(tmp_path: Path) -> None:
    module = _module()
    with pytest.raises(RuntimeError, match="clean source tree"):
        module.build_replay(
            root=tmp_path,
            memory_path=tmp_path / "memory.sqlite3",
            revision={"commit": "sha", "branch": "b", "dirty": True},
        )


def test_f4b_artifact_write_is_fail_closed(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "artifact.json"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already exists"):
        module._write_artifact(path, {"status": "pass"})


def test_f4b_modules_have_no_live_execution_import() -> None:
    forbidden = {
        "factorio_ai_lab.cortex.option_execute",
        "factorio_ai_lab.cortex.structural_execute",
        "factorio_ai_lab.experiments.curriculum_runner",
        "factorio_ai_lab.integrations.fle",
        "factorio_rcon",
    }
    for path in (
        ROOT / "src/factorio_ai_lab/cortex/memory_retrieval.py",
        SCRIPT,
    ):
        tree = ast.parse(path.read_text(), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        assert not (imports & forbidden)
