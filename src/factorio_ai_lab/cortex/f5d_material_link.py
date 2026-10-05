"""Pure F5-D plan for linking a buffered producer to an existing processor."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.structural import plan_processing_for_buffered_output
from factorio_ai_lab.learning.factory_graph import build_factory_graph, node_id
from factorio_ai_lab.learning.repair_loop import INTENT_PLACE_PROCESSING, RepairAction
from factorio_ai_lab.planning.delivery import (
    MODE_BELT,
    MODE_INSERTER,
    DeliveryLink,
    plan_delivery,
)
from factorio_ai_lab.planning.footprints import blocked_tiles, entity_tiles
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog


@dataclass(frozen=True)
class MaterialLinkPlan:
    producer_id: str
    material: str
    product: str
    source_buffer: Mapping[str, Any]
    target_processor: Mapping[str, Any]
    delivery: DeliveryLink
    construction_items: Mapping[str, int]
    plate_requirements: Mapping[str, int]
    craft_sequence: tuple[Mapping[str, Any], ...]
    bootstrap: Mapping[str, Any]
    actuator: Mapping[str, Any]
    baseline_entities: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "producer_id": self.producer_id,
            "material": self.material,
            "product": self.product,
            "source_buffer": dict(self.source_buffer),
            "target_processor": dict(self.target_processor),
            "delivery": self.delivery.to_dict(),
            "construction_items": dict(self.construction_items),
            "plate_requirements": dict(self.plate_requirements),
            "craft_sequence": [dict(row) for row in self.craft_sequence],
            "bootstrap": dict(self.bootstrap),
            "actuator": dict(self.actuator),
            "baseline_entities": [dict(row) for row in self.baseline_entities],
        }


def _position(entity: Mapping[str, Any]) -> dict[str, float]:
    raw = entity.get("position")
    if not isinstance(raw, Mapping):
        raise TypeError("entity position unavailable")
    return {"x": float(raw["x"]), "y": float(raw["y"])}


def _contents_count(entity: Mapping[str, Any], item: str, field: str) -> int:
    raw = entity.get(field)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return 0
    return sum(
        int(row.get("count") or 0)
        for row in raw
        if isinstance(row, Mapping) and row.get("name") == item
    )


def _craft_requirements(
    catalog: RuntimeFactorioCatalog,
    item: str,
    quantity: int,
    *,
    stop_items: frozenset[str],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    raw: dict[str, int] = {}
    steps: list[dict[str, Any]] = []

    def expand(name: str, count: int) -> None:
        if count <= 0:
            return
        if name in stop_items:
            raw[name] = raw.get(name, 0) + int(count)
            return
        choice = catalog.recipe_choice(name)
        if choice is None or not choice.enabled:
            raise ValueError(f"no enabled recipe for construction item {name!r}")
        if choice.spec.category != "crafting":
            raise ValueError(
                f"construction dependency {name!r} requires non-character "
                f"category {choice.spec.category!r}"
            )
        crafts = math.ceil(float(count) / float(choice.product_amount))
        for ingredient in choice.spec.ingredients:
            needed = math.ceil(float(ingredient.count) * crafts - 1e-12)
            expand(ingredient.item, needed)
        steps.append(
            {
                "item": name,
                "quantity": int(count),
                "crafts": int(crafts),
                "recipe": choice.recipe_name,
            }
        )

    expand(item, int(quantity))
    return raw, steps


def _world_index(
    entities: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    return {
        node_id(row, index): row
        for index, row in enumerate(entities)
        if isinstance(row, Mapping)
    }


def _arm_positions(delivery: DeliveryLink) -> tuple[tuple[float,float],...]:
    rows=[]
    for arm in (delivery.lift,delivery.drop):
        if arm is not None:
            rows.append((float(arm.position[0]),float(arm.position[1])))
    return tuple(rows)


def _small_pole_covers(
    position: tuple[float,float],
    entities: Sequence[Mapping[str,Any]],
) -> bool:
    """Fail-closed local power proof for the promoted small-pole topology."""
    x,y=position
    for row in entities:
        if row.get("name")!="small-electric-pole":
            continue
        network=row.get("network_id")
        if network is None:
            continue
        try:
            pos=_position(row)
        except (KeyError,TypeError,ValueError):
            continue
        if abs(pos["x"]-x)<=2.5 and abs(pos["y"]-y)<=2.5:
            return True
    return False


def _select_delivery_actuator(
    delivery: DeliveryLink,
    *,
    entities: Sequence[Mapping[str,Any]],
    catalog: RuntimeFactorioCatalog,
) -> tuple[str,dict[str,Any] | None] | None:
    arms=_arm_positions(delivery)
    if not arms:
        return None
    local_power=all(_small_pole_covers(pos,entities) for pos in arms)
    if local_power:
        energy=catalog.machine_energy("inserter")
        if energy.source_measured and energy.source_type=="electric":
            return (
                "inserter",
                {
                    "energy_source":"electric",
                    "local_power_proven":True,
                    "selection":"measured_local_small_pole_coverage",
                },
            )

    actuator="burner-inserter"
    energy=catalog.machine_energy(actuator)
    fuel=catalog.fuel("coal")
    if (
        not energy.burner
        or not energy.energy_usage_measured
        or energy.energy_usage_per_tick_j is None
        or fuel is None
        or not fuel.measured
        or fuel.fuel_value_j is None
        or not fuel.compatible_with(energy.fuel_categories)
    ):
        return None
    horizon_s=120.0
    margin=1.25
    joules=energy.energy_usage_per_tick_j*60.0*horizon_s*margin
    units=max(1,math.ceil(joules/fuel.fuel_value_j))
    return (
        actuator,
        {
            "energy_source":"burner",
            "local_power_proven":False,
            "selection":"burner_fail_closed_without_measured_local_power",
            "fuel_item":"coal",
            "fuel_units_per_actuator":units,
            "horizon_s":horizon_s,
            "margin":margin,
            "energy_usage_per_tick_j":energy.energy_usage_per_tick_j,
            "fuel_value_j":fuel.fuel_value_j,
        },
    )


def _richest_coal_source(
    entities: Sequence[Mapping[str,Any]],
    *,
    required: int,
    reserve: int,
) -> Mapping[str,Any] | None:
    candidates=[]
    for row in entities:
        if row.get("name")!="wooden-chest":
            continue
        coal=_contents_count(row,"coal","contents")
        if coal<required+reserve:
            continue
        try:
            pos=_position(row)
        except (KeyError,TypeError,ValueError):
            continue
        candidates.append((coal,float(pos["y"]),float(pos["x"]),row))
    if not candidates:
        return None
    _,_,_,source=max(
        candidates,
        key=lambda value:(value[0],-value[1],-value[2]),
    )
    return source


def plan_existing_processing_link(
    snapshot: Mapping[str, Any],
    *,
    action: RepairAction,
    catalog: RuntimeFactorioCatalog,
    belt_budget: int = 32,
) -> MaterialLinkPlan | None:
    entities_raw = snapshot.get("entities")
    if not isinstance(entities_raw, Sequence) or isinstance(
        entities_raw, (str, bytes)
    ):
        return None
    entities = tuple(row for row in entities_raw if isinstance(row, Mapping))
    targets = tuple(action.targets or ())
    if not targets:
        return None

    probe = ActionRequest(
        action_id="f5d-material-link:probe",
        family=ActionFamily.PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        provenance=ActionProvenance(
            requested_by="f5d-material-link",
            source_component="factorio_ai_lab.cortex.f5d_material_link",
            code_revision="pure-plan",
            run_id="pure-plan",
        ),
        arguments={"producers": list(targets)},
        targets=targets,
    )
    graph_plan = plan_processing_for_buffered_output(
        probe,
        graph=build_factory_graph(entities),
        world_entities=entities,
        catalog=catalog,
        available={},
        belt_budget=belt_budget,
    )
    if not graph_plan.branches:
        return None

    index = _world_index(entities)
    blocked = blocked_tiles(entities, None)
    candidates: list[
        tuple[int, float, str, Any, Mapping[str, Any], DeliveryLink]
    ] = []
    for branch in graph_plan.branches:
        source = index.get(branch.source_buffer)
        if source is None:
            continue
        source_pos = _position(source)
        for processor_id, processor in index.items():
            if processor.get("name") != branch.processor:
                continue
            processor_pos = _position(processor)
            link = plan_delivery(
                source_tiles=entity_tiles(source, None),
                target_tiles=entity_tiles(processor, None),
                blocked=blocked,
                belt_budget=belt_budget,
            )
            if not link.builds:
                continue
            distance = math.hypot(
                source_pos["x"] - processor_pos["x"],
                source_pos["y"] - processor_pos["y"],
            )
            candidates.append(
                (
                    link.belt_count,
                    distance,
                    processor_id,
                    branch,
                    processor,
                    link,
                )
            )
    if not candidates:
        return None

    _, _, processor_id, branch, processor, delivery = min(
        candidates,
        key=lambda row: (row[0], row[1], row[2]),
    )
    source = index[branch.source_buffer]
    inserters = 1 if delivery.mode == MODE_INSERTER else 2
    belts = delivery.belt_count if delivery.mode == MODE_BELT else 0
    selected_actuator=_select_delivery_actuator(
        delivery,
        entities=entities,
        catalog=catalog,
    )
    if selected_actuator is None:
        return None
    actuator_name,actuator_details=selected_actuator
    actuator_details=dict(actuator_details or {})
    construction = {actuator_name: inserters}
    if belts:
        construction["transport-belt"] = belts

    plate_requirements: dict[str, int] = {}
    craft_sequence: list[dict[str, Any]] = []
    for item, count in sorted(construction.items()):
        raw, steps = _craft_requirements(
            catalog,
            item,
            count,
            stop_items=frozenset({"iron-plate", "copper-plate"}),
        )
        for material, quantity in raw.items():
            plate_requirements[material] = (
                plate_requirements.get(material, 0) + int(quantity)
            )
        craft_sequence.extend(steps)

    iron_needed = int(plate_requirements.get("iron-plate", 0))
    copper_needed = int(plate_requirements.get("copper-plate", 0))
    target_iron = _contents_count(processor, "iron-plate", "craft_output")
    iron_shortfall = max(0, iron_needed - target_iron)
    source_iron_ore = _contents_count(source, "iron-ore", "contents")
    if branch.material != "iron-ore" or branch.product != "iron-plate":
        return None
    if iron_shortfall > source_iron_ore:
        return None

    fuel_source: Mapping[str,Any] | None=None
    fuel_total=0
    if actuator_details.get("energy_source")=="burner":
        per_actuator=int(actuator_details.get("fuel_units_per_actuator") or 0)
        if per_actuator<=0:
            return None
        fuel_total=per_actuator*inserters
        fuel_source=_richest_coal_source(
            entities,
            required=fuel_total,
            reserve=100,
        )
        if fuel_source is None:
            return None
        fuel_pos=_position(fuel_source)
        actuator_details.update({
            "fuel_total":fuel_total,
            "fuel_source_reserve":100,
            "fuel_source":{
                "entity_name":"wooden-chest",
                "x":fuel_pos["x"],
                "y":fuel_pos["y"],
                "coal_before":_contents_count(fuel_source,"coal","contents"),
            },
        })

    copper_source: Mapping[str, Any] | None = None
    if copper_needed:
        copper_candidates = [
            row
            for row in entities
            if _contents_count(row, "copper-plate", "craft_output") >= copper_needed
        ]
        if not copper_candidates:
            return None
        copper_source = min(
            copper_candidates,
            key=lambda row: (
                math.hypot(
                    _position(row)["x"] - _position(source)["x"],
                    _position(row)["y"] - _position(source)["y"],
                ),
                float(_position(row)["y"]),
                float(_position(row)["x"]),
            ),
        )

    fuel_count = _contents_count(processor, "coal", "fuel")
    fuel_remaining = float(processor.get("fuel_remaining") or 0.0)
    if iron_shortfall and fuel_count <= 0 and fuel_remaining <= 0:
        return None

    bootstrap = {
        "iron_plate_needed": iron_needed,
        "iron_plate_available_before": target_iron,
        "iron_ore_to_smelt": iron_shortfall,
        "source_iron_ore_before": source_iron_ore,
        "copper_plate_needed": copper_needed,
        "copper_plate_source": (
            None
            if copper_source is None
            else {
                "node_id": next(
                    key for key, value in index.items() if value is copper_source
                ),
                "entity_name": str(copper_source.get("name") or ""),
                **_position(copper_source),
                "copper_plate_before": _contents_count(
                    copper_source, "copper-plate", "craft_output"
                ),
            }
        ),
        "smelt_wait_seconds": max(8, math.ceil(iron_shortfall * 3.2) + 5),
    }
    baseline_entities=tuple(
        {
            "entity_name":str(row.get("name") or ""),
            **_position(row),
        }
        for row in entities
        if row.get("name")!="character"
    )
    return MaterialLinkPlan(
        producer_id=branch.producers[0],
        material=branch.material,
        product=branch.product,
        source_buffer={
            "node_id": branch.source_buffer,
            "entity_name": str(source.get("name") or ""),
            **_position(source),
            "material_count": _contents_count(source, branch.material, "contents"),
        },
        target_processor={
            "node_id": processor_id,
            "entity_name": str(processor.get("name") or ""),
            **_position(processor),
            "fuel_count": fuel_count,
            "fuel_remaining": fuel_remaining,
        },
        delivery=delivery,
        construction_items=construction,
        plate_requirements=plate_requirements,
        craft_sequence=tuple(craft_sequence),
        bootstrap=bootstrap,
        actuator={
            "name":actuator_name,
            **actuator_details,
        },
        baseline_entities=baseline_entities,
    )
