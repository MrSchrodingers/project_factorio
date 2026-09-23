"""Cross-generation persistence of the surviving factory.

Selection needs a substrate. Until this module existed the evolution loop reset
the world at the start of every generation, so the only thing crossing the
generation boundary was the genome. A champion that survived a full curriculum
started the next generation on an empty map, and "behaviour emerging from the
persistence of survival" had nothing to persist.

What FLE can actually carry across a reset
------------------------------------------
``FactorioInstance.reset(game_state)`` restores, in order:

* the agent inventories (``reset`` admin tool),
* every force entity inside the capture radius of ``save_entity_state``,
  including position, direction, recipe, chest/module/fuel inventories, burner
  charge and transport-line contents (``load_entity_state``),
* the research state (``load_research_state``),
* the agent messages,
* the pickled Python namespace of each agent.

The transport format is therefore ``GameState``: it is the only capture that
FLE itself knows how to replay, and ``TransactionalFLEExecutor.reset`` already
forwards it through ``options={'game_state': ...}``.

What is deliberately left out, and why
--------------------------------------
* **Pickled namespaces.** Capture rewrites them as empty payloads. The
  inheritance we want is physical - factory, stock, research - not the agent's
  Python variables, and reloading pickles from a file on disk is a
  deserialization surface with no benefit here. FLE tolerates the empty payload:
  ``FactorioNamespace.load`` swallows the decode error and leaves the namespace
  clean.
* **``load_blueprint``.** Rejected as transport: its ``server.lua`` calls
  ``force.research_all_technologies()``. Restoring a layout that way would hand
  every heir a finished technology tree and void every research milestone the
  curriculum measures.
* **Everything outside ``GameState``.** Ore-patch depletion, pollution, biter
  state, elapsed ticks and entities outside the ``save_entity_state`` radius are
  not part of the capture and do not survive. A restored world is a restored
  *factory*, not a restored *save game*.

One upstream defect is worked around here: ``GameState.to_raw`` serializes each
inventory with ``inventory.__dict__``, but ``fle.env.entities.Inventory`` is a
pydantic v2 model with ``extra="allow"``, so the item counts live in
``__pydantic_extra__`` and ``__dict__`` is empty. Serializing naively would
persist ``"inventories": [{}]`` and silently disinherit every plate the
champion was carrying. ``capture_champion_state`` rebuilds that field from the
live objects before writing.

Evidence hygiene
----------------
A generation that starts on top of an inherited factory cannot claim to have
built it. Every capture and every restore is therefore accompanied by an
``InheritanceLedger`` derived from the state actually written or read, and
``attribute_generation`` reports the per-entity delta between what was
inherited and what the generation ended with. Fitness must read ``built``, not
the absolute counts; ``evidence_contaminated`` flags every generation whose
absolute metrics include inherited capability.
"""

from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from factorio_ai_lab.learning.checkpoints import load_game_state, save_game_state

INHERITANCE_SUFFIX = ".inheritance.json"


def inheritance_path(path: Path) -> Path:
    """Sidecar that records the provenance of a persisted substrate."""
    return path.with_suffix(path.suffix + INHERITANCE_SUFFIX)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _clean_name(value: Any) -> str:
    """Normalize an entity/item name coming from the Lua serializer.

    ``save_entity_state`` builds names as ``'"' .. entity.name .. '"'`` so that
    the verbatim Lua dumper emits a quoted string. Depending on the transport
    the Python side may see ``iron-chest`` or ``"iron-chest"``; counting the
    second shape under its quoted key would report zero for every known
    positive case, so both are normalized to the bare name.
    """
    text = str(value).strip()
    while len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].strip()
    return text


