"""FLE compiler for the F5-C coal self-sufficiency transaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from fle.env.game_types import Prototype, Resource

from factorio_ai_lab.cortex.structural_prepare import StructuralOperation


def _factorio_name(member: Any) -> str | None:
    value=getattr(member,"value",None)
    if isinstance(value,tuple) and value and isinstance(value[0],str):
        return value[0]
    return value if isinstance(value,str) else None


def _enum_symbol(enum: Any,name: str,prefix: str) -> str:
    matches=[
        member_name
        for member_name,member in enum.__members__.items()
        if _factorio_name(member)==name
    ]
    if not matches:
        raise ValueError(f"no FLE {prefix} member for {name!r}")
    return f"{prefix}.{matches[0]}"


def _prototype(name: str) -> str:
    return _enum_symbol(Prototype,name,"Prototype")


def _resource(name: str) -> str:
    matches: list[str]=[]
    for member_name in dir(Resource):
        if member_name.startswith("_"):
            continue
        member=getattr(Resource,member_name,None)
        factorio_name=(
            member[0]
            if isinstance(member,tuple)
            and member
            and isinstance(member[0],str)
            else _factorio_name(member)
        )
        if factorio_name==name:
            matches.append(member_name)
    if not matches:
        raise ValueError(f"no FLE Resource member for {name!r}")
    return f"Resource.{matches[0]}"


def _position(raw: Mapping[str,Any]) -> str:
    x=raw.get("x")
    y=raw.get("y")
    if not isinstance(x,Real) or not isinstance(y,Real):
        raise TypeError(f"invalid position {dict(raw)!r}")
    return f"Position(x={float(x)!r},y={float(y)!r})"


def _positive_int(params: Mapping[str,Any],name: str) -> int:
    value=params.get(name)
    if (
        not isinstance(value,Real)
        or isinstance(value,bool)
        or int(value)<=0
        or float(value)!=float(int(value))
    ):
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _harvest_lines(resources: object) -> list[str]:
    if not isinstance(resources,Sequence) or isinstance(resources,(str,bytes)):
        raise TypeError("coal transaction requires bootstrap_resources")
    lines: list[str]=[]
    for index,raw in enumerate(resources):
        if not isinstance(raw,Mapping):
            raise TypeError("coal bootstrap resource row must be a mapping")
        amount=_positive_int(raw,"quantity")
        radius=raw.get("radius")
        radius_clause=""
        if radius is not None:
            if (
                not isinstance(radius,Real)
                or isinstance(radius,bool)
                or float(radius)<=0
            ):
                raise ValueError("coal bootstrap radius must be positive")
            radius_clause=f", radius={float(radius)!r}"
        position=raw.get("position")
        waypoints=raw.get("validated_path_waypoints")
        if not isinstance(position,Mapping):
            raise TypeError("coal bootstrap resource requires validated position")
        if (
            not isinstance(waypoints,int)
            or isinstance(waypoints,bool)
            or waypoints<=0
        ):
            raise ValueError(
                "coal bootstrap resource requires positive validated path waypoints"
            )
        var=f"cortex_coal_bootstrap_resource_{index}"
        lines.extend((
            f"{var}={_position(position)}",
            f"cortex_fast_reposition({var})",
            "harvest_resource(",
            f"    {var},",
            f"    quantity={amount}{radius_clause},",
            ")",
        ))
    return lines


def compile_coal_self_sufficiency(
    operation: StructuralOperation,
) -> list[str]:
    params=operation.parameters
    target=params.get("target_position")
    iron_extractor=params.get("incumbent_iron_extractor_position")
    iron_buffer=params.get("incumbent_iron_buffer_position")
    if not isinstance(target,Mapping):
        raise TypeError("coal transaction requires target_position")
    if not isinstance(iron_extractor,Mapping) or not isinstance(iron_buffer,Mapping):
        raise TypeError("coal transaction requires incumbent iron positions")

    furnaces=_positive_int(params,"bootstrap_furnace_quantity")
    iron_ore=_positive_int(params,"bootstrap_iron_ore_quantity")
    smelt_coal=_positive_int(params,"bootstrap_smelting_coal")
    smelt_seconds=_positive_int(params,"bootstrap_smelt_seconds")
    seed=_positive_int(params,"bootstrap_seed_coal")
    seed_window=_positive_int(params,"seed_window_seconds")
    endogenous_window=_positive_int(params,"endogenous_window_seconds")
    transfer_min=_positive_int(params,"endogenous_transfer_min")
    if seed!=1:
        raise ValueError("F5 coal v1 requires exactly one bootstrap seed coal")
    if transfer_min<2:
        raise ValueError(
            "F5 coal v1 requires transfer to at least two fuel consumers"
        )

    lines=_harvest_lines(params.get("bootstrap_resources"))
    lines.extend((
        f"craft_item({_prototype('stone-furnace')}, quantity={furnaces})",
        "cortex_coal_bootstrap_furnace=place_entity_next_to(",
        f"    {_prototype('stone-furnace')},",
        "    player_location,",
        "    direction=Direction.RIGHT,",
        ")",
        "cortex_coal_bootstrap_furnace=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_bootstrap_furnace,",
        f"    quantity={smelt_coal},",
        ")",
        "cortex_coal_bootstrap_furnace=insert_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_coal_bootstrap_furnace,",
        f"    quantity={iron_ore},",
        ")",
        f"sleep({smelt_seconds})",
        (
            "cortex_coal_bootstrap_plate_count=inspect_inventory("
            "cortex_coal_bootstrap_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "if cortex_coal_bootstrap_plate_count < 9:",
        (
            "    raise RuntimeError("
            "'coal bootstrap smelting produced fewer than 9 iron plates')"
        ),
        "extract_item(",
        f"    {_prototype('iron-plate')},",
        "    cortex_coal_bootstrap_furnace,",
        "    quantity=cortex_coal_bootstrap_plate_count,",
        ")",
        "pickup_entity(cortex_coal_bootstrap_furnace)",
        f"craft_item({_prototype('burner-mining-drill')}, quantity=1)",
        f"craft_item({_prototype('wooden-chest')}, quantity=2)",
        f"cortex_fast_reposition({_position(target)})",
        "cortex_coal_extractor=place_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    position={_position(target)},",
        "    direction=Direction.DOWN,",
        ")",
        "cortex_coal_buffer=place_entity_next_to(",
        f"    {_prototype('wooden-chest')},",
        "    cortex_coal_extractor.position,",
        "    direction=Direction.DOWN,",
        ")",
        "cortex_bootstrap_quarantine=place_entity_next_to(",
        f"    {_prototype('wooden-chest')},",
        "    cortex_coal_extractor.position,",
        "    direction=Direction.UP,",
        ")",
        (
            "cortex_bootstrap_total=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        f"cortex_bootstrap_keep={seed}",
        (
            "cortex_bootstrap_quarantine_count=max("
            "0,cortex_bootstrap_total-cortex_bootstrap_keep)"
        ),
        "if cortex_bootstrap_quarantine_count>0:",
        "    cortex_bootstrap_quarantine=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_bootstrap_quarantine,",
        "        quantity=cortex_bootstrap_quarantine_count,",
        "    )",
        (
            "cortex_seed_available=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_seed_available != {seed}:",
        (
            "    raise RuntimeError("
            "'coal bootstrap quarantine did not leave exactly one seed')"
        ),
        "cortex_coal_extractor=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_extractor,",
        f"    quantity={seed},",
        ")",
        "cortex_incumbent_iron_extractor=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {_position(iron_extractor)},",
        ")",
        "cortex_incumbent_iron_buffer=get_entity(",
        f"    {_prototype('wooden-chest')},",
        f"    {_position(iron_buffer)},",
        ")",
        (
            "cortex_incumbent_iron_bootstrap_fuel=inspect_inventory("
            "cortex_incumbent_iron_extractor)"
            f"[{_prototype('coal')}]"
        ),
        "cortex_incumbent_iron_bootstrap_removed=0",
        "if cortex_incumbent_iron_bootstrap_fuel>0:",
        "    cortex_incumbent_iron_bootstrap_removed=extract_item(",
        f"        {_prototype('coal')},",
        "        cortex_incumbent_iron_extractor,",
        "        quantity=cortex_incumbent_iron_bootstrap_fuel,",
        "    )",
        "if cortex_incumbent_iron_bootstrap_removed>0:",
        "    cortex_bootstrap_quarantine=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_bootstrap_quarantine,",
        "        quantity=cortex_incumbent_iron_bootstrap_removed,",
        "    )",
        (
            "cortex_incumbent_iron_bootstrap_inventory_remaining="
            "inspect_inventory(cortex_incumbent_iron_extractor)"
            f"[{_prototype('coal')}]"
        ),
        f"sleep({seed_window})",
        (
            "cortex_seed_fuel_remaining=inspect_inventory("
            "cortex_coal_extractor)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_seed_phase_count=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_iron_buffer_pre_endogenous=inspect_inventory("
            "cortex_incumbent_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        "cortex_endogenous_transfer=0",
        f"if cortex_seed_phase_count>={transfer_min}:",
        "    cortex_endogenous_transfer=extract_item(",
        f"        {_prototype('coal')},",
        "        cortex_coal_buffer,",
        "        quantity=min(3,cortex_seed_phase_count),",
        "    )",
        "cortex_coal_self_refuel=0",
        f"if inspect_inventory()[{_prototype('coal')}]>=1:",
        "    cortex_coal_extractor=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_coal_extractor,",
        "        quantity=1,",
        "    )",
        "    cortex_coal_self_refuel=1",
        "cortex_iron_endogenous_refuel=0",
        f"if inspect_inventory()[{_prototype('coal')}]>=1:",
        "    cortex_incumbent_iron_extractor=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_incumbent_iron_extractor,",
        "        quantity=1,",
        "    )",
        "    cortex_iron_endogenous_refuel=1",
        (
            "cortex_endogenous_player_remainder=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "if cortex_endogenous_player_remainder>0:",
        "    cortex_coal_buffer=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_coal_buffer,",
        "        quantity=cortex_endogenous_player_remainder,",
        "    )",
        (
            "cortex_coal_stock_before=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"sleep({endogenous_window})",
        (
            "cortex_coal_stock_after=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_coal_endogenous_growth=max("
            "0,cortex_coal_stock_after-cortex_coal_stock_before)"
        ),
        (
            "cortex_iron_buffer_after=inspect_inventory("
            "cortex_incumbent_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_incumbent_iron_buffer_growth=max("
            "0,cortex_iron_buffer_after-cortex_iron_buffer_pre_endogenous)"
        ),
        "cortex_coal_extractor_exists=cortex_coal_extractor is not None",
        (
            "cortex_coal_mined=("
            "cortex_seed_phase_count>0 and cortex_coal_endogenous_growth>0)"
        ),
        (
            "cortex_endogenous_coal_reaches_fuel_consumer=("
            "cortex_coal_self_refuel>=1 and "
            "cortex_iron_endogenous_refuel>=1)"
        ),
        (
            "cortex_external_bootstrap_fuel_retired=("
            "cortex_bootstrap_total>=1 and "
            "cortex_bootstrap_quarantine_count=="
            "cortex_bootstrap_total-cortex_bootstrap_keep and "
            "cortex_seed_fuel_remaining==0 and "
            "cortex_incumbent_iron_bootstrap_inventory_remaining==0)"
        ),
        (
            "cortex_incumbent_iron_survives=("
            "cortex_incumbent_iron_extractor is not None and "
            "cortex_incumbent_iron_buffer_growth>0)"
        ),
        (
            "print({'coal_mined':cortex_coal_mined,"
            "'endogenous_coal_reaches_fuel_consumer':"
            "cortex_endogenous_coal_reaches_fuel_consumer,"
            "'external_bootstrap_fuel_retired':"
            "cortex_external_bootstrap_fuel_retired,"
            "'iron_bootstrap_removed':"
            "cortex_incumbent_iron_bootstrap_removed,"
            "'iron_bootstrap_remaining':"
            "cortex_incumbent_iron_bootstrap_inventory_remaining,"
            "'coal_endogenous_growth':cortex_coal_endogenous_growth,"
            "'iron_buffer_growth':cortex_incumbent_iron_buffer_growth})"
        ),
    ))
    return lines
