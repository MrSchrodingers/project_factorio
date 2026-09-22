from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelCandidate:
    task: str
    model_id: str
    protocol: str
    eligible: bool
    primary_score: float
    secondary_score: float | None = None
    artifact: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "model_id": self.model_id,
            "protocol": self.protocol,
            "eligible": self.eligible,
            "primary_score": self.primary_score,
            "secondary_score": self.secondary_score,
            "artifact": self.artifact,
            "metadata": self.metadata or {},
        }


@dataclass(frozen=True)
class ModelSelection:
    promoted: bool
    reason: str
    incumbent: dict[str, Any] | None
    challenger: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "promoted": self.promoted,
            "reason": self.reason,
            "incumbent": self.incumbent,
            "challenger": self.challenger,
        }


class ModelArena:
    """Persistent incumbent/challenger selection for trained ML components.

    Scores are expressed as normalized losses: lower is better. Secondary score
    is also lower-is-better and acts as a retention guard when present.
    """

    def __init__(
        self,
        path: Path,
        *,
        improvement_ratio: float = 0.995,
        secondary_retention_ratio: float = 1.05,
    ) -> None:
        if not 0 < improvement_ratio <= 1:
            raise ValueError("improvement_ratio must be in (0, 1]")
        if secondary_retention_ratio < 1:
            raise ValueError("secondary_retention_ratio must be >= 1")
        self.path = path
        self.improvement_ratio = improvement_ratio
        self.secondary_retention_ratio = secondary_retention_ratio

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"tasks": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"tasks": {}}
        return payload if isinstance(payload, dict) else {"tasks": {}}

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def select(self, candidate: ModelCandidate) -> ModelSelection:
        payload = self._read()
        tasks = payload.setdefault("tasks", {})
        incumbent = tasks.get(candidate.task)
        challenger = candidate.to_dict()

        if not candidate.eligible:
            return ModelSelection(
                promoted=False,
                reason="challenger failed model eligibility gates",
                incumbent=incumbent if isinstance(incumbent, dict) else None,
                challenger=challenger,
            )

        if not isinstance(incumbent, dict):
            tasks[candidate.task] = challenger
            self._write(payload)
            return ModelSelection(
                promoted=True,
                reason="first eligibility-qualified model becomes incumbent",
                incumbent=None,
                challenger=challenger,
            )

        if incumbent.get("protocol") != candidate.protocol:
            return ModelSelection(
                promoted=False,
                reason="validation protocol changed; incumbent comparison is not commensurate",
                incumbent=incumbent,
                challenger=challenger,
            )

        incumbent_primary = float(incumbent.get("primary_score", float("inf")))
        primary_improved = (
            candidate.primary_score
            <= incumbent_primary * self.improvement_ratio
        )

        secondary_retained = True
        incumbent_secondary = incumbent.get("secondary_score")
        if (
            candidate.secondary_score is not None
            and isinstance(incumbent_secondary, (int, float))
        ):
            secondary_retained = (
                candidate.secondary_score
                <= float(incumbent_secondary) * self.secondary_retention_ratio
            )

        if primary_improved and secondary_retained:
            previous = dict(incumbent)
            tasks[candidate.task] = challenger
            self._write(payload)
            return ModelSelection(
                promoted=True,
                reason="challenger improves normalized loss and retains secondary gate",
                incumbent=previous,
                challenger=challenger,
            )

        reasons = []
        if not primary_improved:
            reasons.append("primary normalized loss did not improve")
        if not secondary_retained:
            reasons.append("secondary loss regressed beyond retention")
        return ModelSelection(
            promoted=False,
            reason="; ".join(reasons),
            incumbent=incumbent,
            challenger=challenger,
        )
