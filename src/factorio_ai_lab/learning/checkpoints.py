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



def _repair_inventories(raw: str, game_state: Any) -> str:
    """Restore inventory contents that ``GameState.to_raw()`` drops.

    ``to_raw`` serialises each inventory with ``inventory.__dict__``, but
    ``fle.env.entities.Inventory`` is a pydantic v2 model declared with
    ``extra="allow"``: the items live in ``__pydantic_extra__`` and
    ``__dict__`` is empty. A checkpoint written naively therefore persists
    ``"inventories": [{}]`` and silently loses everything the agent was
    carrying - restoring from it would hand the heir an empty pocket while
    reporting success.

    Measured on this machine:

        Inventory(**{iron-plate: 3, coal: 5})
          __dict__            -> {}
          __pydantic_extra__  -> {iron-plate: 3, coal: 5}

    The live objects still hold the truth, so the field is rebuilt from them
    before the payload is committed.
    """
    parsed = json.loads(raw)
    serialised = parsed.get("inventories")
    live = getattr(game_state, "inventories", None)
    if not isinstance(serialised, list) or not isinstance(live, list):
        return raw
    if len(serialised) != len(live):
        return raw

    # Imported late: lifelong imports this module.
    from factorio_ai_lab.learning.lifelong import inventory_items

    repaired = False
    for index, inventory in enumerate(live):
        items = inventory_items(inventory)
        if not items or serialised[index]:
            continue
        serialised[index] = dict(items)
        repaired = True
    if not repaired:
        return raw
    parsed["inventories"] = serialised
    return json.dumps(parsed)


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

    raw = _repair_inventories(raw, game_state)
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
