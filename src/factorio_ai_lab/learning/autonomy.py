from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from factorio_ai_lab.learning.factory_graph import (
    FUEL_STARVED_STATUSES,
    POWER_STARVED_STATUSES,
    TRANSPORT_NAMES,
    UNKNOWN_STATUS,
    normalize_status,
)

_INSERTERS = {"burner-inserter", "inserter", "fast-inserter", "long-handed-inserter"}
#: Taken from factory_graph rather than restated. A second private copy of
#: this list is how the two modules came to disagree about what a conveyor is:
#: the graph learned that a splitter and an underground belt carry material
#: while this module still counted three plain tiers, so a factory
#: distributing through a bus would read here as having no distribution.
_BELTS = TRANSPORT_NAMES

#: The conditions that must hold at the same time for the factory to keep
#: producing while the agent does nothing. `score` counts them and
#: `closed_loop` is their conjunction; nothing else defines either.
CLOSED_LOOP_GATES = (
    "fuel_distribution",
    "electric_distribution",
    "smelting_distribution",
    "zero_manual_logistics",
    "healthy_fuel",
    "healthy_power",
    "soak_long_enough",
    "producing_material",
)


def _all_true(*values: bool | None) -> bool | None:
    """
    Three-valued conjunction: a definite failure wins, otherwise unknown wins.

    A gate that was not measured must never be reported as satisfied, and must
    never turn a definite failure into an unknown either.
    """
    if any(value is False for value in values):
        return False
    if any(value is None for value in values):
        return None
    return True


def _positive_rate(rates: Mapping[str, float], item: str) -> bool | None:
    """Positive measured rate, measured zero, or no measurement."""
    if item not in rates:
        return None
    return float(rates[item]) > 0.0


def _any_positive_rate(
    rates: Mapping[str, float],
    items: Sequence[str],
) -> bool | None:
    """Three-valued OR over rate measurements.

    A positive observed member proves True. False requires every requested
    member to have been measured and non-positive. Any missing member keeps a
    non-positive set unknown.
    """
    states = [_positive_rate(rates, item) for item in items]
    if any(state is True for state in states):
        return True
    if any(state is None for state in states):
        return None
    return False


@dataclass(frozen=True)
class AutonomyEvidence:
    """
    What one world snapshot proves about the factory running without the agent.

    `score` is the fraction of CLOSED_LOOP_GATES that hold in the snapshot and
    is always a measurement. 0.0 means the snapshot was read and no gate holds
    -- measured and bad. A gate that could not be measured counts as not
    holding, so the score is a lower bound and `unmeasured_gates` names what
    was missing. "Never evaluated" has no representation here and must not be
    encoded as 0.0: a caller that never ran an evaluation records None in its
    own vector (see `FitnessVector.autonomy_score`). Confusing the two is what
    turns "we did not look" into "the factory is worthless".

    `closed_loop` is the same eight gates as a conjunction: True when all of
    them hold, False when at least one definitely fails, None when none fails
    but at least one was not measured. None is not a failure; it is the absence
    of a verdict, and callers that gate on it should treat it as "not
    demonstrated" while reporting it apart from a demonstrated failure.

    `soak_runtime_s` follows the same rule: None means no soak window was
    measured, 0.0 means one was measured and lasted no time at all.
    """

    level: str
    score: float
    soak_runtime_s: float | None
    manual_harvest_calls: int | None = 0
    manual_transfer_calls: int | None = 0
    manual_craft_calls: int | None = 0
    assisted_navigation_count: int = 0
    no_fuel_entities: int = 0
    no_power_entities: int = 0
    belt_count: int = 0
    inserter_count: int = 0
    producer_count: int = 0
    capabilities: frozenset[str] = field(default_factory=frozenset)
    #: Gate name -> True / False / None (not measured). Also carries
    #: `autonomous_smelting_cells`, which is a count rather than a gate.
    topology: Mapping[str, Any] = field(default_factory=dict)
    autonomous_rates_per_s: Mapping[str, float] = field(default_factory=dict)

    @property
    def manual_logistics_calls(self) -> int | None:
        """Harvests plus transfers, or None when nothing counted them."""
        if self.manual_harvest_calls is None or self.manual_transfer_calls is None:
            return None
        return self.manual_harvest_calls + self.manual_transfer_calls

    @property
    def unmeasured_gates(self) -> tuple[str, ...]:
        """Gates the snapshot could not decide, in CLOSED_LOOP_GATES order."""
        return tuple(
            name
            for name in CLOSED_LOOP_GATES
            if self.topology.get(name, None) is None
        )

    @property
    def closed_loop(self) -> bool | None:
        states = [self.topology.get(name, None) for name in CLOSED_LOOP_GATES]
        return _all_true(*states)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = sorted(self.capabilities)
        payload["manual_logistics_calls"] = self.manual_logistics_calls
        payload["closed_loop"] = self.closed_loop
        payload["unmeasured_gates"] = list(self.unmeasured_gates)
        payload["score"] = round(self.score, 6)
        payload["autonomous_rates_per_s"] = {
            key: round(float(value), 8)
            for key, value in sorted(self.autonomous_rates_per_s.items())
        }
        return payload


