"""Compiler for a bounded F5-D persistent material link."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from fle.env.game_types import Prototype

from factorio_ai_lab.cortex.structural_prepare import StructuralOperation


def _prototype_name(member: Any) -> str | None:
    value=getattr(member,"value",None)
    if isinstance(value,tuple) and value and isinstance(value[0],str):
        return value[0]
    return value if isinstance(value,str) else None


def _prototype(name: str) -> str:
    matches=[
        key for key,member in Prototype.__members__.items()
        if _prototype_name(member)==name
    ]
    if not matches:
        raise ValueError(f"no FLE Prototype member for {name!r}")
    return f"Prototype.{matches[0]}"


def _position(raw: Mapping[str,Any]) -> str:
    x=raw.get("x"); y=raw.get("y")
    if (
        not isinstance(x,Real) or isinstance(x,bool)
        or not isinstance(y,Real) or isinstance(y,bool)
    ):
        raise TypeError(f"invalid position {dict(raw)!r}")
    return f"Position(x={float(x)!r},y={float(y)!r})"


def _direction(dx: int,dy: int) -> str:
    names={(0,-1):"UP",(0,1):"DOWN",(-1,0):"LEFT",(1,0):"RIGHT"}
    try:
        return names[(dx,dy)]
    except KeyError as exc:
        raise ValueError(f"non-cardinal belt step {(dx,dy)!r}") from exc


def _arm_lines(
    prefix: str,
    raw: Mapping[str,Any],
    *,
    actuator: str,
    fuel_item: str | None=None,
    fuel_units: int=0,
) -> list[str]:
    position=raw.get("position")
    direction=str(raw.get("direction") or "").upper()
    if not isinstance(position,Mapping):
        raise TypeError("delivery arm requires position")
    if direction not in {"UP","DOWN","LEFT","RIGHT"}:
        raise ValueError("delivery arm requires cardinal direction")
    lines=[
        f"move_to({_position(position)})",
        f"{prefix}=place_entity(",
        f"    {_prototype(actuator)},",
        f"    position={_position(position)},",
        f"    direction=Direction.{direction},",
        ")",
    ]
    if fuel_item is not None:
        if fuel_units<=0:
            raise ValueError("burner actuator fuel units must be positive")
        lines.extend([
            f"{prefix}=insert_item(",
            f"    {_prototype(fuel_item)},",
            f"    {prefix},",
            f"    quantity={fuel_units},",
            ")",
        ])
    return lines


def compile_autonomous_material_link(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    source=params.get("source_buffer")
    target=params.get("target_processor")
    delivery=params.get("delivery")
    bootstrap=params.get("bootstrap")
    sequence=params.get("craft_sequence")
    construction=params.get("construction_items")
    actuator=params.get("actuator")
    if not all(
        isinstance(row,Mapping)
        for row in (source,target,delivery,bootstrap,construction,actuator)
    ):
        raise TypeError("material link frozen plan is incomplete")
    if not isinstance(sequence,Sequence) or isinstance(sequence,(str,bytes)):
        raise TypeError("material link craft sequence unavailable")
    if str(source.get("entity_name") or "")!="wooden-chest":
        raise ValueError("material link source must be wooden-chest")
    if str(target.get("entity_name") or "")!="stone-furnace":
        raise ValueError("material link target must be stone-furnace")

    material_name=str(params.get("material") or "")
    product_name=str(params.get("product") or "")
    if (material_name,product_name) not in {
        ("iron-ore","iron-plate"),
        ("copper-ore","copper-plate"),
    }:
        raise ValueError(
            f"unsupported material-link transformation "
            f"{material_name!r}->{product_name!r}"
        )

    actuator_name=str(actuator.get("name") or "")
    if actuator_name not in {"inserter","burner-inserter"}:
        raise ValueError(f"unsupported material-link actuator {actuator_name!r}")
    energy_source=str(actuator.get("energy_source") or "")
    fuel_item: str | None=None
    fuel_units=0
    fuel_total=0
    fuel_source=None
    if energy_source=="burner":
        fuel_item=str(actuator.get("fuel_item") or "")
        fuel_units=int(actuator.get("fuel_units_per_actuator") or 0)
        fuel_total=int(actuator.get("fuel_total") or 0)
        fuel_source=actuator.get("fuel_source")
        if (
            actuator_name!="burner-inserter"
            or fuel_item!="coal"
            or fuel_units<=0
            or fuel_total<=0
            or not isinstance(fuel_source,Mapping)
        ):
            raise ValueError("invalid burner material-link actuator contract")
    elif energy_source!="electric":
        raise ValueError("material-link actuator energy source unavailable")

    iron_needed=int(bootstrap.get("iron_plate_needed") or 0)
    construction_iron=bootstrap.get("construction_iron")
    copper_needed=int(bootstrap.get("copper_plate_needed") or 0)
    contaminants=bootstrap.get("source_contaminants",[])
    if iron_needed<0 or copper_needed<0:
        raise ValueError("invalid material-link construction budget")
    if iron_needed and not isinstance(construction_iron,Mapping):
        raise TypeError("material link requires construction iron bootstrap")
    if not isinstance(contaminants,Sequence) or isinstance(
        contaminants,(str,bytes)
    ):
        raise TypeError("material link contaminants must be a sequence")

    source_pos=_position(source)
    target_pos=_position(target)
    lines=[
        f"cortex_link_source=get_entity({_prototype('wooden-chest')},{source_pos})",
        f"cortex_link_target=get_entity({_prototype('stone-furnace')},{target_pos})",
        (
            "cortex_link_source_before=inspect_inventory(cortex_link_source)"
            f"[{_prototype(material_name)}]"
        ),
        (
            "cortex_link_target_product_before=inspect_inventory(cortex_link_target)"
            f"[{_prototype(product_name)}]"
        ),
    ]

    if fuel_source is not None:
        fuel_pos=_position(fuel_source)
        reserve=int(actuator.get("fuel_source_reserve") or 0)
        if reserve<0:
            raise ValueError("material-link fuel reserve must be non-negative")
        lines.extend([
            f"cortex_link_fuel_source=get_entity({_prototype('wooden-chest')},{fuel_pos})",
            (
                "cortex_link_fuel_before=inspect_inventory(cortex_link_fuel_source)"
                f"[{_prototype('coal')}]"
            ),
            f"if cortex_link_fuel_before < {fuel_total+reserve}:",
            "    raise RuntimeError('endogenous coal below actuator fuel budget')",
            f"move_to({fuel_pos})",
            "cortex_link_fuel_drawn=extract_item(",
            f"    {_prototype('coal')},",
            "    cortex_link_fuel_source,",
            f"    quantity={fuel_total},",
            ")",
        ])
    else:
        lines.extend(["cortex_link_fuel_before=0","cortex_link_fuel_drawn=0"])

    for index,raw in enumerate(contaminants):
        if not isinstance(raw,Mapping):
            raise TypeError("material link contaminant row must be mapping")
        item=str(raw.get("item") or "")
        count=int(raw.get("count") or 0)
        sink=raw.get("sink")
        if (
            not item.endswith("-science-pack")
            or count<=0
            or not isinstance(sink,Mapping)
            or str(sink.get("entity_name") or "")!="lab"
        ):
            raise ValueError("unsupported material-link source contaminant")
        sink_pos=_position(sink)
        lines.extend([
            (
                f"cortex_link_contaminant_sink_{index}=get_entity("
                f"{_prototype('lab')},{sink_pos})"
            ),
            f"move_to({source_pos})",
            f"cortex_link_contaminant_extract_{index}=extract_item(",
            f"    {_prototype(item)},",
            "    cortex_link_source,",
            f"    quantity={count},",
            ")",
            f"move_to({sink_pos})",
            f"cortex_link_contaminant_insert_{index}=insert_item(",
            f"    {_prototype(item)},",
            f"    cortex_link_contaminant_sink_{index},",
            f"    quantity={count},",
            ")",
        ])

    if iron_needed:
        assert isinstance(construction_iron,Mapping)
        iron_source=construction_iron.get("source")
        if not isinstance(iron_source,Mapping):
            raise TypeError("construction iron source unavailable")
        iron_source_name=str(iron_source.get("entity_name") or "")
        if iron_source_name!="stone-furnace":
            raise ValueError("construction iron source must be stone-furnace")
        iron_source_pos=_position(iron_source)
        mode=str(construction_iron.get("mode") or "")
        wait_seconds=int(construction_iron.get("wait_seconds") or 0)
        ore_to_smelt=int(construction_iron.get("iron_ore_to_smelt") or 0)
        ore_source=construction_iron.get("iron_ore_source")
        lines.append(
            f"cortex_link_iron_source=get_entity("
            f"{_prototype('stone-furnace')},{iron_source_pos})"
        )
        if mode=="manual_smelt":
            if (
                ore_to_smelt<=0
                or not isinstance(ore_source,Mapping)
                or str(ore_source.get("entity_name") or "")!="wooden-chest"
            ):
                raise ValueError("manual construction smelt source unavailable")
            ore_source_pos=_position(ore_source)
            lines.extend([
                (
                    f"cortex_link_iron_ore_source=get_entity("
                    f"{_prototype('wooden-chest')},{ore_source_pos})"
                ),
                (
                    "cortex_link_iron_ore_before=inspect_inventory("
                    "cortex_link_iron_ore_source)"
                    f"[{_prototype('iron-ore')}]"
                ),
                f"if cortex_link_iron_ore_before < {ore_to_smelt}:",
                "    raise RuntimeError('endogenous iron ore below construction budget')",
                f"move_to({ore_source_pos})",
                "cortex_link_bootstrap_ore=extract_item(",
                f"    {_prototype('iron-ore')},",
                "    cortex_link_iron_ore_source,",
                f"    quantity={ore_to_smelt},",
                ")",
                f"move_to({iron_source_pos})",
                "cortex_link_iron_source=insert_item(",
                f"    {_prototype('iron-ore')},",
                "    cortex_link_iron_source,",
                f"    quantity={ore_to_smelt},",
                ")",
            ])
        elif mode not in {"autonomous_wait","existing_stock"}:
            raise ValueError(f"unsupported construction iron mode {mode!r}")

        if wait_seconds>0:
            lines.append(f"sleep({wait_seconds})")
        lines.extend([
            (
                "cortex_link_iron_ready=inspect_inventory(cortex_link_iron_source)"
                f"[{_prototype('iron-plate')}]"
            ),
            f"if cortex_link_iron_ready < {iron_needed}:",
            "    raise RuntimeError('endogenous iron plates did not reach link budget')",
            f"move_to({iron_source_pos})",
            "cortex_link_iron_drawn=extract_item(",
            f"    {_prototype('iron-plate')},",
            "    cortex_link_iron_source,",
            f"    quantity={iron_needed},",
            ")",
        ])
    else:
        lines.append("cortex_link_iron_drawn=0")

    copper_source=bootstrap.get("copper_plate_source")
    if copper_needed:
        if not isinstance(copper_source,Mapping):
            raise TypeError("copper plate source required by material link")
        copper_name=str(copper_source.get("entity_name") or "")
        if copper_name!="stone-furnace":
            raise ValueError("copper plate source must be stone-furnace")
        copper_pos=_position(copper_source)
        lines.extend([
            f"cortex_link_copper_source=get_entity({_prototype(copper_name)},{copper_pos})",
            (
                "cortex_link_copper_before=inspect_inventory(cortex_link_copper_source)"
                f"[{_prototype('copper-plate')}]"
            ),
            f"if cortex_link_copper_before < {copper_needed}:",
            "    raise RuntimeError('endogenous copper plates below link budget')",
            f"move_to({copper_pos})",
            "cortex_link_copper_drawn=extract_item(",
            f"    {_prototype('copper-plate')},",
            "    cortex_link_copper_source,",
            f"    quantity={copper_needed},",
            ")",
        ])
    else:
        lines.extend(["cortex_link_copper_before=0","cortex_link_copper_drawn=0"])

    lines.extend([
        (
            "cortex_link_source_preflow=inspect_inventory(cortex_link_source)"
            f"[{_prototype(material_name)}]"
        ),
        (
            "cortex_link_target_preflow=inspect_inventory(cortex_link_target)"
            f"[{_prototype(product_name)}]"
        ),
    ])

    for index,raw in enumerate(sequence):
        if not isinstance(raw,Mapping):
            raise TypeError("craft sequence row must be a mapping")
        item=str(raw.get("item") or "")
        quantity=int(raw.get("quantity") or 0)
        if not item or quantity<=0:
            raise ValueError("invalid craft sequence row")
        lines.extend([
            f"cortex_link_craft_{index}=craft_item(",
            f"    {_prototype(item)},",
            f"    quantity={quantity},",
            ")",
        ])

    lift=delivery.get("lift")
    drop=delivery.get("drop")
    path=delivery.get("path")
    mode=str(delivery.get("mode") or "")
    if not isinstance(lift,Mapping):
        raise TypeError("material link requires lift inserter")
    lines.extend(
        _arm_lines(
            "cortex_link_lift",
            lift,
            actuator=actuator_name,
            fuel_item=fuel_item,
            fuel_units=fuel_units,
        )
    )
    if mode=="belt":
        if not isinstance(path,Sequence) or isinstance(path,(str,bytes)) or not path:
            raise TypeError("belt material link requires path")
        points=[]
        for raw in path:
            if not isinstance(raw,Mapping):
                raise TypeError("belt path row must be a mapping")
            points.append((int(raw["x"]),int(raw["y"])))
        for index,(x,y) in enumerate(points):
            if index+1<len(points):
                nx,ny=points[index+1]
                direction=_direction(nx-x,ny-y)
            elif index>0:
                px,py=points[index-1]
                direction=_direction(x-px,y-py)
            else:
                direction="RIGHT"
            pos={"x":x+0.5,"y":y+0.5}
            lines.extend([
                f"move_to({_position(pos)})",
                f"cortex_link_belt_{index}=place_entity(",
                f"    {_prototype('transport-belt')},",
                f"    position={_position(pos)},",
                f"    direction=Direction.{direction},",
                ")",
            ])
        if not isinstance(drop,Mapping):
            raise TypeError("belt material link requires drop inserter")
        lines.extend(
            _arm_lines(
                "cortex_link_drop",
                drop,
                actuator=actuator_name,
                fuel_item=fuel_item,
                fuel_units=fuel_units,
            )
        )
    elif mode!="inserter":
        raise ValueError(f"unsupported material link mode {mode!r}")

    lines.extend([
        "sleep(20)",
        (
            "cortex_material_link_output_after=inspect_inventory(cortex_link_target)"
            f"[{_prototype(product_name)}]"
        ),
        (
            "cortex_material_link_source_after=inspect_inventory(cortex_link_source)"
            f"[{_prototype(material_name)}]"
        ),
        (
            "cortex_autonomous_material_link_succeeded=("
            "cortex_material_link_output_after>cortex_link_target_preflow "
            "and cortex_material_link_source_after<cortex_link_source_preflow)"
        ),
        (
            "print({'cortex_material_link_output_after':"
            "cortex_material_link_output_after,"
            "'cortex_material_link_source_after':cortex_material_link_source_after,"
            "'cortex_autonomous_material_link_succeeded':"
            "cortex_autonomous_material_link_succeeded})"
        ),
    ])
    return lines
