"""World identity and fixed-map-set evaluation.

Two facts this module exists to enforce.

1. A seed is not a world. In this cluster the seed handed to
   `environment.reset(...)` never reaches the map generator (FLE 0.4.3
   documents the argument as unused) and the lab scenario ships a
   pre-generated map, so several distinct seeds can name the exact same
   terrain. Identity therefore has to come from the world itself: the map
   generator seed plus the coarse footprint of the resource patches, both
   read over RCON.

2. A fitness measured on one world is not a fitness. A genome is comparable
   to another genome only when both were evaluated over the same fixed set of
   worlds, and the aggregate keeps the worst case next to the mean so that
   memorising a single terrain stops looking like general competence.

Nothing here talks to Factorio. The RCON transport lives in
`scripts/world_signature.py`; this module only builds the read-only command
text and turns its payload into a signature.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

PATCH_CELL_SIZE = 32
DEFAULT_SCAN_RADIUS = 192

# Read-only: find_entities_filtered plus map_gen_settings. No entity is
# created, moved or destroyed, and no game state is written.
_WORLD_SIGNATURE_TEMPLATE = """/silent-command local s=game.surfaces[1]
local radius=__RADIUS__
local cell=__CELL__
local area={left_top={x=-radius,y=-radius},right_bottom={x=radius,y=radius}}
local cells={}
local order={}
for _,e in pairs(s.find_entities_filtered{area=area,type="resource"}) do
  if e.valid then
    local bx=math.floor(e.position.x/cell)
    local by=math.floor(e.position.y/cell)
    local key=e.name..":"..bx..":"..by
    local c=cells[key]
    if not c then
      c={name=e.name,cell_x=bx,cell_y=by,count=0,amount=0,
         min_x=e.position.x,max_x=e.position.x,
         min_y=e.position.y,max_y=e.position.y}
      cells[key]=c
      order[#order+1]=c
    end
    c.count=c.count+1
    c.amount=c.amount+(e.amount or 0)
    c.min_x=math.min(c.min_x,e.position.x)
    c.max_x=math.max(c.max_x,e.position.x)
    c.min_y=math.min(c.min_y,e.position.y)
    c.max_y=math.max(c.max_y,e.position.y)
  end
end
local g=s.map_gen_settings
rcon.print(helpers.table_to_json({
  connected=true,
  surface=s.name,
  tick=game.tick,
  map_seed=g.seed,
  width=g.width,
  height=g.height,
  scan_radius=radius,
  cell_size=cell,
  patch_count=#order,
  patches=order
}))
"""


class MapSuiteError(ValueError):
    """The suite or the scores handed to it cannot support a fair comparison."""


class WorldMismatchError(RuntimeError):
    """The world observed during evaluation is not the world the suite names."""


def world_signature_command(
    *,
    radius: int = DEFAULT_SCAN_RADIUS,
    cell_size: int = PATCH_CELL_SIZE,
) -> str:
    """Build the read-only RCON command that samples the terrain."""
    if radius <= 0:
        raise ValueError("radius must be positive")
    if cell_size <= 0:
        raise ValueError("cell_size must be positive")
    return (
        _WORLD_SIGNATURE_TEMPLATE
        .replace("__RADIUS__", str(int(radius)))
        .replace("__CELL__", str(int(cell_size)))
    )


def _patch_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    patches = payload.get("patches")
    if patches is None:
        return []
    if isinstance(patches, Mapping):
        # Lua serialises an empty or sparse array as an object.
        rows = list(patches.values())
    elif isinstance(patches, Sequence) and not isinstance(patches, str | bytes):
        rows = list(patches)
    else:
        raise MapSuiteError("patches must be a list or an object keyed by cell")
    return [row for row in rows if isinstance(row, Mapping)]


def patch_footprint(payload: Mapping[str, Any]) -> list[list[Any]]:
    """Coarse spatial footprint of the resource patches.

    Only the occupied grid cells are kept, never the ore amounts: an agent
    mining the world it runs in changes amounts constantly, and a terrain
    identity that drifts while the run progresses would be useless for
    deciding whether two runs happened on the same map.
    """
    footprint = {
        (
            str(row.get("name", "")),
            int(row.get("cell_x", 0)),
            int(row.get("cell_y", 0)),
        )
        for row in _patch_rows(payload)
    }
    return [[name, cell_x, cell_y] for name, cell_x, cell_y in sorted(footprint)]


def world_signature(payload: Mapping[str, Any]) -> str:
    """Stable identity of a generated world.

    Combines the map generator seed with the patch footprint: the seed alone
    would trust a value the cluster may never have applied, and the footprint
    alone would collapse two worlds that happen to share a sampled region.
    """
    if not isinstance(payload, Mapping):
        raise MapSuiteError("world payload must be a mapping")
    if payload.get("connected") is False:
        raise MapSuiteError(str(payload.get("error") or "world payload not connected"))
    canonical = json.dumps(
        {
            "map_seed": payload.get("map_seed"),
            "width": payload.get("width"),
            "height": payload.get("height"),
            "footprint": patch_footprint(payload),
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def world_fingerprint(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Signature plus the human-readable evidence behind it."""
    rows = _patch_rows(payload)
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("name", ""))
        entry = totals.setdefault(
            name,
            {"cells": 0, "entities": 0, "min_x": None, "max_x": None,
             "min_y": None, "max_y": None},
        )
        entry["cells"] += 1
        entry["entities"] += int(row.get("count", 0))
        for axis in ("x", "y"):
            low = row.get(f"min_{axis}")
            high = row.get(f"max_{axis}")
            if low is not None:
                current = entry[f"min_{axis}"]
                entry[f"min_{axis}"] = low if current is None else min(current, low)
            if high is not None:
                current = entry[f"max_{axis}"]
                entry[f"max_{axis}"] = high if current is None else max(current, high)
    return {
        "world_signature": world_signature(payload),
        "map_seed": payload.get("map_seed"),
        "surface": payload.get("surface"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "tick": payload.get("tick"),
        "scan_radius": payload.get("scan_radius"),
        "cell_size": payload.get("cell_size"),
        "patch_cells": len(rows),
        "resources": {name: totals[name] for name in sorted(totals)},
        "footprint": patch_footprint(payload),
    }


@dataclass(frozen=True)
class MapSpec:
    """One world of a fixed evaluation set."""

    map_id: str
    world_signature: str
    seed: int | None = None
    endpoint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "world_signature": self.world_signature,
            "seed": self.seed,
            "endpoint": self.endpoint,
        }


