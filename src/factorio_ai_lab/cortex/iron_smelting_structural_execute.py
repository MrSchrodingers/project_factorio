"""Compiler for the F5-C iron-smelting transactional Option."""

from __future__ import annotations

from collections.abc import Mapping
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
        key
        for key,member in Prototype.__members__.items()
        if _prototype_name(member)==name
    ]
    if not matches:
        raise ValueError(f"no FLE Prototype member for {name!r}")
    return f"Prototype.{matches[0]}"


def _position(raw: Mapping[str,Any]) -> str:
    x=raw.get("x")
    y=raw.get("y")
    if (
        not isinstance(x,Real)
        or isinstance(x,bool)
        or not isinstance(y,Real)
        or isinstance(y,bool)
    ):
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


def compile_iron_smelting(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    furnace=params.get("furnace_position")
    stone=params.get("stone_route")
    iron_extractor=params.get("incumbent_iron_extractor_position")
    iron_buffer=params.get("incumbent_iron_buffer_position")
    coal_extractor=params.get("incumbent_coal_extractor_position")
    coal_buffer=params.get("incumbent_coal_buffer_position")
    for label,value in (
        ("furnace_position",furnace),
        ("stone_route",stone),
        ("incumbent_iron_extractor_position",iron_extractor),
        ("incumbent_iron_buffer_position",iron_buffer),
        ("incumbent_coal_extractor_position",coal_extractor),
        ("incumbent_coal_buffer_position",coal_buffer),
    ):
        if not isinstance(value,Mapping):
            raise TypeError(f"iron smelting requires {label}")

    stone_quantity=_positive_int(params,"stone_quantity")
    iron_ore_draw=_positive_int(params,"iron_ore_draw")
    coal_draw=_positive_int(params,"coal_draw")
    furnace_coal=_positive_int(params,"furnace_coal")
    iron_refuel=_positive_int(params,"iron_refuel_coal")
    coal_refuel=_positive_int(params,"coal_refuel_coal")
    probe_seconds=_positive_int(params,"working_probe_seconds")
    smelt_seconds=_positive_int(params,"smelt_window_seconds")
    survival_seconds=_positive_int(params,"survival_window_seconds")
    if smelt_seconds<=probe_seconds:
        raise ValueError("smelt_window_seconds must exceed working_probe_seconds")

    waypoints=stone.get("validated_path_waypoints")
    if (
        stone.get("resource")!="stone"
        or not isinstance(waypoints,int)
        or isinstance(waypoints,bool)
        or waypoints<=0
        or not isinstance(stone.get("position"),Mapping)
    ):
        raise ValueError("iron smelting stone route is not path validated")

    return [
        f"cortex_smelting_stone={_position(stone['position'])}",
        "cortex_fast_reposition(cortex_smelting_stone)",
        "harvest_resource(",
        "    cortex_smelting_stone,",
        f"    quantity={stone_quantity},",
        ")",
        f"craft_item({_prototype('stone-furnace')}, quantity=1)",
        "cortex_iron_extractor=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {_position(iron_extractor)},",
        ")",
        "cortex_iron_buffer=get_entity(",
        f"    {_prototype('wooden-chest')},",
        f"    {_position(iron_buffer)},",
        ")",
        "cortex_coal_extractor=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {_position(coal_extractor)},",
        ")",
        "cortex_coal_buffer=get_entity(",
        f"    {_prototype('wooden-chest')},",
        f"    {_position(coal_buffer)},",
        ")",
        (
            "cortex_iron_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_available=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_iron_available < {iron_ore_draw}:",
        "    raise RuntimeError('endogenous iron buffer below smelting draw')",
        f"if cortex_coal_available < {coal_draw}:",
        "    raise RuntimeError('endogenous coal buffer below smelting draw')",
        "cortex_iron_drawn=extract_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_buffer,",
        f"    quantity={iron_ore_draw},",
        ")",
        "cortex_coal_drawn=extract_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_buffer,",
        f"    quantity={coal_draw},",
        ")",
        f"if cortex_iron_drawn != {iron_ore_draw}:",
        "    raise RuntimeError('failed exact endogenous iron draw')",
        f"if cortex_coal_drawn != {coal_draw}:",
        "    raise RuntimeError('failed exact endogenous coal draw')",
        (
            "cortex_player_iron_after_draw=inspect_inventory()"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_player_coal_after_draw=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_iron_buffer_after_draw=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_buffer_after_draw=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        "cortex_iron_extractor=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_iron_extractor,",
        f"    quantity={iron_refuel},",
        ")",
        "cortex_coal_extractor=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_extractor,",
        f"    quantity={coal_refuel},",
        ")",
        (
            "cortex_player_coal_after_refuel=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        f"cortex_fast_reposition({_position(furnace)})",
        "cortex_iron_furnace=place_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    position={_position(furnace)},",
        ")",
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_iron_furnace,",
        f"    quantity={furnace_coal},",
        ")",
        (
            "cortex_player_coal_after_furnace_fuel=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_furnace,",
        f"    quantity={iron_ore_draw},",
        ")",
        (
            "cortex_furnace_iron_after_insert=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_furnace_plate_after_insert=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_player_iron_after_furnace_insert=inspect_inventory()"
            f"[{_prototype('iron-ore')}]"
        ),
        f"sleep({probe_seconds})",
        "cortex_iron_furnace_probe=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {_position(furnace)},",
        ")",
        "cortex_furnace_status_observed=str(cortex_iron_furnace_probe.status)",
        (
            "cortex_furnace_iron_after_probe=inspect_inventory("
            "cortex_iron_furnace_probe)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_furnace_plate_after_probe=inspect_inventory("
            "cortex_iron_furnace_probe)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_furnace_working_observed=("
            "'WORKING' in cortex_furnace_status_observed.upper() or "
            "cortex_furnace_plate_after_probe>0)"
        ),
        f"sleep({smelt_seconds-probe_seconds})",
        "cortex_iron_furnace_live=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {_position(furnace)},",
        ")",
        "cortex_iron_furnace_exists=cortex_iron_furnace_live is not None",
        (
            "cortex_furnace_iron_remaining=inspect_inventory(cortex_iron_furnace_live)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_iron_plate_count=inspect_inventory(cortex_iron_furnace_live)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_iron_ore_input_live=("
            f"cortex_furnace_iron_after_insert == {iron_ore_draw} and "
            "cortex_iron_plate_count>0)"
        ),
        (
            "cortex_furnace_operational=("
            "cortex_iron_furnace_exists and "
            "cortex_furnace_working_observed)"
        ),
        "cortex_iron_plate_output_positive=cortex_iron_plate_count>0",
        (
            "cortex_iron_survival_before=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_survival_before=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"sleep({survival_seconds})",
        (
            "cortex_iron_survival_after=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_survival_after=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_iron_survival_growth=max("
            "0,cortex_iron_survival_after-cortex_iron_survival_before)"
        ),
        (
            "cortex_coal_survival_growth=max("
            "0,cortex_coal_survival_after-cortex_coal_survival_before)"
        ),
        "cortex_iron_extractor_live=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {_position(iron_extractor)},",
        ")",
        "cortex_coal_extractor_live=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {_position(coal_extractor)},",
        ")",
        (
            "cortex_iron_extraction_survives=("
            "cortex_iron_extractor_live is not None and "
            "cortex_iron_survival_growth>0)"
        ),
        (
            "cortex_coal_self_sufficiency_survives=("
            "cortex_coal_extractor_live is not None and "
            "cortex_coal_survival_growth>0)"
        ),
        "print({",
        "    'iron_ore_input_live':cortex_iron_ore_input_live,",
        "    'furnace_operational':cortex_furnace_operational,",
        "    'iron_plate_output_positive':cortex_iron_plate_output_positive,",
        "    'iron_plate_count':cortex_iron_plate_count,",
        "    'iron_furnace_exists':cortex_iron_furnace_exists,",
        "    'furnace_iron_after_insert':cortex_furnace_iron_after_insert,",
        "    'furnace_plate_after_insert':cortex_furnace_plate_after_insert,",
        "    'furnace_iron_after_probe':cortex_furnace_iron_after_probe,",
        "    'furnace_plate_after_probe':cortex_furnace_plate_after_probe,",
        "    'player_iron_after_draw':cortex_player_iron_after_draw,",
        "    'player_coal_after_draw':cortex_player_coal_after_draw,",
        "    'player_coal_after_refuel':cortex_player_coal_after_refuel,",
        (
            "    'player_coal_after_furnace_fuel':"
            "cortex_player_coal_after_furnace_fuel,"
        ),
        (
            "    'player_iron_after_furnace_insert':"
            "cortex_player_iron_after_furnace_insert,"
        ),
        "    'iron_extraction_survives':cortex_iron_extraction_survives,",
        "    'coal_self_sufficiency_survives':cortex_coal_self_sufficiency_survives,",
        "    'iron_survival_growth':cortex_iron_survival_growth,",
        "    'coal_survival_growth':cortex_coal_survival_growth,",
        "})",
    ]
