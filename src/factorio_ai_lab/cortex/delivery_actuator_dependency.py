"""Energy-aware delivery-actuator dependency completion for Cortex F2-F4.

This layer is pure planning. It chooses an observed inserter prototype only
when the actuator is carried and its measured energy dependency is satisfiable.

Electric power availability is an explicit tri-state input:
- True: an observed/satisfied power capability exists;
- False: power was observed unavailable;
- None: power capability was not measured.

No world mutation occurs here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from numbers import Real
from typing import Any

from factorio_ai_lab.cortex.actions import Refusal
from factorio_ai_lab.cortex.functional_dependency import (
    FuelCandidateEvaluation,
    FuelDependency,
    plan_burner_fuel_dependency,
)
from factorio_ai_lab.cortex.structural_prepare import (
    DELIVERY_ACTUATOR_CONTRACT_VERSION,
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.planning.delivery import MODE_INSERTER
from factorio_ai_lab.planning.resupply import FuelSource
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    MachineEnergy,
    RuntimeFactorioCatalog,
)

REFUSAL_DELIVERY_OPERATION_MISSING = "delivery_actuator_operation_missing"
REFUSAL_DELIVERY_MODE_UNSUPPORTED = "delivery_actuator_mode_unsupported"
REFUSAL_DELIVERY_ACTUATOR_UNAVAILABLE = "delivery_actuator_unavailable"
REFUSAL_DELIVERY_ENERGY_UNMEASURED = "delivery_actuator_energy_unmeasured"
REFUSAL_DELIVERY_POWER_UNMEASURED = "delivery_actuator_power_unmeasured"
REFUSAL_DELIVERY_POWER_UNAVAILABLE = "delivery_actuator_power_unavailable"
REFUSAL_DELIVERY_ENERGY_NOT_COMPOSED = "delivery_actuator_energy_not_composed"
REFUSAL_DELIVERY_NO_SATISFIABLE_ACTUATOR = "delivery_actuator_no_satisfiable_candidate"


def _inventory_count(inventory: Mapping[str, Any], item: str) -> int:
    raw = inventory.get(item, 0)
    if isinstance(raw, bool) or not isinstance(raw, Real):
        return 0
    return max(0, int(raw))


@dataclass(frozen=True)
class DeliveryActuatorEvaluation:
    actuator: str
    carried: int
    energy: MachineEnergy
    ready: bool
    refusal: Refusal | None = None
    fuel_dependency: FuelDependency | None = None
    fuel_evaluations: tuple[FuelCandidateEvaluation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "actuator": self.actuator,
            "carried": self.carried,
            "energy": self.energy.as_dict(),
            "ready": self.ready,
            "refusal": None if self.refusal is None else self.refusal.to_dict(),
            "fuel_dependency": (
                None
                if self.fuel_dependency is None
                else self.fuel_dependency.to_dict()
            ),
            "fuel_evaluations": [
                evaluation.to_dict() for evaluation in self.fuel_evaluations
            ],
        }


@dataclass(frozen=True)
class DeliveryActuatorDependency:
    actuator: str
    energy: MachineEnergy
    fuel_dependency: FuelDependency | None = None
    electric_power_available: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "delivery_actuator_energy",
            "actuator": self.actuator,
            "energy": self.energy.as_dict(),
            "electric_power_available": self.electric_power_available,
            "fuel_dependency": (
                None
                if self.fuel_dependency is None
                else self.fuel_dependency.to_dict()
            ),
        }


@dataclass(frozen=True)
class DeliveryActuatorResult:
    source: PreparedStructuralAction
    prepared: PreparedStructuralAction | None = None
    dependency: DeliveryActuatorDependency | None = None
    evaluations: tuple[DeliveryActuatorEvaluation, ...] = ()
    refusal: Refusal | None = None

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.refusal is None):
            raise ValueError(
                "delivery actuator completion requires prepared or refusal"
            )

    @property
    def ready(self) -> bool:
        return self.prepared is not None


def _delivery_operation(
    prepared: PreparedStructuralAction,
) -> StructuralOperation | None:
    rows = [
        operation
        for operation in prepared.operations
        if operation.op == "connect_delivery"
    ]
    return rows[0] if len(rows) == 1 else None


def _actuator_anchor(
    operation: StructuralOperation,
) -> tuple[float, float] | None:
    delivery = operation.parameters.get("delivery")
    if not isinstance(delivery, Mapping):
        return None
    lift = delivery.get("lift")
    if not isinstance(lift, Mapping):
        return None
    position = lift.get("position")
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


def _candidate_order(
    operation: StructuralOperation,
    catalog: RuntimeFactorioCatalog,
) -> tuple[str, ...]:
    requested = operation.parameters.get("entities")
    preferred = (
        [str(value) for value in requested if isinstance(value, str) and value]
        if isinstance(requested, Sequence)
        and not isinstance(requested, (str, bytes))
        else []
    )
    observed = list(catalog.machine_names_by_type("inserter"))
    result: list[str] = []
    for name in [*preferred, *observed]:
        if name not in result:
            result.append(name)
    return tuple(result)


def _evaluate_actuator(
    actuator: str,
    *,
    anchor: tuple[float, float] | None,
    catalog: RuntimeFactorioCatalog,
    inventory: Mapping[str, Any],
    electric_power_available: bool | None,
    fuel_sources_by_item: Mapping[str, Sequence[FuelSource]] | None,
    horizon_s: float,
    margin: float,
) -> DeliveryActuatorEvaluation:
    carried = _inventory_count(inventory, actuator)
    energy = catalog.machine_energy(actuator)
    if carried <= 0:
        return DeliveryActuatorEvaluation(
            actuator=actuator,
            carried=carried,
            energy=energy,
            ready=False,
            refusal=Refusal(
                code=REFUSAL_DELIVERY_ACTUATOR_UNAVAILABLE,
                detail=f"delivery actuator {actuator!r} is not carried",
                retriable=True,
            ),
        )

    if energy.source_type_status == PROBE_ABSENT:
        return DeliveryActuatorEvaluation(
            actuator=actuator,
            carried=carried,
            energy=energy,
            ready=True,
        )
    if energy.source_type_status != PROBE_MEASURED:
        return DeliveryActuatorEvaluation(
            actuator=actuator,
            carried=carried,
            energy=energy,
            ready=False,
            refusal=Refusal(
                code=REFUSAL_DELIVERY_ENERGY_UNMEASURED,
                detail=(
                    f"energy source for delivery actuator {actuator!r} is "
                    f"{energy.source_type_status}"
                ),
                retriable=True,
            ),
        )

    if energy.source_type == "electric":
        if electric_power_available is True:
            return DeliveryActuatorEvaluation(
                actuator=actuator,
                carried=carried,
                energy=energy,
                ready=True,
            )
        code = (
            REFUSAL_DELIVERY_POWER_UNMEASURED
            if electric_power_available is None
            else REFUSAL_DELIVERY_POWER_UNAVAILABLE
        )
        detail = (
            "electric power capability was not measured"
            if electric_power_available is None
            else "electric power capability was observed unavailable"
        )
        return DeliveryActuatorEvaluation(
            actuator=actuator,
            carried=carried,
            energy=energy,
            ready=False,
            refusal=Refusal(
                code=code,
                detail=f"{actuator!r}: {detail}",
                retriable=True,
            ),
        )

    if energy.source_type == "burner":
        fuel = plan_burner_fuel_dependency(
            machine=actuator,
            energy=energy,
            anchor=anchor,
            catalog=catalog,
            inventory=inventory,
            fuel_sources_by_item=fuel_sources_by_item,
            horizon_s=horizon_s,
            margin=margin,
        )
        if fuel.ready and fuel.dependency is not None:
            return DeliveryActuatorEvaluation(
                actuator=actuator,
                carried=carried,
                energy=energy,
                ready=True,
                fuel_dependency=fuel.dependency,
                fuel_evaluations=fuel.evaluations,
            )
        return DeliveryActuatorEvaluation(
            actuator=actuator,
            carried=carried,
            energy=energy,
            ready=False,
            refusal=fuel.refusal,
            fuel_evaluations=fuel.evaluations,
        )

    return DeliveryActuatorEvaluation(
        actuator=actuator,
        carried=carried,
        energy=energy,
        ready=False,
        refusal=Refusal(
            code=REFUSAL_DELIVERY_ENERGY_NOT_COMPOSED,
            detail=(
                f"energy source {energy.source_type!r} for delivery actuator "
                f"{actuator!r} has no F2-F4 dependency adapter"
            ),
        ),
    )


def _fuel_actuator_operation(
    dependency: DeliveryActuatorDependency,
) -> StructuralOperation | None:
    fuel = dependency.fuel_dependency
    if fuel is None:
        return None
    return StructuralOperation(
        op="fuel_delivery_actuator",
        parameters={
            "actuator": dependency.actuator,
            "fuel_item": fuel.fuel.name,
            "quantity": fuel.units_needed,
            "fuel_value_j": fuel.fuel.fuel_value_j,
            "fuel_categories": list(fuel.fuel.fuel_categories),
            "energy_usage_per_tick_j": fuel.energy.energy_usage_per_tick_j,
            "horizon_s": fuel.horizon_s,
            "supply_plan": fuel.supply_plan.to_dict(),
        },
    )


def _rewrite_operations(
    prepared: PreparedStructuralAction,
    *,
    dependency: DeliveryActuatorDependency,
) -> tuple[StructuralOperation, ...]:
    result: list[StructuralOperation] = []
    for operation in prepared.operations:
        if operation.op != "connect_delivery":
            result.append(operation)
            continue
        parameters = dict(operation.parameters)
        parameters["entities"] = [dependency.actuator]
        result.append(
            StructuralOperation(
                op="connect_delivery",
                parameters=parameters,
            )
        )
        fuel = _fuel_actuator_operation(dependency)
        if fuel is not None:
            result.append(fuel)
    return tuple(result)


def complete_delivery_actuator_dependency(
    prepared: PreparedStructuralAction,
    *,
    catalog: RuntimeFactorioCatalog,
    inventory: Mapping[str, Any],
    electric_power_available: bool | None,
    fuel_sources_by_item: Mapping[str, Sequence[FuelSource]] | None = None,
    horizon_s: float = 10.0,
    margin: float = 1.25,
) -> DeliveryActuatorResult:
    """Select and satisfy one direct-delivery actuator from measured facts."""

    operation = _delivery_operation(prepared)
    if operation is None:
        return DeliveryActuatorResult(
            source=prepared,
            refusal=Refusal(
                code=REFUSAL_DELIVERY_OPERATION_MISSING,
                detail="prepared action has no unique connect_delivery operation",
            ),
        )
    if str(operation.parameters.get("mode") or "") != MODE_INSERTER:
        return DeliveryActuatorResult(
            source=prepared,
            refusal=Refusal(
                code=REFUSAL_DELIVERY_MODE_UNSUPPORTED,
                detail="F2-F4 currently composes direct-inserter delivery only",
            ),
        )

    anchor = _actuator_anchor(operation)
    evaluations: list[DeliveryActuatorEvaluation] = []
    for actuator in _candidate_order(operation, catalog):
        evaluation = _evaluate_actuator(
            actuator,
            anchor=anchor,
            catalog=catalog,
            inventory=inventory,
            electric_power_available=electric_power_available,
            fuel_sources_by_item=fuel_sources_by_item,
            horizon_s=horizon_s,
            margin=margin,
        )
        evaluations.append(evaluation)
        if not evaluation.ready:
            continue
        dependency = DeliveryActuatorDependency(
            actuator=actuator,
            energy=evaluation.energy,
            fuel_dependency=evaluation.fuel_dependency,
            electric_power_available=(
                electric_power_available
                if evaluation.energy.source_type == "electric"
                else None
            ),
        )
        preflight = dict(prepared.preflight)
        preflight["delivery_actuator_dependency"] = dependency.to_dict()
        completed = replace(
            prepared,
            contract_version=DELIVERY_ACTUATOR_CONTRACT_VERSION,
            operations=_rewrite_operations(
                prepared,
                dependency=dependency,
            ),
            preflight=preflight,
        )
        return DeliveryActuatorResult(
            source=prepared,
            prepared=completed,
            dependency=dependency,
            evaluations=tuple(evaluations),
        )

    details = "; ".join(
        (
            f"{row.actuator}:"
            + ("unknown" if row.refusal is None else row.refusal.code)
        )
        for row in evaluations
    )
    return DeliveryActuatorResult(
        source=prepared,
        evaluations=tuple(evaluations),
        refusal=Refusal(
            code=REFUSAL_DELIVERY_NO_SATISFIABLE_ACTUATOR,
            detail="no observed delivery actuator has a satisfiable energy dependency"
            + (f" ({details})" if details else ""),
            retriable=True,
        ),
    )
