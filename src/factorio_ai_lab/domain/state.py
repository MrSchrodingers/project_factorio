from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class TileKind(StrEnum):
    EMPTY = "empty"
    BLOCKED = "blocked"
    RESOURCE = "resource"
    ENTITY = "entity"


@dataclass(frozen=True, order=True)
class GridPoint:
    x: int
    y: int

    def manhattan(self, other: "GridPoint") -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


@dataclass(frozen=True)
class EntityState:
    entity_id: str
    prototype: str
    position: GridPoint
    direction: int = 0
    status: str = "unknown"


@dataclass
class WorldState:
    tick: int = 0
    entities: dict[str, EntityState] = field(default_factory=dict)
    inventory: dict[str, float] = field(default_factory=dict)
    production_per_minute: dict[str, float] = field(default_factory=dict)
    blocked_tiles: set[GridPoint] = field(default_factory=set)
    metadata: dict[str, object] = field(default_factory=dict)

    def inventory_total(self) -> float:
        return float(sum(self.inventory.values()))

    def flow(self, item: str) -> float:
        return float(self.production_per_minute.get(item, 0.0))

    def update_flows(self, flows: Mapping[str, float]) -> None:
        self.production_per_minute.update({k: float(v) for k, v in flows.items()})
