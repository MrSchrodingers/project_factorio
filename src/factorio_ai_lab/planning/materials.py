from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MaterialPosition:
    item: str
    on_hand: float = 0.0
    work_in_progress: float = 0.0
    incoming: float = 0.0
    reserved: float = 0.0
    safety_stock: float = 0.0

    @property
    def gross_available(self) -> float:
        return max(0.0, self.on_hand) + max(0.0, self.work_in_progress) + max(
            0.0, self.incoming
        )

    @property
    def net_available(self) -> float:
        return max(
            0.0,
            self.gross_available
            - max(0.0, self.reserved)
            - max(0.0, self.safety_stock),
        )

    def to_dict(self) -> dict[str, float | str]:
        return {
            "item": self.item,
            "on_hand": float(self.on_hand),
            "work_in_progress": float(self.work_in_progress),
            "incoming": float(self.incoming),
            "reserved": float(self.reserved),
            "safety_stock": float(self.safety_stock),
            "gross_available": float(self.gross_available),
            "net_available": float(self.net_available),
        }


@dataclass(frozen=True)
class MaterialAllocation:
    item: str
    required: float
    allocated: float
    shortage: float
    projected_surplus: float
    position: MaterialPosition

    @property
    def feasible(self) -> bool:
        return self.shortage <= 1e-9

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "required": float(self.required),
            "allocated": float(self.allocated),
            "shortage": float(self.shortage),
            "projected_surplus": float(self.projected_surplus),
            "feasible": self.feasible,
            "position": self.position.to_dict(),
        }


@dataclass(frozen=True)
class MaterialPlan:
    allocations: tuple[MaterialAllocation, ...]

    @property
    def feasible(self) -> bool:
        return all(row.feasible for row in self.allocations)

    @property
    def total_shortage(self) -> float:
        return sum(row.shortage for row in self.allocations)

    def shortage_by_item(self) -> dict[str, float]:
        return {
            row.item: row.shortage
            for row in self.allocations
            if row.shortage > 1e-9
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "total_shortage": float(self.total_shortage),
            "shortage_by_item": self.shortage_by_item(),
            "allocations": [row.to_dict() for row in self.allocations],
        }


class MaterialLedger:
    """Finite-horizon MRP ledger for construction and production commitments.

    The ledger separates physical stock from WIP/incoming material and from
    reservations/safety stock. Planning never double-counts material already
    committed to another requirement.
    """

    def __init__(
        self,
        positions: Mapping[str, MaterialPosition] | None = None,
    ) -> None:
        self._positions = dict(positions or {})

    def position(self, item: str) -> MaterialPosition:
        return self._positions.get(item, MaterialPosition(item=item))

    def plan(
        self,
        requirements: Mapping[str, float],
    ) -> MaterialPlan:
        allocations: list[MaterialAllocation] = []
        for item in sorted(requirements):
            required = max(0.0, float(requirements[item]))
            position = self.position(item)
            available = position.net_available
            allocated = min(required, available)
            shortage = max(0.0, required - allocated)
            allocations.append(
                MaterialAllocation(
                    item=item,
                    required=required,
                    allocated=allocated,
                    shortage=shortage,
                    projected_surplus=max(0.0, available - allocated),
                    position=position,
                )
            )
        return MaterialPlan(tuple(allocations))

    @classmethod
    def from_inventory(
        cls,
        on_hand: Mapping[str, float],
        *,
        work_in_progress: Mapping[str, float] | None = None,
        incoming: Mapping[str, float] | None = None,
        reserved: Mapping[str, float] | None = None,
        safety_stock: Mapping[str, float] | None = None,
    ) -> MaterialLedger:
        wip = work_in_progress or {}
        inbound = incoming or {}
        commitments = reserved or {}
        safety = safety_stock or {}
        items = set(on_hand) | set(wip) | set(inbound) | set(commitments) | set(safety)
        return cls(
            {
                item: MaterialPosition(
                    item=item,
                    on_hand=float(on_hand.get(item, 0.0) or 0.0),
                    work_in_progress=float(wip.get(item, 0.0) or 0.0),
                    incoming=float(inbound.get(item, 0.0) or 0.0),
                    reserved=float(commitments.get(item, 0.0) or 0.0),
                    safety_stock=float(safety.get(item, 0.0) or 0.0),
                )
                for item in items
            }
        )
