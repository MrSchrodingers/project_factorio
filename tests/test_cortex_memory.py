from __future__ import annotations

import ast
from pathlib import Path

import pytest

from factorio_ai_lab.cortex.memory import (
    CONFIDENCE_NOT_ESTIMATED,
    CONFIDENCE_WILSON_LOWER_95,
    CognitiveMemoryStore,
    ConfidenceEstimate,
    MemoryConflict,
    MemoryItem,
    MemoryKind,
    MemoryOccurrence,
    ValidityScope,
    WorkingMemory,
    WorkingMemorySlot,
)

ROOT = Path(__file__).parents[1]


def _item(
    kind: MemoryKind = MemoryKind.SEMANTIC,
    *,
    key: str = "lesson",
) -> MemoryItem:
    return MemoryItem(
        kind=kind,
        key=key,
        content={"lesson": "measured fact"},
        scope=ValidityScope(stage="smelting"),
    )


def _occurrence(
    item: MemoryItem,
    *,
    locator: str = "row:1",
    qualified: bool = True,
    reward: float | None = None,
    payload: dict | None = None,
    batch_id: str = "batch-1",
) -> MemoryOccurrence:
    return MemoryOccurrence(
        memory_id=item.memory_id,
        source="runs/source.jsonl",
        source_sha256="source-sha",
        source_locator=locator,
        observed_at="2026-09-25T00:00:00+00:00",
        payload=payload or {"value": locator},
        batch_id=batch_id,
        qualified=qualified,
        reward=reward,
    )


def test_working_memory_is_bounded_and_overwrite_is_deterministic() -> None:
    memory = WorkingMemory(capacity=3)
    for key in ("a", "b", "c", "d"):
        memory = memory.remember(
            WorkingMemorySlot(key=key, value=key.upper(), source="test")
        )

    assert [slot.key for slot in memory.slots] == ["b", "c", "d"]
    assert memory.recall("a") is None
    assert memory.recall("d").value == "D"

    replaced = memory.remember(
        WorkingMemorySlot(key="c", value="new", source="updated")
    )
    assert [slot.key for slot in replaced.slots] == ["b", "d", "c"]
    assert replaced.recall("c").value == "new"
    assert replaced.to_dict()["persistent"] is False


def test_scope_and_memory_identity_are_stable() -> None:
    scope_a = ValidityScope(stage="smelting", tags=("iron", "factory", "iron"))
    scope_b = ValidityScope(stage="smelting", tags=("factory", "iron"))
    item_a = MemoryItem(
        kind=MemoryKind.SEMANTIC,
        key="k",
        content={"lesson": "x"},
        scope=scope_a,
    )
    item_b = MemoryItem(
        kind=MemoryKind.SEMANTIC,
        key="k",
        content={"lesson": "x"},
        scope=scope_b,
    )

    assert item_a.memory_id == item_b.memory_id
    assert item_a.item_digest == item_b.item_digest
    assert scope_a.tags == ("factory", "iron")


def test_store_is_restart_safe_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    item = _item()
    occurrence = _occurrence(item)

    with CognitiveMemoryStore(path) as store:
        assert store.ingest(item, occurrence) == "inserted"
        assert store.ingest(item, occurrence) == "already_present"
        assert store.quick_check() == "ok"
        assert store.item_count(MemoryKind.SEMANTIC) == 1
        assert store.occurrence_count(MemoryKind.SEMANTIC) == 1
        assert store.support_counts(item.memory_id) == {
            "support_count": 1,
            "qualified_support_count": 1,
            "contradiction_count": 0,
        }

    with CognitiveMemoryStore(path) as store:
        assert store.quick_check() == "ok"
        assert store.item_count() == 1
        assert store.occurrence_count() == 1


def test_same_source_locator_with_changed_payload_conflicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "memory.sqlite3"
    item = _item()

    with CognitiveMemoryStore(path) as store:
        store.ingest(item, _occurrence(item, payload={"value": 1}))
        with pytest.raises(MemoryConflict, match="content conflict"):
            store.ingest(item, _occurrence(item, payload={"value": 2}))


