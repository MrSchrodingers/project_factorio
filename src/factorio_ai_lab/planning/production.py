from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Recipe:
    name: str
    cycle_seconds: float
    inputs: Mapping[str, float]
    outputs: Mapping[str, float]

    def rate_per_machine(self, item: str) -> float:
        if self.cycle_seconds <= 0:
            raise ValueError("cycle_seconds deve ser positivo")
        return 60.0 * float(self.outputs.get(item, 0.0)) / self.cycle_seconds


@dataclass(frozen=True)
class MachineRequirement:
    recipe: str
    target_item: str
    target_per_minute: float
    machines: float


def machines_for_target(recipe: Recipe, target_item: str, target_per_minute: float) -> MachineRequirement:
    rate = recipe.rate_per_machine(target_item)
    if rate <= 0:
        raise ValueError(f"A receita {recipe.name!r} não produz {target_item!r}")
    if target_per_minute < 0:
        raise ValueError("target_per_minute não pode ser negativo")
    return MachineRequirement(
        recipe=recipe.name,
        target_item=target_item,
        target_per_minute=target_per_minute,
        machines=target_per_minute / rate,
    )
