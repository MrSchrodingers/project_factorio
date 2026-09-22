from __future__ import annotations

import fcntl
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _canonical_failure_payload(
    *,
    stage: str,
    phase: str,
    detail: str,
    diagnostics: Mapping[str, Any],
) -> str:
    selected = {
        "stage": stage,
        "phase": phase,
        "detail": detail,
        "route_stop_reason": diagnostics.get("route_stop_reason"),
        "route_deficits": diagnostics.get("route_deficits"),
        "failed_gates": diagnostics.get("failed_gates"),
        "error_occurred": diagnostics.get("error_occurred"),
    }
    return json.dumps(selected, sort_keys=True, default=str)


def counterexample_signature(
    *,
    stage: str,
    phase: str,
    detail: str,
    diagnostics: Mapping[str, Any],
) -> str:
    canonical = _canonical_failure_payload(
        stage=stage,
        phase=phase,
        detail=detail,
        diagnostics=diagnostics,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class CounterexampleRecord:
    run_id: str
    stage: str
    phase: str
    detail: str
    signature: str
    diagnostics: Mapping[str, Any]
    configuration: Mapping[str, Any]
    repair: Mapping[str, Any] | None = None
    at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at or datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "stage": self.stage,
            "phase": self.phase,
            "detail": self.detail,
            "signature": self.signature,
            "diagnostics": dict(self.diagnostics),
            "configuration": dict(self.configuration),
            "repair": dict(self.repair) if self.repair else None,
        }


class ExperienceBuffer:
    """Append-only cross-run counterexample replay with deterministic dedupe."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def append(self, record: CounterexampleRecord) -> bool:
        rows = self._rows()
        if any(
            row.get("run_id") == record.run_id
            and row.get("signature") == record.signature
            for row in rows[-256:]
        ):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(
                    json.dumps(record.to_dict(), sort_keys=True, default=str)
                    + "\n"
                )
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True

    def recent(
        self,
        *,
        stage: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        rows = self._rows()
        if stage is not None:
            rows = [row for row in rows if row.get("stage") == stage]
        return rows[-max(1, int(limit)):]

    def signature_count(self, signature: str) -> int:
        return sum(
            row.get("signature") == signature
            for row in self._rows()
        )