@dataclass(frozen=True)
class MapSuite:
    """An ordered, immutable set of worlds every genome is measured on."""

    suite_id: str
    maps: tuple[MapSpec, ...]

    def __post_init__(self) -> None:
        if not self.suite_id:
            raise MapSuiteError("suite_id must not be empty")
        if not self.maps:
            raise MapSuiteError("a map suite needs at least one map")
        map_ids = [spec.map_id for spec in self.maps]
        if len(set(map_ids)) != len(map_ids):
            raise MapSuiteError(f"suite {self.suite_id} repeats a map_id: {map_ids}")
        signatures = [spec.world_signature for spec in self.maps]
        if any(not signature for signature in signatures):
            raise MapSuiteError(
                f"suite {self.suite_id} has a map without a world signature; "
                "an unidentified world cannot be proven distinct"
            )
        if len(set(signatures)) != len(signatures):
            shared = sorted(
                signature
                for signature in set(signatures)
                if signatures.count(signature) > 1
            )
            raise MapSuiteError(
                f"suite {self.suite_id} has maps sharing a world signature "
                f"{shared}: the set is one world wearing several names"
            )

    @classmethod
    def from_dicts(
        cls,
        suite_id: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> MapSuite:
        specs = tuple(
            MapSpec(
                map_id=str(row["map_id"]),
                world_signature=str(row.get("world_signature") or ""),
                seed=None if row.get("seed") is None else int(row["seed"]),
                endpoint=None if row.get("endpoint") is None else str(row["endpoint"]),
            )
            for row in rows
        )
        return cls(suite_id=suite_id, maps=specs)

    @property
    def map_ids(self) -> tuple[str, ...]:
        return tuple(spec.map_id for spec in self.maps)

    @property
    def size(self) -> int:
        return len(self.maps)

    @property
    def suite_signature(self) -> str:
        canonical = json.dumps(
            {
                "suite_id": self.suite_id,
                "worlds": sorted(spec.world_signature for spec in self.maps),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]

    def spec(self, map_id: str) -> MapSpec:
        for candidate in self.maps:
            if candidate.map_id == map_id:
                return candidate
        raise MapSuiteError(f"suite {self.suite_id} has no map {map_id!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "suite_signature": self.suite_signature,
            "size": self.size,
            "maps": [spec.to_dict() for spec in self.maps],
        }


def aggregate_fitness(
    suite: MapSuite,
    scores: Mapping[str, float],
    *,
    world_evidence: str = "absent",
) -> dict[str, Any]:
    """Aggregate per-map fitness into mean and worst case.

    The worst case is reported next to the mean on purpose: a genome that
    scores well on one terrain and collapses on another has a good mean and a
    bad floor, and the floor is what generalisation means here.
    """
    unknown = sorted(set(scores) - set(suite.map_ids))
    if unknown:
        raise MapSuiteError(f"scores name maps outside suite {suite.suite_id}: {unknown}")
    per_map = {
        map_id: float(scores[map_id]) for map_id in suite.map_ids if map_id in scores
    }
    missing = [map_id for map_id in suite.map_ids if map_id not in per_map]
    values = list(per_map.values())
    worst_map = min(per_map, key=lambda key: per_map[key]) if per_map else None
    return {
        "suite_id": suite.suite_id,
        "suite_signature": suite.suite_signature,
        "suite_size": suite.size,
        "per_map": per_map,
        "evaluated_maps": sorted(per_map),
        "missing_maps": missing,
        "complete": not missing,
        "mean": (sum(values) / len(values)) if values else None,
        "worst": min(values) if values else None,
        "best": max(values) if values else None,
        "worst_map": worst_map,
        "world_evidence": world_evidence,
    }


EvaluateOnMap = Callable[[MapSpec], float | Mapping[str, Any]]


def _unpack_outcome(outcome: float | Mapping[str, Any]) -> tuple[float, str | None]:
    if isinstance(outcome, Mapping):
        if "fitness" not in outcome:
            raise MapSuiteError("evaluation mapping must carry a 'fitness' key")
        observed = outcome.get("world_signature")
        return float(outcome["fitness"]), None if observed is None else str(observed)
    return float(outcome), None


class FixedMapSetEvaluator:
    """Evaluate a genome over every world of a fixed suite."""

    def __init__(self, suite: MapSuite) -> None:
        self.suite = suite

    def evaluate(self, genome_id: str, evaluate: EvaluateOnMap) -> dict[str, Any]:
        scores: dict[str, float] = {}
        observed_count = 0
        for spec in self.suite.maps:
            fitness, observed = _unpack_outcome(evaluate(spec))
            if observed is not None:
                observed_count += 1
                if observed != spec.world_signature:
                    raise WorldMismatchError(
                        f"map {spec.map_id} of suite {self.suite.suite_id} was "
                        f"expected to be world {spec.world_signature} but the run "
                        f"observed {observed}"
                    )
            scores[spec.map_id] = fitness
        if observed_count == 0:
            evidence = "absent"
        elif observed_count == self.suite.size:
            evidence = "verified"
        else:
            evidence = "partial"
        result = aggregate_fitness(self.suite, scores, world_evidence=evidence)
        result["genome_id"] = genome_id
        return result


def rank_genomes(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Order genomes by worst case first, mean second.

    Refuses to order results that are not comparable: a ranking across
    different suites, or across partial coverage, is a ranking of the maps
    each genome happened to get.
    """
    if not results:
        return []
    signatures = {str(row.get("suite_signature")) for row in results}
    if len(signatures) > 1:
        raise MapSuiteError(
            f"results come from different map suites {sorted(signatures)}; "
            "they are not comparable"
        )
    incomplete = sorted(
        str(row.get("genome_id"))
        for row in results
        if not row.get("complete", False)
    )
    if incomplete:
        raise MapSuiteError(
            f"genomes {incomplete} were not evaluated on every map of the suite; "
            "ranking them would compare different workloads"
        )
    ordered = sorted(
        (dict(row) for row in results),
        key=lambda row: (
            -float(row.get("worst") or 0.0),
            -float(row.get("mean") or 0.0),
            str(row.get("genome_id")),
        ),
    )
    for position, row in enumerate(ordered, start=1):
        row["rank"] = position
    return ordered
