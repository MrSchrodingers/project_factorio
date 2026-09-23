"""Cross-run qualification gate for an open-play champion.

The gate used to count distinct *seeds*. On this cluster a seed is not a
world: FLE 0.4.3 documents `environment.reset(seed=...)` as ignoring the
argument, and the lab scenario ships a pre-generated map, so eight
consecutive "seeds" ran on one terrain. Counting them as independent trials
asserted a robustness nobody measured.

Qualification therefore counts distinct *worlds*, identified by the terrain
signature read over RCON (`learning.map_suite.world_signature`). A pass
recorded without that evidence still counts as a pass, but it never counts
towards qualification, and once there are enough passes to expose the gap the
state says so in `blocking_reason` instead of quietly waiting forever.

Legacy key note: `distinct_pass_seed_count` is the progress numerator read by
the dashboard and by the open-play runner. It now carries the number of
distinct verified worlds - the unit that actually qualifies - while the raw
seed facts stay available in `distinct_pass_seeds` and `pass_seed_count`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QUALIFYING_UNIT = "distinct_world_signature"


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


def _seed_world_collisions(
    passes: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Worlds reached by more than one seed: the proof that seeds collapse."""
    by_world: dict[str, dict[str, Any]] = {}
    for row in passes:
        signature = row.get("world_signature")
        if not signature:
            continue
        entry = by_world.setdefault(
            str(signature),
            {"world_signature": str(signature), "seeds": set(), "run_ids": []},
        )
        if "seed" in row:
            entry["seeds"].add(int(row["seed"]))
        entry["run_ids"].append(str(row.get("run_id")))
    collisions = []
    for entry in by_world.values():
        if len(entry["seeds"]) > 1:
            collisions.append(
                {
                    "world_signature": entry["world_signature"],
                    "seeds": sorted(entry["seeds"]),
                    "run_ids": sorted(entry["run_ids"]),
                }
            )
    return sorted(collisions, key=lambda row: row["world_signature"])


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
        world_signature: str | None = None,
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
                    "world_signature": (
                        None if world_signature is None else str(world_signature)
                    ),
                    "at": datetime.now(UTC).isoformat(),
                }
            )

        passes = [row for row in attempts if row.get("passed") is True]
        distinct_pass_seeds = sorted(
            {int(row["seed"]) for row in passes if "seed" in row}
        )
        distinct_pass_worlds = sorted(
            {str(row["world_signature"]) for row in passes if row.get("world_signature")}
        )
        unverified_passes = [row for row in passes if not row.get("world_signature")]
        collisions = _seed_world_collisions(passes)
        qualified = len(distinct_pass_worlds) >= self.required_passes
        world_status, blocking_reason = self._diagnose(
            pass_count=len(passes),
            unverified_pass_count=len(unverified_passes),
            collisions=collisions,
            qualified=qualified,
        )
        state.update(
            {
                "updated_at": datetime.now(UTC).isoformat(),
                "last_result": "pass" if passed else "fail",
                "pass_count": len(passes),
                "pass_seed_count": len(distinct_pass_seeds),
                "distinct_pass_seeds": distinct_pass_seeds,
                "distinct_pass_worlds": distinct_pass_worlds,
                "distinct_pass_world_count": len(distinct_pass_worlds),
                # Legacy progress key: now counts worlds, not seeds.
                "distinct_pass_seed_count": len(distinct_pass_worlds),
                "qualifying_unit": QUALIFYING_UNIT,
                "unverified_pass_count": len(unverified_passes),
                "seed_world_collisions": collisions,
                "world_status": world_status,
                "blocking_reason": blocking_reason,
                "qualified": qualified,
            }
        )
        _atomic_json(self.path, state)
        return state

    def _diagnose(
        self,
        *,
        pass_count: int,
        unverified_pass_count: int,
        collisions: list[dict[str, Any]],
        qualified: bool,
    ) -> tuple[str, str | None]:
        """Name the state of the world evidence, and what blocks progress.

        Blocking is only declared once the evidence is conclusive: enough
        passes exist to reach qualification, yet they did not happen on
        distinct worlds. Before that, the gate is simply still collecting.
        """
        if qualified:
            return "verified", None
        if pass_count == 0:
            return "no_pass", None
        if collisions:
            detail = "; ".join(
                f"seeds {row['seeds']} share world {row['world_signature']}"
                for row in collisions
            )
            return (
                "collapsed",
                (
                    f"seed_world_collapse: {detail}. Distinct seeds are running "
                    "on the same terrain, so the passes are not independent trials."
                ),
            )
        if unverified_pass_count and pass_count >= self.required_passes:
            return (
                "unverified",
                (
                    f"world_signature_missing: {unverified_pass_count} of "
                    f"{pass_count} passes carry no terrain signature, so the "
                    "worlds behind them cannot be proven distinct."
                ),
            )
        if unverified_pass_count == pass_count:
            return "unverified", None
        if unverified_pass_count:
            return "partially_verified", None
        # Distinct worlds have already been observed, so another run can still
        # move the count: keep collecting instead of declaring a blockage.
        return "in_progress", None

    def pending_for(
        self,
        *,
        champion_run_id: str,
        configuration: Mapping[str, Any],
    ) -> bool:
        """True while another validation run can still move qualification.

        A blocked gate returns False: repeating a run on a terrain already
        proven identical buys no evidence, it only freezes the incumbent.
        """
        state = self.read()
        return bool(
            state
            and state.get("champion_run_id") == champion_run_id
            and state.get("configuration_signature")
            == configuration_signature(configuration)
            and state.get("last_result") == "pass"
            and not state.get("qualified", False)
            and not state.get("blocking_reason")
        )
