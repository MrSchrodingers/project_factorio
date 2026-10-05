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
from factorio_ai_lab.learning.factory_graph import (
    MATERIAL_RELATIONS,
    build_factory_graph,
    node_id,
)
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



def _contents_map(entity: Mapping[str,Any],field: str) -> dict[str,int]:
    raw=entity.get(field)
    if not isinstance(raw,Sequence) or isinstance(raw,(str,bytes)):
        return {}
    result: dict[str,int]={}
    for row in raw:
        if not isinstance(row,Mapping):
            continue
        name=row.get("name")
        count=row.get("count")
        if (
            not isinstance(name,str)
            or isinstance(count,bool)
            or not isinstance(count,(int,float))
        ):
            continue
        value=max(0,int(count))
        if value:
            result[name]=result.get(name,0)+value
    return result


def _material_adjacency(graph: Mapping[str,Any]) -> dict[str,set[str]]:
    adjacency: dict[str,set[str]]={}
    rows=graph.get("edges")
    if not isinstance(rows,Sequence) or isinstance(rows,(str,bytes)):
        return adjacency
    for row in rows:
        if not isinstance(row,Mapping) or row.get("relation") not in MATERIAL_RELATIONS:
            continue
        source=row.get("source")
        target=row.get("target")
        if isinstance(source,str) and isinstance(target,str):
            adjacency.setdefault(source,set()).add(target)
    return adjacency


def _reaches(adjacency: Mapping[str,set[str]],source: str,target: str) -> bool:
    queue=[source]
    seen={source}
    while queue:
        current=queue.pop(0)
        for nxt in adjacency.get(current,set()):
            if nxt==target:
                return True
            if nxt in seen:
                continue
            seen.add(nxt)
            queue.append(nxt)
    return False


def _source_contaminants(
    source: Mapping[str,Any],
    *,
    material: str,
    entities: Sequence[Mapping[str,Any]],
) -> tuple[Mapping[str,Any],...] | None:
    contaminants={
        name:count
        for name,count in _contents_map(source,"contents").items()
        if name!=material and count>0
    }
    if not contaminants:
        return ()
    labs=[row for row in entities if row.get("name")=="lab"]
    if not labs:
        return None
    source_pos=_position(source)
    lab=min(
        labs,
        key=lambda row:(
            math.hypot(
                _position(row)["x"]-source_pos["x"],
                _position(row)["y"]-source_pos["y"],
            ),
            _position(row)["y"],
            _position(row)["x"],
        ),
    )
    lab_pos=_position(lab)
    rows=[]
    for item,count in sorted(contaminants.items()):
        if not item.endswith("-science-pack"):
            return None
        rows.append({
            "item":item,
            "count":count,
            "sink":{
                "entity_name":"lab",
                "x":lab_pos["x"],
                "y":lab_pos["y"],
            },
        })
    return tuple(rows)


def _construction_iron_bootstrap(
    *,
    entities: Sequence[Mapping[str,Any]],
    index: Mapping[str,Mapping[str,Any]],
    graph: Mapping[str,Any],
    target_processor_id: str,
    target_processor: Mapping[str,Any],
    source: Mapping[str,Any],
    branch_material: str,
    iron_needed: int,
) -> Mapping[str,Any] | None:
    if iron_needed<=0:
        return {
            "mode":"not_required",
            "source":None,
            "iron_plate_needed":0,
            "iron_plate_available_before":0,
            "iron_ore_to_smelt":0,
            "iron_ore_source":None,
            "wait_seconds":0,
        }

    adjacency=_material_adjacency(graph)
    source_pos=_position(source)
    candidates=[]
    for processor_id,processor in index.items():
        if processor.get("name")!="stone-furnace":
            continue
        available=_contents_count(processor,"iron-plate","craft_output")
        fuel_count=_contents_count(processor,"coal","fuel")
        fuel_remaining=float(processor.get("fuel_remaining") or 0.0)
        fuel_ready=fuel_count>0 or fuel_remaining>0
        mode=None
        wait_seconds=0
        ore_to_smelt=0
        ore_source: Mapping[str,Any] | None=None

        if available>=iron_needed:
            mode="existing_stock"
        elif fuel_ready:
            shortfall=iron_needed-available
            linked_sources=[]
            for buffer_id,buffer in index.items():
                ore=_contents_count(buffer,"iron-ore","contents")
                if ore<shortfall:
                    continue
                if _reaches(adjacency,buffer_id,processor_id):
                    linked_sources.append((ore,buffer_id,buffer))
            if linked_sources:
                _,buffer_id,ore_source=max(
                    linked_sources,
                    key=lambda row:(row[0],row[1]),
                )
                mode="autonomous_wait"
                wait_seconds=max(8,math.ceil(shortfall*3.2)+5)
                ore_source={
                    "node_id":buffer_id,
                    "entity_name":str(ore_source.get("name") or ""),
                    **_position(ore_source),
                    "iron_ore_before":_contents_count(
                        ore_source,"iron-ore","contents"
                    ),
                    "autonomous_delivery":True,
                }
            elif (
                branch_material=="iron-ore"
                and processor_id==target_processor_id
                and _contents_count(source,"iron-ore","contents")>=shortfall
            ):
                mode="manual_smelt"
                wait_seconds=max(8,math.ceil(shortfall*3.2)+5)
                ore_to_smelt=shortfall
                ore_source={
                    "entity_name":str(source.get("name") or ""),
                    **source_pos,
                    "iron_ore_before":_contents_count(
                        source,"iron-ore","contents"
                    ),
                    "autonomous_delivery":False,
                }

        if mode is None:
            continue
        processor_pos=_position(processor)
        rank={"existing_stock":0,"autonomous_wait":1,"manual_smelt":2}[mode]
        candidates.append((
            rank,
            math.hypot(
                processor_pos["x"]-source_pos["x"],
                processor_pos["y"]-source_pos["y"],
            ),
            processor_id,
            {
                "mode":mode,
                "source":{
                    "node_id":processor_id,
                    "entity_name":"stone-furnace",
                    **processor_pos,
                    "iron_plate_before":available,
                    "fuel_count":fuel_count,
                    "fuel_remaining":fuel_remaining,
                },
                "iron_plate_needed":iron_needed,
                "iron_plate_available_before":available,
                "iron_ore_to_smelt":ore_to_smelt,
                "iron_ore_source":ore_source,
                "wait_seconds":wait_seconds,
            },
        ))
    if not candidates:
        return None
    return min(candidates,key=lambda row:(row[0],row[1],row[2]))[3]


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
    factory_graph=build_factory_graph(entities)
    graph_plan = plan_processing_for_buffered_output(
        probe,
        graph=factory_graph,
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
    if (branch.material,branch.product) not in {
        ("iron-ore","iron-plate"),
        ("copper-ore","copper-plate"),
    }:
        return None

    contaminants=_source_contaminants(
        source,
        material=branch.material,
        entities=entities,
    )
    if contaminants is None:
        return None

    construction_iron=_construction_iron_bootstrap(
        entities=entities,
        index=index,
        graph=factory_graph,
        target_processor_id=processor_id,
        target_processor=processor,
        source=source,
        branch_material=branch.material,
        iron_needed=iron_needed,
    )
    if construction_iron is None:
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
    if fuel_count <= 0 and fuel_remaining <= 0:
        return None

    bootstrap = {
        "iron_plate_needed": iron_needed,
        "construction_iron":dict(construction_iron),
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
        "source_contaminants":[dict(row) for row in contaminants],
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
