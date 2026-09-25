"""Persistent one-shot authority ledger for Cortex Option execution.

F2-G4A makes execution grants durable across process reconstruction.  The
ledger is intentionally narrow: it persists exact grants and atomically marks
one grant consumed before any runtime mutation is allowed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from factorio_ai_lab.cortex.options import ProcessingChainOptionPlan

GRANT_SCHEMA_VERSION = "cortex_option_execution_grant_v1"
LEDGER_SCHEMA_VERSION = "cortex_option_grant_ledger_v1"

LEDGER_READY = "ready"
LEDGER_CONSUMED = "consumed"
LEDGER_ALREADY_CONSUMED = "already_consumed"
LEDGER_EXPIRED = "expired"
LEDGER_NOT_FOUND = "not_found"
LEDGER_MISMATCH = "mismatch"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def option_plan_digest(plan: ProcessingChainOptionPlan) -> str:
    """Stable SHA-256 over the exact frozen Option plan."""

    import hashlib

    payload = json.dumps(
        plan.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class OptionExecutionScope:
    """Exact non-wildcard execution scope attached to one grant."""

    experiment_id: str
    world_lease_id: str
    option_kind: str
    max_executions: int = 1

    def __post_init__(self) -> None:
        for label, value in (
            ("experiment_id", self.experiment_id),
            ("world_lease_id", self.world_lease_id),
            ("option_kind", self.option_kind),
        ):
            normalized = value.strip()
            if not normalized:
                raise ValueError(f"{label} must be non-empty")
            if any(token in normalized for token in ("*", "?", "[", "]")):
                raise ValueError(f"{label} must be exact; wildcard scope is forbidden")
        if self.max_executions != 1:
            raise ValueError("Option execution grants are strictly one-shot")

    @classmethod
    def for_plan(
        cls,
        plan: ProcessingChainOptionPlan,
        *,
        experiment_id: str,
        world_lease_id: str,
    ) -> OptionExecutionScope:
        return cls(
            experiment_id=experiment_id,
            world_lease_id=world_lease_id,
            option_kind=plan.request.kind.value,
            max_executions=1,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "world_lease_id": self.world_lease_id,
            "option_kind": self.option_kind,
            "max_executions": self.max_executions,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OptionExecutionScope:
        return cls(
            experiment_id=str(payload["experiment_id"]),
            world_lease_id=str(payload["world_lease_id"]),
            option_kind=str(payload["option_kind"]),
            max_executions=int(payload["max_executions"]),
        )


@dataclass(frozen=True)
class OptionExecutionGrant:
    """Durable one-shot promotion grant bound to one exact Option plan."""

    grant_id: str
    option_id: str
    prepared_action_id: str
    plan_digest: str
    code_revision: str
    run_id: str | None
    issued_at: str
    expires_at: str
    scope: OptionExecutionScope
    issued_by: str
    reason: str
    schema_version: str = GRANT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for label, value in (
            ("grant_id", self.grant_id),
            ("option_id", self.option_id),
            ("prepared_action_id", self.prepared_action_id),
            ("plan_digest", self.plan_digest),
            ("code_revision", self.code_revision),
            ("issued_at", self.issued_at),
            ("expires_at", self.expires_at),
            ("issued_by", self.issued_by),
            ("reason", self.reason),
            ("schema_version", self.schema_version),
        ):
            if not value.strip():
                raise ValueError(f"{label} must be non-empty")
        if self.run_id is None or not self.run_id.strip():
            raise ValueError("run_id must be non-empty for durable EXECUTE authority")
        if self.schema_version != GRANT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported grant schema_version {self.schema_version!r}"
            )
        issued = _parse_timestamp(self.issued_at)
        expires = _parse_timestamp(self.expires_at)
        if expires <= issued:
            raise ValueError("expires_at must be after issued_at")

    @classmethod
    def for_plan(
        cls,
        plan: ProcessingChainOptionPlan,
        *,
        scope: OptionExecutionScope,
        issued_by: str,
        reason: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
        grant_id: str | None = None,
    ) -> OptionExecutionGrant:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        issued_dt = _as_utc(now or _utc_now())
        expires_dt = issued_dt + timedelta(seconds=ttl_seconds)
        provenance = plan.request.provenance
        return cls(
            grant_id=grant_id or uuid4().hex,
            option_id=plan.request.option_id,
            prepared_action_id=plan.prepared.action_id,
            plan_digest=option_plan_digest(plan),
            code_revision=provenance.code_revision,
            run_id=provenance.run_id,
            issued_at=issued_dt.isoformat(),
            expires_at=expires_dt.isoformat(),
            scope=scope,
            issued_by=issued_by,
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "option_id": self.option_id,
            "prepared_action_id": self.prepared_action_id,
            "plan_digest": self.plan_digest,
            "code_revision": self.code_revision,
            "run_id": self.run_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "scope": self.scope.to_dict(),
            "issued_by": self.issued_by,
            "reason": self.reason,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class GrantLedgerEntry:
    grant: OptionExecutionGrant
    consumed_at: str | None
    consume_result: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.grant.to_dict(),
            "consumed_at": self.consumed_at,
            "consume_result": self.consume_result,
        }


@dataclass(frozen=True)
class GrantLedgerDecision:
    status: str
    allowed: bool
    entry: GrantLedgerEntry | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "allowed": self.allowed,
            "detail": self.detail,
            "entry": None if self.entry is None else self.entry.to_dict(),
        }


class PersistentOptionGrantLedger:
    """SQLite-backed authority ledger with process-safe one-shot consumption."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS option_execution_grants (
                    grant_id TEXT PRIMARY KEY,
                    option_id TEXT NOT NULL,
                    prepared_action_id TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    code_revision TEXT NOT NULL,
                    run_id TEXT,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    issued_by TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    consumed_at TEXT,
                    consume_result TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_option_grants_plan
                ON option_execution_grants(plan_digest, option_id);

                CREATE INDEX IF NOT EXISTS idx_option_grants_consumed
                ON option_execution_grants(consumed_at);
                """
            )
            row = connection.execute(
                "SELECT value FROM ledger_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO ledger_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", LEDGER_SCHEMA_VERSION),
                )
            elif str(row["value"]) != LEDGER_SCHEMA_VERSION:
                raise RuntimeError(
                    "unsupported Option grant ledger schema_version "
                    f"{row['value']!r}"
                )
            connection.commit()

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> GrantLedgerEntry:
        scope_payload = json.loads(str(row["scope_json"]))
        if not isinstance(scope_payload, dict):
            raise TypeError("persisted grant scope is not an object")
        grant = OptionExecutionGrant(
            grant_id=str(row["grant_id"]),
            option_id=str(row["option_id"]),
            prepared_action_id=str(row["prepared_action_id"]),
            plan_digest=str(row["plan_digest"]),
            code_revision=str(row["code_revision"]),
            run_id=None if row["run_id"] is None else str(row["run_id"]),
            issued_at=str(row["issued_at"]),
            expires_at=str(row["expires_at"]),
            scope=OptionExecutionScope.from_dict(scope_payload),
            issued_by=str(row["issued_by"]),
            reason=str(row["reason"]),
            schema_version=str(row["schema_version"]),
        )
        return GrantLedgerEntry(
            grant=grant,
            consumed_at=(
                None if row["consumed_at"] is None else str(row["consumed_at"])
            ),
            consume_result=(
                None
                if row["consume_result"] is None
                else str(row["consume_result"])
            ),
        )

    def issue(self, grant: OptionExecutionGrant) -> GrantLedgerEntry:
        """Persist a fresh grant; duplicate grant ids are never upserted."""

        scope_json = json.dumps(
            grant.scope.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO option_execution_grants(
                    grant_id,
                    option_id,
                    prepared_action_id,
                    plan_digest,
                    code_revision,
                    run_id,
                    issued_at,
                    expires_at,
                    scope_json,
                    issued_by,
                    reason,
                    schema_version,
                    consumed_at,
                    consume_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    grant.grant_id,
                    grant.option_id,
                    grant.prepared_action_id,
                    grant.plan_digest,
                    grant.code_revision,
                    grant.run_id,
                    grant.issued_at,
                    grant.expires_at,
                    scope_json,
                    grant.issued_by,
                    grant.reason,
                    grant.schema_version,
                ),
            )
            connection.commit()
        entry = self.get(grant.grant_id)
        if entry is None:
            raise RuntimeError("persisted grant disappeared after commit")
        return entry

    def get(self, grant_id: str) -> GrantLedgerEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM option_execution_grants
                WHERE grant_id = ?
                """,
                (grant_id,),
            ).fetchone()
        return None if row is None else self._entry_from_row(row)

    @staticmethod
    def _decision_for_entry(
        entry: GrantLedgerEntry | None,
        grant: OptionExecutionGrant,
        *,
        now: datetime,
    ) -> GrantLedgerDecision:
        if entry is None:
            return GrantLedgerDecision(
                status=LEDGER_NOT_FOUND,
                allowed=False,
                entry=None,
                detail="grant_id is not persisted in the authority ledger",
            )
        if entry.grant != grant:
            return GrantLedgerDecision(
                status=LEDGER_MISMATCH,
                allowed=False,
                entry=entry,
                detail="persisted grant differs from supplied grant",
            )
        if entry.consumed_at is not None:
            return GrantLedgerDecision(
                status=LEDGER_ALREADY_CONSUMED,
                allowed=False,
                entry=entry,
                detail="grant was already consumed durably",
            )
        expires_at = _parse_timestamp(grant.expires_at)
        if now >= expires_at:
            return GrantLedgerDecision(
                status=LEDGER_EXPIRED,
                allowed=False,
                entry=entry,
                detail="grant expired before consumption",
            )
        return GrantLedgerDecision(
            status=LEDGER_READY,
            allowed=True,
            entry=entry,
            detail="grant is persisted, exact, unconsumed and unexpired",
        )

    def check(
        self,
        grant: OptionExecutionGrant,
        *,
        now: datetime | None = None,
    ) -> GrantLedgerDecision:
        instant = _as_utc(now or _utc_now())
        return self._decision_for_entry(
            self.get(grant.grant_id),
            grant,
            now=instant,
        )

    def consume(
        self,
        grant: OptionExecutionGrant,
        *,
        now: datetime | None = None,
        consume_result: str = "reserved_before_runtime_mutation",
    ) -> GrantLedgerDecision:
        """Atomically consume one exact grant before any external mutation."""

        if not consume_result.strip():
            raise ValueError("consume_result must be non-empty")
        instant = _as_utc(now or _utc_now())
        consumed_at = instant.isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM option_execution_grants
                WHERE grant_id = ?
                """,
                (grant.grant_id,),
            ).fetchone()
            entry = None if row is None else self._entry_from_row(row)
            decision = self._decision_for_entry(entry, grant, now=instant)
            if not decision.allowed:
                connection.rollback()
                return decision

            cursor = connection.execute(
                """
                UPDATE option_execution_grants
                SET consumed_at = ?, consume_result = ?
                WHERE grant_id = ? AND consumed_at IS NULL
                """,
                (consumed_at, consume_result, grant.grant_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                latest = self.get(grant.grant_id)
                return GrantLedgerDecision(
                    status=LEDGER_ALREADY_CONSUMED,
                    allowed=False,
                    entry=latest,
                    detail="concurrent consumer won the one-shot grant",
                )
            connection.commit()

        persisted = self.get(grant.grant_id)
        if persisted is None or persisted.consumed_at is None:
            raise RuntimeError("grant consumption was not durable after commit")
        return GrantLedgerDecision(
            status=LEDGER_CONSUMED,
            allowed=True,
            entry=persisted,
            detail="grant consumed durably before runtime mutation",
        )
