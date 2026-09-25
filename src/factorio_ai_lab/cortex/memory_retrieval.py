"""F4-B deterministic hybrid retrieval, consolidation and decay.

Structural scope is a compatibility gate. Lexical similarity, evidence quality
and non-destructive decay are explicit ranking components. Original memory
occurrences are never deleted or rewritten by this layer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.memory import (
    ConfidenceEstimate,
    MemoryKind,
    ValidityScope,
)

RETRIEVAL_POLICY_VERSION = "cortex_hybrid_retrieval_v1"
CONSOLIDATION_POLICY_VERSION = "cortex_memory_consolidation_v1"
DECAY_POLICY_VERSION = "cortex_non_destructive_decay_v1"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return " ".join(
            f"{key} {_flatten_text(item)}"
            for key, item in sorted(value.items())
        )
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def tokenize(value: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).lower()
    return frozenset(_TOKEN_RE.findall(without_marks))


def lexical_similarity(query: str, document: str) -> float:
    query_tokens = tokenize(query)
    document_tokens = tokenize(document)
    if not query_tokens or not document_tokens:
        return 0.0
    overlap = len(query_tokens & document_tokens)
    if overlap == 0:
        return 0.0
    return (2.0 * overlap) / (len(query_tokens) + len(document_tokens))


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    key: str
    content: dict[str, Any]
    scope: ValidityScope
    support_count: int
    qualified_support_count: int
    contradiction_count: int
    confidence: ConfidenceEstimate
    item_digest: str
    last_observed_at: str | None
    occurrence_count: int

    def __post_init__(self) -> None:
        if not self.memory_id:
            raise ValueError("memory_id must be non-empty")
        counts = (
            self.support_count,
            self.qualified_support_count,
            self.contradiction_count,
            self.occurrence_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("memory counts cannot be negative")

    @property
    def document_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.key,
                _flatten_text(self.content),
                self.scope.stage or "",
                self.scope.symptom or "",
                self.scope.phase or "",
                " ".join(self.scope.tags),
            )
            if part
        )


@dataclass(frozen=True)
class MemoryDatabaseSnapshot:
    schema_version: str
    quick_check: str
    item_count: int
    occurrence_count: int
    manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "quick_check": self.quick_check,
            "item_count": self.item_count,
            "occurrence_count": self.occurrence_count,
            "manifest_sha256": self.manifest_sha256,
        }


def load_memory_records(path: Path) -> tuple[MemoryRecord, ...]:
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=10.0,
    )
    try:
        rows = connection.execute(
            """
            SELECT
                i.memory_id,
                i.kind,
                i.memory_key,
                i.content_json,
                i.scope_json,
                i.support_count,
                i.qualified_support_count,
                i.contradiction_count,
                i.confidence_value,
                i.confidence_method,
                i.confidence_n,
                i.item_digest,
                MAX(o.observed_at),
                COUNT(o.occurrence_id)
            FROM memory_items i
            LEFT JOIN memory_occurrences o ON o.memory_id=i.memory_id
            GROUP BY i.memory_id
            ORDER BY i.memory_id
            """
        ).fetchall()
    finally:
        connection.close()

    records = []
    for row in rows:
        content = json.loads(row[3])
        scope = json.loads(row[4])
        if not isinstance(content, dict) or not isinstance(scope, dict):
            raise TypeError(f"invalid memory JSON for {row[0]}")
        records.append(
            MemoryRecord(
                memory_id=str(row[0]),
                kind=MemoryKind(str(row[1])),
                key=str(row[2]),
                content=content,
                scope=ValidityScope(
                    stage=scope.get("stage"),
                    symptom=scope.get("symptom"),
                    phase=scope.get("phase"),
                    world=scope.get("world"),
                    tags=tuple(scope.get("tags") or ()),
                ),
                support_count=int(row[5]),
                qualified_support_count=int(row[6]),
                contradiction_count=int(row[7]),
                confidence=ConfidenceEstimate(
                    None if row[8] is None else float(row[8]),
                    method=str(row[9]),
                    n=int(row[10]),
                ),
                item_digest=str(row[11]),
                last_observed_at=None if row[12] is None else str(row[12]),
                occurrence_count=int(row[13]),
            )
        )
    return tuple(records)


def memory_database_snapshot(path: Path) -> MemoryDatabaseSnapshot:
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=10.0,
    )
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        schema_row = connection.execute(
            "SELECT value FROM memory_meta WHERE key=?",
            ("schema_version",),
        ).fetchone()
        if schema_row is None:
            raise RuntimeError("memory schema version missing")
        items = connection.execute(
            """
            SELECT
                memory_id,
                kind,
                memory_key,
                item_digest,
                support_count,
                qualified_support_count,
                contradiction_count,
                confidence_value,
                confidence_method,
                confidence_n
            FROM memory_items
            ORDER BY memory_id
            """
        ).fetchall()
        occurrences = connection.execute(
            """
            SELECT
                occurrence_id,
                memory_id,
                payload_sha256,
                qualified,
                contradiction,
                reward
            FROM memory_occurrences
            ORDER BY occurrence_id
            """
        ).fetchall()
    finally:
        connection.close()

    manifest = {
        "items": [
            {
                "memory_id": str(row[0]),
                "kind": str(row[1]),
                "key": str(row[2]),
                "item_digest": str(row[3]),
                "support_count": int(row[4]),
                "qualified_support_count": int(row[5]),
                "contradiction_count": int(row[6]),
                "confidence_value": (
                    None if row[7] is None else float(row[7])
                ),
                "confidence_method": str(row[8]),
                "confidence_n": int(row[9]),
            }
            for row in items
        ],
        "occurrences": [
            {
                "occurrence_id": str(row[0]),
                "memory_id": str(row[1]),
                "payload_sha256": str(row[2]),
                "qualified": bool(row[3]),
                "contradiction": bool(row[4]),
                "reward": None if row[5] is None else float(row[5]),
            }
            for row in occurrences
        ],
    }
    return MemoryDatabaseSnapshot(
        schema_version=str(schema_row[0]),
        quick_check=quick_check,
        item_count=len(items),
        occurrence_count=len(occurrences),
        manifest_sha256=_sha256_text(_canonical_json(manifest)),
    )


@dataclass(frozen=True)
class DecayPolicy:
    half_life_days: float = 180.0
    support_scale: float = 8.0
    minimum_weight: float = 0.05
    version: str = DECAY_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.half_life_days <= 0 or self.support_scale <= 0:
            raise ValueError("decay scales must be positive")
        if not 0.0 <= self.minimum_weight <= 1.0:
            raise ValueError("minimum_weight must be in [0,1]")

    def weight(
        self,
        *,
        last_observed_at: str | None,
        reference_time: str | None,
        qualified_support_count: int,
    ) -> float:
        if last_observed_at is None or reference_time is None:
            return 1.0
        try:
            observed = datetime.fromisoformat(last_observed_at)
            reference = datetime.fromisoformat(reference_time)
        except ValueError:
            return 1.0
        age_days = max(
            0.0,
            (reference - observed).total_seconds() / 86400.0,
        )
        age_weight = 0.5 ** (age_days / self.half_life_days)
        support_protection = 1.0 - math.exp(
            -max(0, qualified_support_count) / self.support_scale
        )
        value = age_weight + (1.0 - age_weight) * support_protection
        return max(self.minimum_weight, min(1.0, value))


@dataclass(frozen=True)
class MemoryQuery:
    query_id: str
    text: str = ""
    kinds: tuple[MemoryKind, ...] = ()
    scope: ValidityScope = field(default_factory=ValidityScope)
    limit: int = 5
    reference_time: str | None = None
    include_unqualified: bool = False

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise ValueError("query_id must be non-empty")
        if self.limit <= 0:
            raise ValueError("query limit must be positive")
        object.__setattr__(self, "kinds", tuple(self.kinds))


@dataclass(frozen=True)
class RetrievedMemory:
    memory_id: str
    kind: MemoryKind
    key: str
    scope: ValidityScope
    content: dict[str, Any]
    support_count: int
    qualified_support_count: int
    contradiction_count: int
    confidence: ConfidenceEstimate
    last_observed_at: str | None
    structural_score: float
    similarity_score: float
    evidence_score: float
    decay_weight: float
    total_score: float
    basis: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "key": self.key,
            "scope": self.scope.to_dict(),
            "content": dict(self.content),
            "support_count": self.support_count,
            "qualified_support_count": self.qualified_support_count,
            "contradiction_count": self.contradiction_count,
            "confidence": self.confidence.to_dict(),
            "last_observed_at": self.last_observed_at,
            "scores": {
                "structural": self.structural_score,
                "similarity": self.similarity_score,
                "evidence": self.evidence_score,
                "decay_weight": self.decay_weight,
                "total": self.total_score,
            },
            "basis": list(self.basis),
        }


@dataclass(frozen=True)
class RetrievalResult:
    query: MemoryQuery
    records_considered: int
    records_compatible: int
    results: tuple[RetrievedMemory, ...]
    policy_version: str = RETRIEVAL_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": {
                "query_id": self.query.query_id,
                "text": self.query.text,
                "kinds": [kind.value for kind in self.query.kinds],
                "scope": self.query.scope.to_dict(),
                "limit": self.query.limit,
                "reference_time": self.query.reference_time,
                "include_unqualified": self.query.include_unqualified,
            },
            "policy_version": self.policy_version,
            "records_considered": self.records_considered,
            "records_compatible": self.records_compatible,
            "results": [row.to_dict() for row in self.results],
        }


def _scope_score(
    query: ValidityScope,
    memory: ValidityScope,
) -> tuple[bool, float, tuple[str, ...]]:
    specified = 0
    matched = 0.0
    basis: list[str] = []
    for name in ("stage", "symptom", "phase", "world"):
        query_value = getattr(query, name)
        memory_value = getattr(memory, name)
        if query_value is None:
            continue
        specified += 1
        if memory_value is not None and memory_value != query_value:
            return False, 0.0, (f"scope_mismatch:{name}",)
        if memory_value == query_value:
            matched += 1.0
            basis.append(f"scope_exact:{name}")
        else:
            basis.append(f"scope_unknown:{name}")
    if query.tags:
        specified += 1
        overlap = len(set(query.tags) & set(memory.tags))
        if overlap:
            matched += overlap / len(query.tags)
            basis.append("scope_tag_overlap")
        else:
            basis.append("scope_tag_unknown")
    return (
        True,
        matched / specified if specified else 0.0,
        tuple(basis),
    )


def _evidence_raw(record: MemoryRecord) -> float:
    support = math.log1p(max(0, record.qualified_support_count))
    confidence = record.confidence.value or 0.0
    contradiction_ratio = (
        record.contradiction_count / record.support_count
        if record.support_count
        else 0.0
    )
    return max(0.0, support + confidence - contradiction_ratio)


def retrieve_memories(
    records: Iterable[MemoryRecord],
    query: MemoryQuery,
    *,
    decay_policy: DecayPolicy | None = None,
) -> RetrievalResult:
    policy = decay_policy or DecayPolicy()
    rows = tuple(records)
    compatible: list[
        tuple[MemoryRecord, float, float, tuple[str, ...], float]
    ] = []
    for record in rows:
        if query.kinds and record.kind not in query.kinds:
            continue
        if not query.include_unqualified and record.qualified_support_count <= 0:
            continue
        allowed, structural, basis = _scope_score(query.scope, record.scope)
        if not allowed:
            continue
        similarity = lexical_similarity(query.text, record.document_text)
        compatible.append(
            (
                record,
                structural,
                similarity,
                basis,
                _evidence_raw(record),
            )
        )

    max_evidence = max((row[4] for row in compatible), default=0.0)
    ranked = []
    for record, structural, similarity, basis, raw_evidence in compatible:
        evidence = (
            raw_evidence / max_evidence if max_evidence > 0.0 else 0.0
        )
        decay = policy.weight(
            last_observed_at=record.last_observed_at,
            reference_time=query.reference_time,
            qualified_support_count=record.qualified_support_count,
        )
        total = decay * (
            0.50 * structural
            + 0.30 * similarity
            + 0.20 * evidence
        )
        reasons = list(basis)
        if similarity > 0.0:
            reasons.append("lexical_overlap")
        if record.qualified_support_count > 0:
            reasons.append("qualified_support")
        if record.confidence.value is not None:
            reasons.append("empirical_confidence")
        if decay < 1.0:
            reasons.append("non_destructive_decay")
        ranked.append(
            RetrievedMemory(
                memory_id=record.memory_id,
                kind=record.kind,
                key=record.key,
                scope=record.scope,
                content=dict(record.content),
                support_count=record.support_count,
                qualified_support_count=record.qualified_support_count,
                contradiction_count=record.contradiction_count,
                confidence=record.confidence,
                last_observed_at=record.last_observed_at,
                structural_score=structural,
                similarity_score=similarity,
                evidence_score=evidence,
                decay_weight=decay,
                total_score=total,
                basis=tuple(reasons),
            )
        )
    ranked.sort(
        key=lambda row: (
            -row.total_score,
            -row.structural_score,
            -row.similarity_score,
            -row.qualified_support_count,
            row.memory_id,
        )
    )
    return RetrievalResult(
        query=query,
        records_considered=len(rows),
        records_compatible=len(compatible),
        results=tuple(ranked[: query.limit]),
    )


@dataclass(frozen=True)
class ConsolidationSnapshot:
    policy_version: str
    database: MemoryDatabaseSnapshot
    by_kind: dict[str, dict[str, Any]]
    repeated_semantic_items: int
    semantic_duplicate_support: int
    repeated_counterexample_items: int
    procedural_confidence_items: int
    snapshot_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "database": self.database.to_dict(),
            "by_kind": self.by_kind,
            "repeated_semantic_items": self.repeated_semantic_items,
            "semantic_duplicate_support": self.semantic_duplicate_support,
            "repeated_counterexample_items": self.repeated_counterexample_items,
            "procedural_confidence_items": self.procedural_confidence_items,
            "snapshot_sha256": self.snapshot_sha256,
        }


def consolidate_memory(
    records: Iterable[MemoryRecord],
    *,
    database: MemoryDatabaseSnapshot,
) -> ConsolidationSnapshot:
    rows = tuple(records)
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in MemoryKind:
        if kind is MemoryKind.WORKING:
            continue
        subset = [row for row in rows if row.kind is kind]
        by_kind[kind.value] = {
            "items": len(subset),
            "support": sum(row.support_count for row in subset),
            "qualified_support": sum(
                row.qualified_support_count for row in subset
            ),
            "contradictions": sum(
                row.contradiction_count for row in subset
            ),
            "confidence_known": sum(
                row.confidence.value is not None for row in subset
            ),
        }
    semantic = [row for row in rows if row.kind is MemoryKind.SEMANTIC]
    counterexamples = [
        row for row in rows if row.kind is MemoryKind.COUNTEREXAMPLE
    ]
    procedural = [row for row in rows if row.kind is MemoryKind.PROCEDURAL]
    payload = {
        "policy_version": CONSOLIDATION_POLICY_VERSION,
        "database_manifest_sha256": database.manifest_sha256,
        "by_kind": by_kind,
        "repeated_semantic_items": sum(
            row.support_count > 1 for row in semantic
        ),
        "semantic_duplicate_support": sum(
            max(0, row.support_count - 1) for row in semantic
        ),
        "repeated_counterexample_items": sum(
            row.support_count > 1 for row in counterexamples
        ),
        "procedural_confidence_items": sum(
            row.confidence.value is not None for row in procedural
        ),
    }
    return ConsolidationSnapshot(
        policy_version=CONSOLIDATION_POLICY_VERSION,
        database=database,
        by_kind=by_kind,
        repeated_semantic_items=payload["repeated_semantic_items"],
        semantic_duplicate_support=payload["semantic_duplicate_support"],
        repeated_counterexample_items=payload[
            "repeated_counterexample_items"
        ],
        procedural_confidence_items=payload[
            "procedural_confidence_items"
        ],
        snapshot_sha256=_sha256_text(_canonical_json(payload)),
    )
