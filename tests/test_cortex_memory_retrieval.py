from __future__ import annotations

from factorio_ai_lab.cortex.memory import (
    ConfidenceEstimate,
    MemoryKind,
    ValidityScope,
)
from factorio_ai_lab.cortex.memory_retrieval import (
    DecayPolicy,
    MemoryDatabaseSnapshot,
    MemoryQuery,
    MemoryRecord,
    consolidate_memory,
    lexical_similarity,
    retrieve_memories,
)


def _record(
    *,
    memory_id: str,
    kind: MemoryKind = MemoryKind.SEMANTIC,
    stage: str | None = None,
    symptom: str | None = None,
    text: str = "iron furnace",
    support: int = 1,
    qualified: int | None = None,
    confidence: float | None = None,
    last_observed_at: str | None = "2026-01-01T00:00:00+00:00",
) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        kind=kind,
        key=memory_id,
        content={"lesson": text},
        scope=ValidityScope(stage=stage, symptom=symptom),
        support_count=support,
        qualified_support_count=support if qualified is None else qualified,
        contradiction_count=0,
        confidence=ConfidenceEstimate(
            confidence,
            method="test" if confidence is not None else "not_estimated",
            n=support,
        ),
        item_digest="digest-" + memory_id,
        last_observed_at=last_observed_at,
        occurrence_count=support,
    )


def test_lexical_similarity_is_deterministic_and_accent_insensitive() -> None:
    first = lexical_similarity(
        "mineração ferro",
        "Mineracao de ferro eficiente",
    )
    second = lexical_similarity(
        "mineração ferro",
        "Mineracao de ferro eficiente",
    )
    assert first == second
    assert first > 0.0
    assert lexical_similarity("coal", "iron furnace") == 0.0


def test_explicit_scope_mismatch_is_fail_closed() -> None:
    rows = (
        _record(memory_id="iron", stage="iron"),
        _record(memory_id="copper", stage="copper"),
        _record(memory_id="unscoped", stage=None),
    )
    result = retrieve_memories(
        rows,
        MemoryQuery(
            query_id="iron-stage",
            text="iron furnace",
            scope=ValidityScope(stage="iron"),
            limit=10,
        ),
    )
    ids = [row.memory_id for row in result.results]
    assert "copper" not in ids
    assert "iron" in ids
    assert "unscoped" in ids
    assert result.results[0].memory_id == "iron"
    assert result.results[0].structural_score == 1.0


def test_hybrid_ranking_changes_with_text_on_same_scope() -> None:
    rows = (
        _record(
            memory_id="furnace",
            stage="smelting",
            text="furnace smelts iron plates",
        ),
        _record(
            memory_id="belt",
            stage="smelting",
            text="transport belt feeds copper ore",
        ),
    )
    furnace = retrieve_memories(
        rows,
        MemoryQuery(
            query_id="furnace",
            text="iron furnace plates",
            scope=ValidityScope(stage="smelting"),
        ),
    )
    belt = retrieve_memories(
        rows,
        MemoryQuery(
            query_id="belt",
            text="copper transport belt",
            scope=ValidityScope(stage="smelting"),
        ),
    )
    assert furnace.results[0].memory_id == "furnace"
    assert belt.results[0].memory_id == "belt"
    assert furnace.results[0].similarity_score > 0
    assert belt.results[0].similarity_score > 0


def test_structural_symptom_promotes_procedure_cross_kind() -> None:
    rows = (
        _record(
            memory_id="procedure",
            kind=MemoryKind.PROCEDURAL,
            symptom="fuel_starved:no_fuel",
            text="insert fuel resupply",
            support=54,
            confidence=0.71,
        ),
        _record(
            memory_id="semantic",
            text="fuel theory",
            support=20,
        ),
    )
    result = retrieve_memories(
        rows,
        MemoryQuery(
            query_id="fuel",
            text="resupply insert fuel",
            kinds=(MemoryKind.PROCEDURAL, MemoryKind.SEMANTIC),
            scope=ValidityScope(symptom="fuel_starved:no_fuel"),
        ),
    )
    assert result.results[0].memory_id == "procedure"
    assert "scope_exact:symptom" in result.results[0].basis
    assert "empirical_confidence" in result.results[0].basis


def test_unqualified_memories_are_excluded_by_default() -> None:
    rows = (
        _record(memory_id="qualified", qualified=1),
        _record(memory_id="unqualified", qualified=0),
    )
    default = retrieve_memories(
        rows,
        MemoryQuery(query_id="default", text="iron"),
    )
    inclusive = retrieve_memories(
        rows,
        MemoryQuery(
            query_id="inclusive",
            text="iron",
            include_unqualified=True,
        ),
    )
    assert [row.memory_id for row in default.results] == ["qualified"]
    assert {row.memory_id for row in inclusive.results} == {
        "qualified",
        "unqualified",
    }


def test_decay_is_non_destructive_and_support_protects_old_memory() -> None:
    policy = DecayPolicy(
        half_life_days=100.0,
        support_scale=5.0,
        minimum_weight=0.05,
    )
    low = policy.weight(
        last_observed_at="2026-01-01T00:00:00+00:00",
        reference_time="2027-01-01T00:00:00+00:00",
        qualified_support_count=1,
    )
    high = policy.weight(
        last_observed_at="2026-01-01T00:00:00+00:00",
        reference_time="2027-01-01T00:00:00+00:00",
        qualified_support_count=50,
    )
    assert 0.05 <= low < high <= 1.0


def test_consolidation_counts_duplicate_support_without_deleting_rows() -> None:
    rows = (
        _record(memory_id="semantic-a", support=4),
        _record(memory_id="semantic-b", support=1),
        _record(
            memory_id="procedure",
            kind=MemoryKind.PROCEDURAL,
            support=3,
            confidence=0.6,
        ),
    )
    database = MemoryDatabaseSnapshot(
        schema_version="cortex_cognitive_memory_v1",
        quick_check="ok",
        item_count=3,
        occurrence_count=8,
        manifest_sha256="x" * 64,
    )
    snapshot = consolidate_memory(rows, database=database)
    assert snapshot.repeated_semantic_items == 1
    assert snapshot.semantic_duplicate_support == 3
    assert snapshot.procedural_confidence_items == 1
    assert snapshot.by_kind["semantic"]["items"] == 2
    assert snapshot.by_kind["semantic"]["support"] == 5
    assert len(snapshot.snapshot_sha256) == 64