def _coerce_count(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def decode_entity_state(entities: Any) -> list[dict[str, Any]]:
    """Decode the ``entities`` field of a serialized ``GameState``.

    FLE captures it with ``_save_entity_state(compress=True, encode=True)``,
    i.e. base64 over zlib over JSON. Uncompressed base64 and an already
    decoded list are accepted as well so the ledger keeps working if FLE
    changes the encoding flags.
    """
    if entities is None:
        return []
    if isinstance(entities, list):
        return [dict(item) for item in entities if isinstance(item, Mapping)]
    if not isinstance(entities, str):
        raise TypeError("entities must be a string or a list of mappings")

    text = entities.strip()
    if not text:
        return []
    if text.startswith("["):
        decoded: Any = json.loads(text)
    else:
        blob = base64.b64decode(text)
        try:
            blob = zlib.decompress(blob)
        except zlib.error:
            pass
        decoded = json.loads(blob.decode("utf-8"))
    if isinstance(decoded, Mapping):
        decoded = list(decoded.values())
    if not isinstance(decoded, list):
        raise TypeError("decoded entity state is not a list")
    return [dict(item) for item in decoded if isinstance(item, Mapping)]


def inventory_items(inventory: Any) -> dict[str, int]:
    """Read item counts out of an FLE ``Inventory`` or a plain mapping."""
    if inventory is None:
        return {}
    if isinstance(inventory, Mapping):
        source: Iterable[tuple[Any, Any]] = inventory.items()
    else:
        extra = getattr(inventory, "__pydantic_extra__", None)
        if isinstance(extra, Mapping):
            source = extra.items()
        elif callable(getattr(inventory, "items", None)):
            source = inventory.items()
        else:
            source = vars(inventory).items()
    counts: dict[str, int] = {}
    for key, value in source:
        name = _clean_name(key)
        if not name or name.startswith("_"):
            continue
        count = _coerce_count(value)
        if count:
            counts[name] = counts.get(name, 0) + count
    return counts


@dataclass(frozen=True)
class InheritanceLedger:
    """What a restored world hands the heir before it does any work."""

    entities: dict[str, int]
    entity_total: int
    inventories: dict[str, int]
    inventory_total: int
    researched: tuple[str, ...]
    agents: int

    @property
    def is_empty(self) -> bool:
        return (
            self.entity_total == 0
            and self.inventory_total == 0
            and not self.researched
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": dict(self.entities),
            "entity_total": self.entity_total,
            "inventories": dict(self.inventories),
            "inventory_total": self.inventory_total,
            "researched": list(self.researched),
            "researched_total": len(self.researched),
            "agents": self.agents,
            "empty": self.is_empty,
        }


EMPTY_LEDGER = InheritanceLedger(
    entities={},
    entity_total=0,
    inventories={},
    inventory_total=0,
    researched=(),
    agents=0,
)


def _state_payload(state: Any) -> dict[str, Any]:
    if isinstance(state, Mapping):
        return dict(state)
    to_raw = getattr(state, "to_raw", None)
    if not callable(to_raw):
        raise TypeError("game state must be a mapping or expose to_raw()")
    raw = to_raw()
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("game_state.to_raw() returned an empty/non-string payload")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError("serialized game state must be a JSON object")
    return parsed


def _researched_from_payload(payload: Mapping[str, Any]) -> tuple[str, ...]:
    research = payload.get("research")
    if not isinstance(research, Mapping):
        return ()
    technologies = research.get("technologies")
    if not isinstance(technologies, Mapping):
        return ()
    names = [
        _clean_name(name)
        for name, technology in technologies.items()
        if isinstance(technology, Mapping) and bool(technology.get("researched"))
    ]
    return tuple(sorted(name for name in names if name))


def summarize_state(state: Any) -> InheritanceLedger:
    """Derive the ledger from a state payload, never from a cached sidecar.

    Deriving on every read is what keeps the ledger honest: the checkpoint file
    can be rewritten by the open-play runner, which does not know about the
    sidecar, and a stale ledger would understate the inheritance.
    """
    payload = _state_payload(state)

    entity_counts: dict[str, int] = {}
    entity_total = 0
    for entity in decode_entity_state(payload.get("entities")):
        name = _clean_name(entity.get("name"))
        if not name or name == "character":
            continue
        entity_counts[name] = entity_counts.get(name, 0) + 1
        entity_total += 1

    inventories = payload.get("inventories")
    inventories = inventories if isinstance(inventories, list) else []
    aggregated: dict[str, int] = {}
    for inventory in inventories:
        for name, count in inventory_items(inventory).items():
            aggregated[name] = aggregated.get(name, 0) + count

    return InheritanceLedger(
        entities=dict(sorted(entity_counts.items())),
        entity_total=entity_total,
        inventories=dict(sorted(aggregated.items())),
        inventory_total=sum(aggregated.values()),
        researched=_researched_from_payload(payload),
        agents=len(inventories),
    )


class _SerializedState:
    """Adapter so the sanitized payload can reuse ``save_game_state``."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._payload = dict(payload)

    def to_raw(self) -> str:
        return json.dumps(self._payload)


def _sanitized_payload(game_state: Any) -> dict[str, Any]:
    payload = _state_payload(game_state)

    live_inventories = getattr(game_state, "inventories", None)
    if isinstance(live_inventories, list) and live_inventories:
        # Work around GameState.to_raw dropping pydantic extras (see module
        # docstring): rebuild the item counts from the live objects.
        payload["inventories"] = [
            inventory_items(inventory) for inventory in live_inventories
        ]
    else:
        payload["inventories"] = [
            inventory_items(inventory)
            for inventory in (payload.get("inventories") or [])
        ]

    # Physical inheritance only: no pickled Python namespaces on disk.
    agents = max(1, len(payload["inventories"]))
    payload["namespaces"] = ["" for _ in range(agents)]
    return payload


def capture_champion_state(
    path: Path,
    game_state: Any,
    *,
    run_id: str | None = None,
    generation: Any = None,
    seed: int | None = None,
    arena: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Persist the surviving factory and the ledger of what it hands the heir."""
    payload = _sanitized_payload(game_state)
    ledger = summarize_state(payload)
    checkpoint = save_game_state(
        path,
        _SerializedState(payload),
        run_id=run_id,
        arena=arena or "lifelong_root",
        qualified=True,
    )
    record = {
        "captured_at": _utc_now(),
        "path": str(path),
        "checkpoint": checkpoint.to_dict(),
        "ledger": ledger.to_dict(),
        "source": {
            "run_id": run_id,
            "generation": generation,
            "seed": seed,
            "arena": arena or "lifelong_root",
            "reason": reason,
        },
    }
    _atomic_json(inheritance_path(path), record)
    return record


@dataclass(frozen=True)
class LifelongInheritance:
    """A loaded substrate plus the evidence of what it contains."""

    game_state: Any
    ledger: InheritanceLedger
    path: str
    provenance: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        """JSON-safe view. Never carries the game state: it is megabytes."""
        return {
            "available": True,
            "path": self.path,
            "ledger": self.ledger.to_dict(),
            "provenance": dict(self.provenance),
        }


def read_inheritance_record(path: Path) -> dict[str, Any]:
    sidecar = inheritance_path(path)
    if not sidecar.exists():
        return {}
    try:
        parsed = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_champion_state(path: Path) -> LifelongInheritance | None:
    """Load the persisted substrate, or None when no champion has survived yet."""
    if not path.exists():
        return None
    game_state = load_game_state(path)
    ledger = summarize_state(game_state)
    provenance = read_inheritance_record(path).get("source", {})
    return LifelongInheritance(
        game_state=game_state,
        ledger=ledger,
        path=str(path),
        provenance=provenance if isinstance(provenance, dict) else {},
    )


def _positive_delta(
    final: Mapping[str, int],
    inherited: Mapping[str, int],
) -> dict[str, int]:
    delta: dict[str, int] = {}
    for name in sorted(set(final) | set(inherited)):
        difference = int(final.get(name, 0)) - int(inherited.get(name, 0))
        if difference > 0:
            delta[name] = difference
    return delta


def attribute_generation(
    inherited: InheritanceLedger | None,
    final: InheritanceLedger | None,
) -> dict[str, Any]:
    """Separate what was inherited from what this generation actually built.

    Absolute counts stop being evidence the moment a generation starts on top
    of someone else's factory. ``built`` is the only field a fitness function
    may read as an achievement of the current genome; ``removed`` exposes the
    opposite failure, an heir that destroyed part of what it received.
    """
    base = inherited or EMPTY_LEDGER
    end = final or EMPTY_LEDGER
    inherited_research = set(base.researched)
    final_research = set(end.researched)
    return {
        "inherited": base.to_dict(),
        "final": end.to_dict(),
        "built": {
            "entities": _positive_delta(end.entities, base.entities),
            "entity_total": max(0, end.entity_total - base.entity_total),
            "inventories": _positive_delta(end.inventories, base.inventories),
            "inventory_total": max(0, end.inventory_total - base.inventory_total),
            "researched": sorted(final_research - inherited_research),
        },
        "removed": {
            "entities": _positive_delta(base.entities, end.entities),
            "entity_total": max(0, base.entity_total - end.entity_total),
            "researched": sorted(inherited_research - final_research),
        },
        "evidence_contaminated": not base.is_empty,
    }


class LifelongWarmStart:
    """Inject an inherited world into the first reset of a generation.

    The runners own their executor and call ``executor.reset(seed=seed)``
    themselves, so the only seam available without changing their contract is
    ``TransactionalFLEExecutor.reset``. The patch follows the precedent already
    set by ``enforce_minimum_eval_timeout``: process-local, idempotent,
    reverted on exit, and it never touches site-packages.

    Only the first reset that asks for a fresh world is warm-started. Later
    resets - including the per-step rollback in ``execute``, which calls
    ``environment.reset`` directly and is not intercepted at all - keep their
    exact semantics.

    The context manager also tracks the executors it saw, so the caller can
    read the final world state at the end of the generation without the runner
    having to return it.
    """

    def __init__(
        self,
        game_state: Any = None,
        *,
        executor_class: type | None = None,
    ) -> None:
        self._game_state = game_state
        self._executor_class = executor_class
        self._original: Any = None
        self._patched_class: Any = None
        self.applied = False
        self.executors: list[Any] = []

    def _target_class(self) -> Any:
        if self._executor_class is not None:
            return self._executor_class
        from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor

        return TransactionalFLEExecutor

    def __enter__(self) -> Self:
        target: Any = self._target_class()
        original = target.reset
        warm_start = self

        def reset(executor: Any, *, seed: int | None = None, game_state: Any = None) -> Any:
            warm_start.executors.append(executor)
            if (
                game_state is None
                and warm_start._game_state is not None
                and not warm_start.applied
            ):
                warm_start.applied = True
                return original(executor, seed=seed, game_state=warm_start._game_state)
            return original(executor, seed=seed, game_state=game_state)

        target.reset = reset
        self._patched_class = target
        self._original = original
        return self

    def __exit__(self, *exc_info: object) -> bool:
        patched: Any = self._patched_class
        if patched is not None and self._original is not None:
            patched.reset = self._original
            self._patched_class = None
            self._original = None
        return False

    def final_game_state(self) -> Any:
        """World state left by the last executor this window reset."""
        for executor in reversed(self.executors):
            state = getattr(executor, "game_state", None)
            if state is not None:
                return state
        return None

    def to_record(self) -> dict[str, Any]:
        return {
            "warm_started": self.applied,
            "executors_reset": len(self.executors),
        }