def test_semantic_support_tracks_unqualified_provenance_separately(
    tmp_path: Path,
) -> None:
    item = _item()
    with CognitiveMemoryStore(tmp_path / "memory.sqlite3") as store:
        store.ingest(item, _occurrence(item, locator="row:1", qualified=True))
        store.ingest(item, _occurrence(item, locator="row:2", qualified=False))

        counts = store.support_counts(item.memory_id)
        assert counts["support_count"] == 2
        assert counts["qualified_support_count"] == 1
        assert store.occurrence_count(MemoryKind.SEMANTIC) == 2
        assert (
            store.occurrence_count(
                MemoryKind.SEMANTIC,
                qualified_only=True,
            )
            == 1
        )


def test_procedural_confidence_uses_binary_outcomes_only(
    tmp_path: Path,
) -> None:
    item = MemoryItem(
        kind=MemoryKind.PROCEDURAL,
        key="fuel_starved:no_fuel|resupply",
        content={"action_key": "resupply"},
        scope=ValidityScope(symptom="fuel_starved:no_fuel"),
    )
    with CognitiveMemoryStore(tmp_path / "memory.sqlite3") as store:
        for index, reward in enumerate((1.0, 1.0, 1.0, 0.0)):
            store.ingest(
                item,
                _occurrence(
                    item,
                    locator=f"episode:{index}",
                    reward=reward,
                ),
            )
        evidence = store.refresh_procedural_confidence(item.memory_id)

        assert evidence.support_count == 4
        assert evidence.success_count == 3
        assert evidence.failure_count == 1
        assert evidence.mean_reward == 0.75
        assert evidence.binary_outcomes is True
        assert evidence.confidence.method == CONFIDENCE_WILSON_LOWER_95
        assert evidence.confidence.n == 4
        assert 0.0 < evidence.confidence.value < 0.75

        row = store.memory_rows(MemoryKind.PROCEDURAL)[0]
        assert row["confidence"]["method"] == CONFIDENCE_WILSON_LOWER_95
        assert row["confidence"]["n"] == 4


def test_nonbinary_procedural_reward_does_not_invent_probability(
    tmp_path: Path,
) -> None:
    item = MemoryItem(
        kind=MemoryKind.PROCEDURAL,
        key="continuous-reward",
        content={"action_key": "x"},
    )
    with CognitiveMemoryStore(tmp_path / "memory.sqlite3") as store:
        store.ingest(item, _occurrence(item, reward=0.4))
        evidence = store.refresh_procedural_confidence(item.memory_id)

        assert evidence.binary_outcomes is False
        assert evidence.mean_reward == 0.4
        assert evidence.confidence.value is None
        assert evidence.confidence.method == CONFIDENCE_NOT_ESTIMATED


def test_batch_manifest_is_stable_and_scoped_to_one_migration(
    tmp_path: Path,
) -> None:
    item = _item()
    with CognitiveMemoryStore(tmp_path / "memory.sqlite3") as store:
        store.ingest(
            item,
            _occurrence(item, locator="row:1", batch_id="f4a"),
        )
        first = store.batch_manifest("f4a")
        store.ingest(
            item,
            _occurrence(item, locator="row:2", batch_id="future"),
        )
        second = store.batch_manifest("f4a")

        assert first == second
        assert first["occurrence_count"] == 1
        assert len(first["manifest_sha256"]) == 64


def test_memory_module_has_no_live_execution_import() -> None:
    path = ROOT / "src/factorio_ai_lab/cortex/memory.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    forbidden = {
        "factorio_ai_lab.cortex.option_execute",
        "factorio_ai_lab.cortex.structural_execute",
        "factorio_ai_lab.experiments.curriculum_runner",
        "factorio_ai_lab.integrations.fle",
        "factorio_rcon",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not (imports & forbidden)


def test_confidence_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="in \\[0,1\\]"):
        ConfidenceEstimate(1.1)
