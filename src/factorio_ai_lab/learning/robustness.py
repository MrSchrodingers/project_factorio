from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def configuration_signature(configuration: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(configuration),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class OpenPlayRobustnessGate:
    """Cross-run qualification gate for an immutable champion/configuration."""

    def __init__(self, path: Path, *, required_passes: int = 3) -> None:
        if required_passes < 2:
            raise ValueError("required_passes must be >= 2")
        self.path = path
        self.required_passes = required_passes

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def record(
        self,
        *,
        champion_run_id: str,
        configuration: Mapping[str, Any],
        run_id: str,
        seed: int,
        passed: bool,
        autonomy_runtime_s: float = 0.0,
    ) -> dict[str, Any]:
        signature = configuration_signature(configuration)
        state = self.read()
        same_candidate = (
            state.get("champion_run_id") == champion_run_id
            and state.get("configuration_signature") == signature
        )
        if not same_candidate:
            state = {
                "champion_run_id": champion_run_id,
                "configuration_signature": signature,
                "required_passes": self.required_passes,
                "attempts": [],
            }

        attempts = state.setdefault("attempts", [])
        if not any(row.get("run_id") == run_id for row in attempts):
            attempts.append(
                {
                    "run_id": run_id,
                    "seed": int(seed),
                    "passed": bool(passed),
                    "autonomy_runtime_s": float(autonomy_runtime_s),
                    "at": datetime.now(UTC).isoformat(),
                }
            )

        passes = [row for row in attempts if row.get("passed") is True]
        distinct_pass_seeds = sorted(
            {int(row["seed"]) for row in passes if "seed" in row}
        )
        state.update(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "last_result": "pass" if passed else "fail",
                "pass_count": len(passes),
                "distinct_pass_seed_count": len(distinct_pass_seeds),
                "distinct_pass_seeds": distinct_pass_seeds,
                "qualified": len(distinct_pass_seeds) >= self.required_passes,
            }
        )
        _atomic_json(self.path, state)
        return state

    def pending_for(
        self,
        *,
        champion_run_id: str,
        configuration: Mapping[str, Any],
    ) -> bool:
        state = self.read()
        return bool(
            state
            and state.get("champion_run_id") == champion_run_id
            and state.get("configuration_signature")
            == configuration_signature(configuration)
            and state.get("last_result") == "pass"
            and not state.get("qualified", False)
        )
