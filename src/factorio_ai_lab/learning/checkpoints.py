from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DurableCheckpoint:
    path: str
    sha256: str
    bytes: int
    saved_at: str
    run_id: str | None
    arena: str | None
    qualified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "saved_at": self.saved_at,
            "run_id": self.run_id,
            "arena": self.arena,
            "qualified": self.qualified,
        }


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def save_game_state(
    path: Path,
    game_state: Any,
    *,
    run_id: str | None = None,
    arena: str | None = None,
    qualified: bool = False,
) -> DurableCheckpoint:
    to_raw = getattr(game_state, "to_raw", None)
    if not callable(to_raw):
        raise TypeError("game_state must expose to_raw()")
    raw = to_raw()
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("game_state.to_raw() returned an empty/non-string payload")

    # Validate the JSON envelope independently of FLE before committing it.
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("serialized GameState must be a JSON object")
    if "entities" not in parsed or "inventories" not in parsed:
        raise ValueError("serialized GameState lacks entities/inventories")

    payload = raw if raw.endswith("\n") else raw + "\n"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    saved_at = datetime.now(UTC).isoformat()
    checkpoint = DurableCheckpoint(
        path=str(path),
        sha256=digest,
        bytes=len(payload.encode("utf-8")),
        saved_at=saved_at,
        run_id=run_id,
        arena=arena,
        qualified=bool(qualified),
    )

    _atomic_text(path, payload)
    _atomic_text(
        path.with_suffix(path.suffix + ".meta.json"),
        json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True) + "\n",
    )
    return checkpoint


def load_game_state(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"empty checkpoint: {path}")
    # Lazy import keeps deterministic/unit-test parts independent from FLE.
    from fle.commons.models.game_state import GameState

    return GameState.parse_raw(raw)


def validate_checkpoint(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("checkpoint is not a JSON object")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    state = load_game_state(path)
    round_trip = state.to_raw()
    round_trip_payload = (
        round_trip if round_trip.endswith("\n") else round_trip + "\n"
    )
    return {
        "path": str(path),
        "sha256": digest,
        "bytes": len(raw.encode("utf-8")),
        "round_trip_valid": bool(json.loads(round_trip_payload)),
    }
