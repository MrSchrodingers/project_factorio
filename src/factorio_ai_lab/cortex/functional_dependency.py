"""Functional dependency completion for structural Cortex actions.

F2-F2 composes measured machine-energy requirements with the existing fuel and
resupply planners.  It is a pure planning layer: no FLE calls and no world
mutation occur here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from numbers import Real
from typing import Any

from factorio_ai_lab.cortex.actions import Refusal
from factorio_ai_lab.cortex.structural_prepare import (
    FUNCTIONAL_CONTRACT_VERSION,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.planning.fuel import profile_from_energy_per_tick
from factorio_ai_lab.planning.resupply import FuelSource, SupplyPlan, plan_supply
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    FuelSpec,
    MachineEnergy,
    RuntimeFactorioCatalog,
)

REFUSAL_ENERGY_SOURCE_UNMEASURED = "structural_energy_source_unmeasured"
REFUSAL_ENERGY_USAGE_UNMEASURED = "structural_energy_usage_unmeasured"
REFUSAL_FUEL_CATEGORIES_UNMEASURED = "structural_fuel_categories_unmeasured"
REFUSAL_NO_COMPATIBLE_FUEL = "structural_no_compatible_fuel"
REFUSAL_FUEL_SUPPLY_UNAVAILABLE = "structural_fuel_supply_unavailable"
REFUSAL_ENERGY_SOURCE_NOT_COMPOSED = "structural_energy_source_not_composed"
REFUSAL_PLACEMENT_ANCHOR_MISSING = "structural_dependency_anchor_missing"


@dataclass(frozen=True)
class FuelCandidateEvaluation:
    fuel: FuelSpec
    units_needed: int
    carried: int
    supply_plan: SupplyPlan

    @property
    def covered(self) -> bool:
        return not self.supply_plan.refused

    def to_dict(self) -> dict[str, Any]:
        return {
            "fuel": self.fuel.as_dict(),
            "units_needed": self.units_needed,
            "carried": self.carried,
            "covered": self.covered,
            "supply_plan": self.supply_plan.to_dict(),
        }


@dataclass(frozen=True)
class FuelDependency:
    machine: str
    energy: MachineEnergy
    horizon_s: float
    fuel: FuelSpec
    units_needed: int
    supply_plan: SupplyPlan

    @property
    def carried_only(self) -> bool:
        return not self.supply_plan.fuel_draws

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "burner_fuel",
            "machine": self.machine,
            "energy": self.energy.as_dict(),
            "horizon_s": self.horizon_s,
            "fuel": self.fuel.as_dict(),
            "units_needed": self.units_needed,
            "carried_only": self.carried_only,
            "supply_plan": self.supply_plan.to_dict(),
        }


@dataclass(frozen=True)
class FunctionalDependencyResult:
    source: PreparedStructuralAction
    prepared: PreparedStructuralAction | None = None
    dependency: FuelDependency | None = None
    evaluations: tuple[FuelCandidateEvaluation, ...] = ()
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.refusal is None):
            raise ValueError("functional completion requires prepared or refusal")

    @property
    def ready(self) -> bool:
        return self.prepared is not None


def _inventory_count(inventory: Mapping[str, Any], item: str) -> int:
    raw = inventory.get(item, 0)
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return 0
    return max(0, int(raw))


def _placement_anchor(
    prepared: PreparedStructuralAction,
) -> tuple[float, float] | None:
    placement = prepared.preflight.get("placement")
    if not isinstance(placement, Mapping):
        return None
    position = placement.get("position")
    if not isinstance(position, Mapping):
        return None
    x = position.get("x")
    y = position.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, Real)
        or not isinstance(y, Real)
    ):
        return None
    return float(x), float(y)


def _insert_fuel_operation(
    prepared: PreparedStructuralAction,
    dependency: FuelDependency,
) -> tuple[StructuralOperation, ...]:
    operation = StructuralOperation(
        op="fuel_processor",
        parameters={
            "fuel_item": dependency.fuel.name,
            "quantity": dependency.units_needed,
            "fuel_value_j": dependency.fuel.fuel_value_j,
            "fuel_categories": list(dependency.fuel.fuel_categories),
            "energy_usage_per_tick_j": dependency.energy.energy_usage_per_tick_j,
            "horizon_s": dependency.horizon_s,
            "supply_plan": dependency.supply_plan.to_dict(),
        },
    )
    result: list[StructuralOperation] = []
    inserted = False
    for current in prepared.operations:
        if not inserted and current.op == "connect_delivery":
            result.append(operation)
            inserted = True
        result.append(current)
    if not inserted:
        for index, current in enumerate(result):
            if current.op == "verify_postconditions":
                result.insert(index, operation)
                inserted = True
                break
    if not inserted:
        result.append(operation)
    return tuple(result)


@dataclass(frozen=True)
class FuelDependencyPlan:
    """Reusable fuel dependency plan for any observed burner machine."""

    dependency: FuelDependency | None = None
    evaluations: tuple[FuelCandidateEvaluation, ...] = ()
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.dependency is None) == (self.refusal is None):
            raise ValueError("fuel plan requires exactly one dependency/refusal")

    @property
    def ready(self) -> bool:
        return self.dependency is not None


def plan_burner_fuel_dependency(
    *,
    machine: str,
    energy: MachineEnergy,
    anchor: tuple[float, float] | None,
    catalog: RuntimeFactorioCatalog,
    inventory: Mapping[str, Any],
    fuel_sources_by_item: Mapping[str, Sequence[FuelSource]] | None = None,
    horizon_s: float = 10.0,
    margin: float = 1.25,
) -> FuelDependencyPlan:
    """Plan measured burner fuel without mutating a PreparedStructuralAction."""

    if energy.source_type_status != PROBE_MEASURED:
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_ENERGY_SOURCE_UNMEASURED,
                detail=(
                    f"energy source for {machine!r} is "
                    f"{energy.source_type_status}"
                ),
                retriable=True,
            ),
        )
    if energy.source_type != "burner":
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_ENERGY_SOURCE_NOT_COMPOSED,
                detail=(
                    f"energy source {energy.source_type!r} for {machine!r} "
                    "is not a burner dependency"
                ),
            ),
        )
    if not energy.energy_usage_measured:
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_ENERGY_USAGE_UNMEASURED,
                detail=f"energy usage for burner {machine!r} was not measured",
                retriable=True,
            ),
        )
    if (
        energy.fuel_categories_status != PROBE_MEASURED
        or not energy.fuel_categories
    ):
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_FUEL_CATEGORIES_UNMEASURED,
                detail=f"fuel categories for burner {machine!r} were not measured",
                retriable=True,
            ),
        )
    if anchor is None:
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_PLACEMENT_ANCHOR_MISSING,
                detail=f"no usable placement anchor for burner {machine!r}",
            ),
        )

    profile = profile_from_energy_per_tick(
        machine,
        energy.energy_usage_per_tick_j,
    )
    if profile is None:
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_ENERGY_USAGE_UNMEASURED,
                detail=f"no valid BurnerProfile for {machine!r}",
                retriable=True,
            ),
        )

    fuels = catalog.compatible_fuels(energy.fuel_categories)
    if not fuels:
        return FuelDependencyPlan(
            refusal=Refusal(
                code=REFUSAL_NO_COMPATIBLE_FUEL,
                detail=(
                    "no measured fuel matches categories "
                    f"{list(energy.fuel_categories)!r}"
                ),
                retriable=True,
            ),
        )

    sources = fuel_sources_by_item or {}
    evaluations: list[FuelCandidateEvaluation] = []
    for fuel in fuels:
        if fuel.fuel_value_j is None:
            continue
        units = profile.fuel_units_for_seconds(
            horizon_s,
            fuel.fuel_value_j,
            margin=margin,
        )
        carried = _inventory_count(inventory, fuel.name)
        supply = plan_supply(
            anchor=anchor,
            fuel_needed=units,
            fuel_carried=carried,
            fuel_sources=tuple(sources.get(fuel.name, ())),
        )
        evaluation = FuelCandidateEvaluation(
            fuel=fuel,
            units_needed=units,
            carried=carried,
            supply_plan=supply,
        )
        evaluations.append(evaluation)
        if supply.refused:
            continue
        return FuelDependencyPlan(
            dependency=FuelDependency(
                machine=machine,
                energy=energy,
                horizon_s=float(horizon_s),
                fuel=fuel,
                units_needed=units,
                supply_plan=supply,
            ),
            evaluations=tuple(evaluations),
        )

    details = "; ".join(
        f"{row.fuel.name}:{','.join(row.supply_plan.refusals)}"
        for row in evaluations
    )
    return FuelDependencyPlan(
        evaluations=tuple(evaluations),
        refusal=Refusal(
            code=REFUSAL_FUEL_SUPPLY_UNAVAILABLE,
            detail="no compatible fuel supply covers the horizon"
            + (f" ({details})" if details else ""),
            retriable=True,
        ),
    )


def complete_structural_dependencies(
    prepared: PreparedStructuralAction,
    *,
    catalog: RuntimeFactorioCatalog,
    inventory: Mapping[str, Any],
    fuel_sources_by_item: Mapping[str, Sequence[FuelSource]] | None = None,
    horizon_s: float = 10.0,
    margin: float = 1.25,
) -> FunctionalDependencyResult:
    """Complete one prepared branch with its measured processor energy need."""

    processor = str(prepared.preflight.get("processor") or "")
    energy = catalog.machine_energy(processor)

    if energy.source_type_status not in {PROBE_MEASURED, PROBE_ABSENT}:
        return FunctionalDependencyResult(
            source=prepared,
            refusal=Refusal(
                code=REFUSAL_ENERGY_SOURCE_UNMEASURED,
                detail=(
                    f"energy source for {processor!r} is "
                    f"{energy.source_type_status}"
                ),
                retriable=True,
            ),
        )

    if energy.source_type_status == PROBE_ABSENT:
        preflight = dict(prepared.preflight)
        preflight["energy_dependency"] = {
            "kind": "none",
            "machine": processor,
            "source_status": PROBE_ABSENT,
        }
        return FunctionalDependencyResult(
            source=prepared,
            prepared=replace(
                prepared,
                contract_version=FUNCTIONAL_CONTRACT_VERSION,
                preflight=preflight,
            ),
        )

    plan = plan_burner_fuel_dependency(
        machine=processor,
        energy=energy,
        anchor=_placement_anchor(prepared),
        catalog=catalog,
        inventory=inventory,
        fuel_sources_by_item=fuel_sources_by_item,
        horizon_s=horizon_s,
        margin=margin,
    )
    if not plan.ready or plan.dependency is None:
        return FunctionalDependencyResult(
            source=prepared,
            evaluations=plan.evaluations,
            refusal=plan.refusal,
        )

    dependency = plan.dependency
    preflight = dict(prepared.preflight)
    preflight["energy_dependency"] = dependency.to_dict()
    completed = replace(
        prepared,
        contract_version=FUNCTIONAL_CONTRACT_VERSION,
        operations=_insert_fuel_operation(prepared, dependency),
        preflight=preflight,
    )
    return FunctionalDependencyResult(
        source=prepared,
        prepared=completed,
        dependency=dependency,
        evaluations=plan.evaluations,
    )