def _status(entity: Mapping[str, Any]) -> str:
    return normalize_status(entity.get("status"))


def _status_reported(entities: Sequence[Mapping[str, Any]]) -> bool:
    return any(_status(entity) != UNKNOWN_STATUS for entity in entities)


def _position(entity: Mapping[str, Any]) -> tuple[float, float] | None:
    raw = entity.get("position")
    if not isinstance(raw, Mapping):
        return None
    try:
        return float(raw["x"]), float(raw["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    pa = _position(a)
    pb = _position(b)
    if pa is None or pb is None:
        return math.inf
    return math.hypot(pa[0] - pb[0], pa[1] - pb[1])


def _has_near(
    source: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    radius: float,
) -> bool:
    return any(_distance(source, candidate) <= radius for candidate in candidates)


def evaluate_factory_autonomy(
    *,
    entities: Sequence[Mapping[str, Any]],
    interventions: Mapping[str, int] | None = None,
    production_rates_per_s: Mapping[str, float] | None = None,
    soak_runtime_s: float | None = 0.0,
    assisted_navigation_count: int = 0,
) -> AutonomyEvidence:
    """
    Score a factory snapshot against the eight closed-loop gates.

    `entities` is a world snapshot: name, position and, when the game reported
    one, status. An empty sequence is a measurement -- there is no factory --
    and scores 0.0; a caller that failed to take a snapshot must not call this
    function and pretend the result is zero autonomy.

    `interventions` is the committed count of agent material handling.
    An empty mapping means the executor was instrumented and committed
    nothing; None means nothing counted, and then the manual-logistics gate is
    reported as unmeasured instead of as zero manual work.

    `soak_runtime_s` is the unattended window the snapshot closes. None means
    no window was measured, and then the soak gate -- and every gate that
    depends on it -- is unmeasured rather than failed.
    """
    rates = {
        str(key): max(0.0, float(value))
        for key, value in (production_rates_per_s or {}).items()
        if isinstance(value, (int, float))
    }
    rows = [row for row in entities if isinstance(row, Mapping)]

    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for entity in rows:
        by_name.setdefault(str(entity.get("name", "")), []).append(entity)

    belts = [
        entity
        for name in _BELTS
        for entity in by_name.get(name, [])
    ]
    inserters = [
        entity
        for name in _INSERTERS
        for entity in by_name.get(name, [])
    ]
    drills = [
        *by_name.get("burner-mining-drill", []),
        *by_name.get("electric-mining-drill", []),
    ]
    furnaces = [
        *by_name.get("stone-furnace", []),
        *by_name.get("steel-furnace", []),
        *by_name.get("electric-furnace", []),
    ]
    chests = [
        *by_name.get("wooden-chest", []),
        *by_name.get("iron-chest", []),
        *by_name.get("steel-chest", []),
    ]
    boilers = by_name.get("boiler", [])
    engines = by_name.get("steam-engine", [])
    pumps = by_name.get("offshore-pump", [])
    poles = [
        *by_name.get("small-electric-pole", []),
        *by_name.get("medium-electric-pole", []),
        *by_name.get("big-electric-pole", []),
        *by_name.get("substation", []),
    ]
    consumers = [
        *by_name.get("lab", []),
        *by_name.get("assembling-machine-1", []),
        *by_name.get("assembling-machine-2", []),
        *by_name.get("assembling-machine-3", []),
        *by_name.get("electric-mining-drill", []),
    ]

    no_fuel = sum(
        _status(entity) in FUEL_STARVED_STATUSES
        for entity in rows
    )
    no_power = sum(
        _status(entity) in POWER_STARVED_STATUSES
        for entity in rows
    )

    boiler_has_inserter = any(
        _has_near(boiler, inserters, radius=3.2)
        for boiler in boilers
    )
    boiler_has_belt = any(
        _has_near(boiler, belts, radius=5.0)
        for boiler in boilers
    )
    furnace_has_inserter = any(
        _has_near(furnace, inserters, radius=3.2)
        for furnace in furnaces
    )
    furnace_has_belt = any(
        _has_near(furnace, belts, radius=5.0)
        for furnace in furnaces
    )
    autonomous_smelting_cells = 0
    for furnace in furnaces:
        nearby_inserters = sum(
            _distance(furnace, inserter) <= 3.2
            for inserter in inserters
        )
        has_belt = _has_near(furnace, belts, radius=5.0)
        has_output_buffer = _has_near(furnace, chests, radius=5.0)
        if nearby_inserters >= 3 and has_belt and has_output_buffer:
            autonomous_smelting_cells += 1
    smelting_output_distribution = autonomous_smelting_cells >= 2

    steam_physical = bool(pumps and boilers and engines)
    fuel_distribution = bool(
        drills and belts and inserters and boiler_has_inserter and boiler_has_belt
    )
    electric_distribution = bool(steam_physical and poles and consumers)
    smelting_distribution = bool(
        drills
        and furnaces
        and belts
        and inserters
        and furnace_has_inserter
        and furnace_has_belt
        and smelting_output_distribution
    )
    if interventions is None:
        manual_harvest: int | None = None
        manual_transfer: int | None = None
        manual_craft: int | None = None
        no_manual_logistics: bool | None = None
    else:
        manual_harvest = int(interventions.get("manual_harvest_calls", 0) or 0)
        manual_transfer = int(interventions.get("manual_transfer_calls", 0) or 0)
        manual_craft = int(interventions.get("manual_craft_calls", 0) or 0)
        no_manual_logistics = manual_harvest + manual_transfer == 0

    soak_long_enough: bool | None = (
        None if soak_runtime_s is None else float(soak_runtime_s) >= 60.0
    )
    # Zero manual logistics only counts over a window long enough to be
    # evidence: no agent call during no time at all proves nothing.
    zero_manual_logistics = _all_true(soak_long_enough, no_manual_logistics)

    fuel_consumers = [
        *boilers,
        *furnaces,
        *by_name.get("burner-mining-drill", []),
        *by_name.get("burner-inserter", []),
    ]
    power_consumers = [*consumers, *inserters]
    # Structure first: with no fuel burner or no electric consumer the gate is
    # a measured failure. With the structure present but no status reported,
    # the zero starvation count is an absence of measurement, not health.
    if not fuel_consumers:
        healthy_fuel: bool | None = False
    elif not _status_reported(fuel_consumers):
        healthy_fuel = None
    else:
        healthy_fuel = no_fuel == 0
    if not power_consumers:
        healthy_power: bool | None = False
    elif not _status_reported(power_consumers):
        healthy_power = None
    else:
        healthy_power = no_power == 0

    # A recent flow-statistics spike is evidence that material moved, not that
    # the producing chain still exists. "Live" autonomy therefore requires a
    # compatible physical topology, a healthy energy state and a full
    # zero-manual soak window in addition to positive production.
    coal_chain_live = _all_true(
        _positive_rate(rates, "coal"),
        fuel_distribution,
        healthy_fuel,
        zero_manual_logistics,
    )
    iron_chain_live = _all_true(
        _positive_rate(rates, "iron-plate"),
        smelting_distribution,
        healthy_fuel,
        zero_manual_logistics,
    )
    copper_chain_live = _all_true(
        _positive_rate(rates, "copper-plate"),
        smelting_distribution,
        healthy_fuel,
        zero_manual_logistics,
    )
    producing_material = _all_true(
        coal_chain_live,
        iron_chain_live,
        copper_chain_live,
    )
    producing_industry = _all_true(
        electric_distribution,
        healthy_power,
        zero_manual_logistics,
        _any_positive_rate(
            rates,
            (
                "iron-gear-wheel",
                "electronic-circuit",
                "automation-science-pack",
                "logistic-science-pack",
            ),
        ),
    )

    topology: dict[str, Any] = {
        "steam_physical": steam_physical,
        "fuel_distribution": fuel_distribution,
        "electric_distribution": electric_distribution,
        "smelting_distribution": smelting_distribution,
        "smelting_output_distribution": smelting_output_distribution,
        "autonomous_smelting_cells": autonomous_smelting_cells,
        "producing_material": producing_material,
        "coal_chain_live": coal_chain_live,
        "iron_chain_live": iron_chain_live,
        "copper_chain_live": copper_chain_live,
        "producing_industry": producing_industry,
        "zero_manual_logistics": zero_manual_logistics,
        "healthy_fuel": healthy_fuel,
        "healthy_power": healthy_power,
        "soak_long_enough": soak_long_enough,
    }

    capabilities: set[str] = set()
    if drills:
        capabilities.add("extraction_demonstrated")
    if steam_physical:
        capabilities.add("steam_generation_demonstrated")
    if fuel_distribution:
        capabilities.add("fuel_logistics_physical")
    if smelting_distribution:
        capabilities.add("smelting_logistics_physical")
    if electric_distribution:
        capabilities.add("electric_distribution_physical")
    if producing_industry is True:
        capabilities.add("powered_industry_live")

    required = tuple(topology[name] for name in CLOSED_LOOP_GATES)
    # Lower bound: a gate that was not measured is not credited. The gates that
    # were not measured travel with the evidence in `unmeasured_gates`.
    score = sum(value is True for value in required) / len(required)
    closed_loop = _all_true(*required)

    if closed_loop is True:
        level = "closed_loop"
        capabilities.add("closed_loop_autonomous_factory")
    elif (
        steam_physical
        or fuel_distribution
        or smelting_distribution
        or producing_material is True
    ):
        level = "semi_autonomous"
    elif drills or furnaces or boilers:
        level = "manual_bootstrap"
    else:
        level = "none"

    return AutonomyEvidence(
        level=level,
        score=score,
        soak_runtime_s=(
            None if soak_runtime_s is None else max(0.0, float(soak_runtime_s))
        ),
        manual_harvest_calls=manual_harvest,
        manual_transfer_calls=manual_transfer,
        manual_craft_calls=manual_craft,
        assisted_navigation_count=max(0, int(assisted_navigation_count)),
        no_fuel_entities=no_fuel,
        no_power_entities=no_power,
        belt_count=len(belts),
        inserter_count=len(inserters),
        producer_count=len(drills),
        capabilities=frozenset(capabilities),
        topology=topology,
        autonomous_rates_per_s=rates,
    )
