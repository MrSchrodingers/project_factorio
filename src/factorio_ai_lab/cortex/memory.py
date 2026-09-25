"""F4 cognitive-memory substrate.

The substrate separates transient working memory from durable episodic,
semantic, procedural and counterexample memory. It stores provenance and
validity scope explicitly, treats missing confidence as unknown rather than
zero, and makes every imported occurrence idempotent and tamper-evident.

F4-A introduces no Factorio execution surface.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

MEMORY_SCHEMA_VERSION = "cortex_cognitive_memory_v1"
CONFIDENCE_NOT_ESTIMATED = "not_estimated"
CONFIDENCE_WILSON_LOWER_95 = "wilson_lower_95"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MemoryKind(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    COUNTEREXAMPLE = "counterexample"


@dataclass(frozen=True)
class ValidityScope:
    """Known applicability bounds for one memory.

    Missing fields mean unknown/not constrained by available evidence, not a
    wildcard claim that the memory is valid everywhere.
    """

    stage: str | None = None
    symptom: str | None = None
    phase: str | None = None
    world: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("stage", "symptom", "phase", "world"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} scope must be non-empty when provided")
        clean = tuple(sorted({tag.strip() for tag in self.tags if tag.strip()}))
        object.__setattr__(self, "tags", clean)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "symptom": self.symptom,
            "phase": self.phase,
            "world": self.world,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class ConfidenceEstimate:
    value: float | None
    method: str = CONFIDENCE_NOT_ESTIMATED
    n: int = 0

    def __post_init__(self) -> None:
        if not self.method.strip():
            raise ValueError("confidence method must be non-empty")
        if self.n < 0:
            raise ValueError("confidence n cannot be negative")
        if self.value is not None:
            value = float(self.value)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("confidence must be finite and in [0,1]")
            object.__setattr__(self, "value", value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "method": self.method,
            "n": self.n,
        }


@dataclass(frozen=True)
class MemoryItem:
    kind: MemoryKind
    key: str
    content: Mapping[str, Any]
    scope: ValidityScope = field(default_factory=ValidityScope)
    confidence: ConfidenceEstimate = field(
        default_factory=lambda: ConfidenceEstimate(None)
    )

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("memory key must be non-empty")
        object.__setattr__(self, "content", dict(self.content))

    @property
    def memory_id(self) -> str:
        identity = {
            "kind": self.kind.value,
            "key": self.key,
            "scope": self.scope.to_dict(),
        }
        return "memory-" + _sha256_text(_canonical_json(identity))[:24]

    @property
    def item_digest(self) -> str:
        static = {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "key": self.key,
            "content": dict(self.content),
            "scope": self.scope.to_dict(),
        }
        return _sha256_text(_canonical_json(static))

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "key": self.key,
            "content": dict(self.content),
            "scope": self.scope.to_dict(),
            "confidence": self.confidence.to_dict(),
            "item_digest": self.item_digest,
        }


@dataclass(frozen=True)
class MemoryOccurrence:
    """One observed support/contradiction event for a durable memory."""

    memory_id: str
    source: str
    source_sha256: str
    source_locator: str
    payload: Mapping[str, Any]
    batch_id: str
    observed_at: str | None = None
    qualified: bool = True
    contradiction: bool = False
    reward: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "memory_id",
            "source",
            "source_sha256",
            "source_locator",
            "batch_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        object.__setattr__(self, "payload", dict(self.payload))
        if self.reward is not None:
            value = float(self.reward)
            if not math.isfinite(value):
                raise ValueError("occurrence reward must be finite")
            object.__setattr__(self, "reward", value)

    @property
    def occurrence_id(self) -> str:
        identity = {
            "memory_id": self.memory_id,
            "source": self.source,
            "source_locator": self.source_locator,
        }
        return "occurrence-" + _sha256_text(_canonical_json(identity))[:24]

    @property
    def payload_sha256(self) -> str:
        return _sha256_text(_canonical_json(dict(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "memory_id": self.memory_id,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "source_locator": self.source_locator,
            "observed_at": self.observed_at,
            "qualified": self.qualified,
            "contradiction": self.contradiction,
            "reward": self.reward,
            "batch_id": self.batch_id,
            "payload_sha256": self.payload_sha256,
        }


@dataclass(frozen=True)
class WorkingMemorySlot:
    key: str
    value: Any
    source: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("working-memory key must be non-empty")
        if not self.source.strip():
            raise ValueError("working-memory source must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class WorkingMemory:
    """Bounded, deterministic, non-persistent executive workspace."""

    capacity: int
    slots: tuple[WorkingMemorySlot, ...] = ()

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("working-memory capacity must be positive")
        if len(self.slots) > self.capacity:
            raise ValueError("working-memory slots exceed capacity")
        keys = [slot.key for slot in self.slots]
        if len(keys) != len(set(keys)):
            raise ValueError("working-memory keys must be unique")
        object.__setattr__(self, "slots", tuple(self.slots))

    def remember(self, slot: WorkingMemorySlot) -> Self:
        retained = tuple(row for row in self.slots if row.key != slot.key)
        updated = retained + (slot,)
        if len(updated) > self.capacity:
            updated = updated[-self.capacity :]
        return type(self)(capacity=self.capacity, slots=updated)

    def recall(self, key: str) -> WorkingMemorySlot | None:
        for slot in reversed(self.slots):
            if slot.key == key:
                return slot
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "capacity": self.capacity,
            "size": len(self.slots),
            "slots": [slot.to_dict() for slot in self.slots],
            "persistent": False,
        }


@dataclass(frozen=True)
class ProcedureEvidence:
    support_count: int
    success_count: int
    failure_count: int
    mean_reward: float | None
    binary_outcomes: bool
    confidence: ConfidenceEstimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "support_count": self.support_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "mean_reward": self.mean_reward,
            "binary_outcomes": self.binary_outcomes,
            "confidence": self.confidence.to_dict(),
        }


class MemoryConflict(RuntimeError):
    """Raised when immutable memory identity/source content changes."""


class CognitiveMemoryStore:
    """SQLite-backed durable memory store with occurrence-level provenance."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self.path),
            timeout=30.0,
            isolation_level=None,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
                memory_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                content_json TEXT NOT NULL,
                scope_json TEXT NOT NULL,
                item_digest TEXT NOT NULL,
                support_count INTEGER NOT NULL DEFAULT 0,
                qualified_support_count INTEGER NOT NULL DEFAULT 0,
                contradiction_count INTEGER NOT NULL DEFAULT 0,
                confidence_value REAL,
                confidence_method TEXT NOT NULL,
                confidence_n INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_occurrences (
                occurrence_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL REFERENCES memory_items(memory_id),
                source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_locator TEXT NOT NULL,
                observed_at TEXT,
                payload_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                qualified INTEGER NOT NULL,
                contradiction INTEGER NOT NULL,
                reward REAL,
                inserted_at TEXT NOT NULL,
                UNIQUE(memory_id, source, source_locator)
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_items_kind
            ON memory_items(kind)
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memory_occurrences_batch
            ON memory_occurrences(batch_id)
            """
        )
        row = self._connection.execute(
            "SELECT value FROM memory_meta WHERE key=?",
            ("schema_version",),
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO memory_meta(key,value) VALUES(?,?)",
                ("schema_version", MEMORY_SCHEMA_VERSION),
            )
        elif row[0] != MEMORY_SCHEMA_VERSION:
            raise RuntimeError(f"unsupported memory schema {row[0]!r}")

    def ingest(
        self,
        item: MemoryItem,
        occurrence: MemoryOccurrence,
    ) -> str:
        if occurrence.memory_id != item.memory_id:
            raise ValueError("occurrence memory_id does not match item")
        now = datetime.now(UTC).isoformat()
        content_json = _canonical_json(dict(item.content))
        scope_json = _canonical_json(item.scope.to_dict())
        payload_json = _canonical_json(dict(occurrence.payload))

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                """
                SELECT
                    kind,
                    memory_key,
                    content_json,
                    scope_json,
                    item_digest
                FROM memory_items
                WHERE memory_id=?
                """,
                (item.memory_id,),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO memory_items(
                        memory_id,
                        kind,
                        memory_key,
                        content_json,
                        scope_json,
                        item_digest,
                        confidence_value,
                        confidence_method,
                        confidence_n,
                        created_at,
                        updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        item.memory_id,
                        item.kind.value,
                        item.key,
                        content_json,
                        scope_json,
                        item.item_digest,
                        item.confidence.value,
                        item.confidence.method,
                        item.confidence.n,
                        now,
                        now,
                    ),
                )
            elif existing != (
                item.kind.value,
                item.key,
                content_json,
                scope_json,
                item.item_digest,
            ):
                raise MemoryConflict(
                    f"memory item {item.memory_id} immutable content conflict"
                )

            prior = self._connection.execute(
                """
                SELECT payload_sha256, source_sha256, batch_id
                FROM memory_occurrences
                WHERE occurrence_id=?
                """,
                (occurrence.occurrence_id,),
            ).fetchone()
            if prior is not None:
                expected = (
                    occurrence.payload_sha256,
                    occurrence.source_sha256,
                    occurrence.batch_id,
                )
                if prior != expected:
                    raise MemoryConflict(
                        f"occurrence {occurrence.occurrence_id} content conflict"
                    )
                self._connection.execute("COMMIT")
                return "already_present"

            self._connection.execute(
                """
                INSERT INTO memory_occurrences(
                    occurrence_id,
                    memory_id,
                    source,
                    source_sha256,
                    source_locator,
                    observed_at,
                    payload_sha256,
                    payload_json,
                    batch_id,
                    qualified,
                    contradiction,
                    reward,
                    inserted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    occurrence.occurrence_id,
                    occurrence.memory_id,
                    occurrence.source,
                    occurrence.source_sha256,
                    occurrence.source_locator,
                    occurrence.observed_at,
                    occurrence.payload_sha256,
                    payload_json,
                    occurrence.batch_id,
                    1 if occurrence.qualified else 0,
                    1 if occurrence.contradiction else 0,
                    occurrence.reward,
                    now,
                ),
            )
            self._connection.execute(
                """
                UPDATE memory_items
                SET
                    support_count=support_count+1,
                    qualified_support_count=qualified_support_count+?,
                    contradiction_count=contradiction_count+?,
                    updated_at=?
                WHERE memory_id=?
                """,
                (
                    1 if occurrence.qualified else 0,
                    1 if occurrence.contradiction else 0,
                    now,
                    item.memory_id,
                ),
            )
            self._connection.execute("COMMIT")
            return "inserted"
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def update_confidence(
        self,
        memory_id: str,
        confidence: ConfidenceEstimate,
    ) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                """
                UPDATE memory_items
                SET
                    confidence_value=?,
                    confidence_method=?,
                    confidence_n=?,
                    updated_at=?
                WHERE memory_id=?
                """,
                (
                    confidence.value,
                    confidence.method,
                    confidence.n,
                    datetime.now(UTC).isoformat(),
                    memory_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(memory_id)
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def quick_check(self) -> str:
        return str(self._connection.execute("PRAGMA quick_check").fetchone()[0])

    def item_count(self, kind: MemoryKind | None = None) -> int:
        if kind is None:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM memory_items"
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM memory_items WHERE kind=?",
                (kind.value,),
            ).fetchone()
        return int(row[0])

    def occurrence_count(
        self,
        kind: MemoryKind | None = None,
        *,
        qualified_only: bool = False,
    ) -> int:
        clauses = []
        args: list[Any] = []
        if kind is not None:
            clauses.append("i.kind=?")
            args.append(kind.value)
        if qualified_only:
            clauses.append("o.qualified=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        row = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM memory_occurrences o
            JOIN memory_items i ON i.memory_id=o.memory_id
            """
            + where,
            tuple(args),
        ).fetchone()
        return int(row[0])

    def support_counts(self, memory_id: str) -> dict[str, int]:
        row = self._connection.execute(
            """
            SELECT
                support_count,
                qualified_support_count,
                contradiction_count
            FROM memory_items
            WHERE memory_id=?
            """,
            (memory_id,),
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return {
            "support_count": int(row[0]),
            "qualified_support_count": int(row[1]),
            "contradiction_count": int(row[2]),
        }

    def procedure_evidence(self, memory_id: str) -> ProcedureEvidence:
        row = self._connection.execute(
            "SELECT kind FROM memory_items WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
        if row is None:
            raise KeyError(memory_id)
        if row[0] != MemoryKind.PROCEDURAL.value:
            raise ValueError("procedure evidence requires procedural memory")

        rewards = [
            float(value)
            for (value,) in self._connection.execute(
                """
                SELECT reward
                FROM memory_occurrences
                WHERE memory_id=?
                  AND qualified=1
                  AND contradiction=0
                  AND reward IS NOT NULL
                ORDER BY occurrence_id
                """,
                (memory_id,),
            ).fetchall()
        ]
        if not rewards:
            return ProcedureEvidence(
                support_count=0,
                success_count=0,
                failure_count=0,
                mean_reward=None,
                binary_outcomes=True,
                confidence=ConfidenceEstimate(None),
            )
        binary = all(value in (0.0, 1.0) for value in rewards)
        successes = sum(1 for value in rewards if value == 1.0)
        failures = sum(1 for value in rewards if value == 0.0)
        mean_reward = sum(rewards) / len(rewards)
        confidence = (
            ConfidenceEstimate(
                _wilson_lower(successes, len(rewards)),
                method=CONFIDENCE_WILSON_LOWER_95,
                n=len(rewards),
            )
            if binary
            else ConfidenceEstimate(None, n=len(rewards))
        )
        return ProcedureEvidence(
            support_count=len(rewards),
            success_count=successes,
            failure_count=failures,
            mean_reward=mean_reward,
            binary_outcomes=binary,
            confidence=confidence,
        )

    def refresh_procedural_confidence(self, memory_id: str) -> ProcedureEvidence:
        evidence = self.procedure_evidence(memory_id)
        self.update_confidence(memory_id, evidence.confidence)
        return evidence

    def kind_snapshot(self) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for kind in (
            MemoryKind.EPISODIC,
            MemoryKind.SEMANTIC,
            MemoryKind.PROCEDURAL,
            MemoryKind.COUNTEREXAMPLE,
        ):
            result[kind.value] = {
                "items": self.item_count(kind),
                "occurrences": self.occurrence_count(kind),
                "qualified_occurrences": self.occurrence_count(
                    kind,
                    qualified_only=True,
                ),
            }
        return result

    def batch_manifest(self, batch_id: str) -> dict[str, Any]:
        rows = self._connection.execute(
            """
            SELECT
                o.occurrence_id,
                o.memory_id,
                i.item_digest,
                o.payload_sha256,
                o.qualified,
                o.contradiction,
                o.reward
            FROM memory_occurrences o
            JOIN memory_items i ON i.memory_id=o.memory_id
            WHERE o.batch_id=?
            ORDER BY o.occurrence_id
            """,
            (batch_id,),
        ).fetchall()
        manifest = [
            {
                "occurrence_id": str(row[0]),
                "memory_id": str(row[1]),
                "item_digest": str(row[2]),
                "payload_sha256": str(row[3]),
                "qualified": bool(row[4]),
                "contradiction": bool(row[5]),
                "reward": None if row[6] is None else float(row[6]),
            }
            for row in rows
        ]
        return {
            "batch_id": batch_id,
            "occurrence_count": len(manifest),
            "manifest_sha256": _sha256_text(_canonical_json(manifest)),
        }

    def memory_rows(
        self,
        kind: MemoryKind,
    ) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            """
            SELECT
                memory_id,
                memory_key,
                support_count,
                qualified_support_count,
                contradiction_count,
                confidence_value,
                confidence_method,
                confidence_n,
                scope_json,
                content_json
            FROM memory_items
            WHERE kind=?
            ORDER BY memory_id
            """,
            (kind.value,),
        ).fetchall()
        return tuple(
            {
                "memory_id": str(row[0]),
                "key": str(row[1]),
                "support_count": int(row[2]),
                "qualified_support_count": int(row[3]),
                "contradiction_count": int(row[4]),
                "confidence": {
                    "value": None if row[5] is None else float(row[5]),
                    "method": str(row[6]),
                    "n": int(row[7]),
                },
                "scope": json.loads(row[8]),
                "content": json.loads(row[9]),
            }
            for row in rows
        )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _wilson_lower(successes: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0:
        raise ValueError("Wilson interval requires positive total")
    if successes < 0 or successes > total:
        raise ValueError("invalid successes")
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = p + z2 / (2.0 * total)
    spread = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
    return max(0.0, (center - spread) / denominator)
