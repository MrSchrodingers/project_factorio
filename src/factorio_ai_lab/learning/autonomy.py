from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

_BAD_FUEL_STATUSES = {"no_fuel"}
_BAD_POWER_STATUSES = {
    "no_power",
    "low_power",
    "not_plugged_in_electric_network",
    "not_connected",
}
_INSERTERS = {"burner-inserter", "inserter", "fast-inserter", "long-handed-inserter"}
_BELTS = {"transport-belt", "fast-transport-belt", "express-transport-belt"}


@dataclass(frozen=True)
class AutonomyEvidence:
    level: str
    score: float
    soak_runtime_s: float
    manual_harvest_calls: int = 0
    manual_transfer_calls: int = 0
    manual_craft_calls: int = 0
    assisted_navigation_count: int = 0
    no_fuel_entities: int = 0
    no_power_entities: int = 0
    belt_count: int = 0
    inserter_count: int = 0
    producer_count: int = 0
    capabilities: frozenset[str] = field(default_factory=frozenset)
    topology: Mapping[str, bool] = field(default_factory=dict)
    autonomous_rates_per_s: Mapping[str, float] = field(default_factory=dict)

    @property
    def manual_logistics_calls(self) -> int:
        return self.manual_harvest_calls + self.manual_transfer_calls

    @property
    def closed_loop(self) -> bool:
        return self.level == "closed_loop"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = sorted(self.capabilities)
        payload["manual_logistics_calls"] = self.manual_logistics_calls
        payload["closed_loop"] = self.closed_loop
        payload["score"] = round(self.score, 6)
        payload["autonomous_rates_per_s"] = {
            key: round(float(value), 8)
            for key, value in sorted(self.autonomous_rates_per_s.items())
        }
        return payload


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
    soak_runtime_s: float = 0.0,
    assisted_navigation_count: int = 0,
) -> AutonomyEvidence:
    interventions = interventions or {}
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
        str(entity.get("status", "")) in _BAD_FUEL_STATUSES
        for entity in rows
    )
    no_power = sum(
        str(entity.get("status", "")) in _BAD_POWER_STATUSES
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
    manual_harvest = int(interventions.get("manual_harvest_calls", 0) or 0)
    manual_transfer = int(interventions.get("manual_transfer_calls", 0) or 0)
    manual_craft = int(interventions.get("manual_craft_calls", 0) or 0)
    zero_manual_logistics = (
        soak_runtime_s >= 60.0
        and manual_harvest + manual_transfer == 0
    )
    fuel_consumers_present = bool(
        boilers
        or furnaces
        or by_name.get("burner-mining-drill", [])
        or by_name.get("burner-inserter", [])
    )
    power_consumers_present = bool(consumers or inserters)
    healthy_fuel = fuel_consumers_present and no_fuel == 0
    healthy_power = power_consumers_present and no_power == 0

    # A recent flow-statistics spike is evidence that material moved, not that
    # the producing chain still exists. "Live" autonomy therefore requires a
    # compatible physical topology, a healthy energy state and a full
    # zero-manual soak window in addition to positive production.
    coal_chain_live = bool(
        rates.get("coal", 0.0) > 0
        and fuel_distribution
        and healthy_fuel
        and zero_manual_logistics
    )
    iron_chain_live = bool(
        rates.get("iron-plate", 0.0) > 0
        and smelting_distribution
        and healthy_fuel
        and zero_manual_logistics
    )
    copper_chain_live = bool(
        rates.get("copper-plate", 0.0) > 0
        and smelting_distribution
        and healthy_fuel
        and zero_manual_logistics
    )
    producing_material = (
        coal_chain_live
        and iron_chain_live
        and copper_chain_live
    )
    producing_industry = bool(
        electric_distribution
        and healthy_power
        and zero_manual_logistics
        and any(
            rates.get(item, 0.0) > 0
            for item in (
                "iron-gear-wheel",
                "electronic-circuit",
                "automation-science-pack",
                "logistic-science-pack",
            )
        )
    )

    topology = {
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
        "soak_long_enough": soak_runtime_s >= 60.0,
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
    if producing_industry:
        capabilities.add("powered_industry_live")

    required = (
        topology["fuel_distribution"],
        topology["electric_distribution"],
        topology["smelting_distribution"],
        topology["zero_manual_logistics"],
        topology["healthy_fuel"],
        topology["healthy_power"],
        topology["soak_long_enough"],
        topology["producing_material"],
    )
    score = sum(bool(value) for value in required) / len(required)

    if all(required):
        level = "closed_loop"
        capabilities.add("closed_loop_autonomous_factory")
    elif (
        steam_physical
        or fuel_distribution
        or smelting_distribution
        or producing_material
    ):
        level = "semi_autonomous"
    elif drills or furnaces or boilers:
        level = "manual_bootstrap"
    else:
        level = "none"

    return AutonomyEvidence(
        level=level,
        score=score,
        soak_runtime_s=max(0.0, float(soak_runtime_s)),
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
