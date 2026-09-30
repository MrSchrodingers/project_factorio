"""Compiler for the F5-C persistent copper-chain transactional Option."""

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
    x=raw.get("x"); y=raw.get("y")
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


def compile_copper_chain(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    routes=params.get("route_validations")
    if not isinstance(positions,Mapping):
        raise TypeError("copper chain requires frozen positions")
    if not isinstance(routes,Mapping):
        raise TypeError("copper chain requires observed route validations")

    required=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer",
        "boiler","steam_engine",
        "copper_extractor","copper_furnace",
        "stone","wood",
    )
    parsed: dict[str,str]={}
    for name in required:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"copper chain requires position {name}")
        parsed[name]=_position(raw)

    for name in ("stone","wood","copper","furnace"):
        route=routes.get(name)
        if not isinstance(route,Mapping):
            raise TypeError(f"copper chain missing route validation {name}")
        if route.get("validator")!="observed_weighted_astar_v1":
            raise ValueError(f"copper chain route {name} is not observed-A* validated")
        waypoints=route.get("path_waypoints")
        if not isinstance(waypoints,int) or isinstance(waypoints,bool) or waypoints<=0:
            raise ValueError(f"copper chain route {name} has no validated waypoints")

    stone=_positive_int(params,"stone_bootstrap")
    wood=_positive_int(params,"wood_bootstrap")
    iron_budget=_positive_int(params,"construction_iron_plates")
    initial_coal=_positive_int(params,"initial_coal_draw")
    iron_refuel=_positive_int(params,"iron_recovery_refuel")
    coal_refuel=_positive_int(params,"coal_recovery_refuel")
    iron_furnace_refuel=_positive_int(params,"iron_furnace_refuel")
    copper_drill_fuel=_positive_int(params,"copper_drill_fuel")
    copper_furnace_fuel=_positive_int(params,"copper_furnace_fuel")
    copper_ore_draw=_positive_int(params,"copper_ore_draw")
    survival_coal=_positive_int(params,"survival_coal_draw")
    survival_iron=_positive_int(params,"survival_iron_ore_draw")
    recovery_window=_positive_int(params,"iron_recovery_window_seconds")
    iron_smelt_window=_positive_int(params,"iron_smelt_window_seconds")
    copper_extract_window=_positive_int(params,"copper_extract_window_seconds")
    copper_smelt_window=_positive_int(params,"copper_smelt_window_seconds")
    survival_recovery=_positive_int(params,"survival_recovery_window_seconds")
    survival_window=_positive_int(params,"survival_window_seconds")

    return [
        "cortex_iron_extractor=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {parsed['iron_extractor']},",
        ")",
        "cortex_iron_buffer=get_entity(",
        f"    {_prototype('wooden-chest')},",
        f"    {parsed['iron_buffer']},",
        ")",
        "cortex_iron_furnace=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {parsed['iron_furnace']},",
        ")",
        "cortex_coal_extractor=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {parsed['coal_extractor']},",
        ")",
        "cortex_coal_buffer=get_entity(",
        f"    {_prototype('wooden-chest')},",
        f"    {parsed['coal_buffer']},",
        ")",
        "cortex_boiler=get_entity(",
        f"    {_prototype('boiler')},",
        f"    {parsed['boiler']},",
        ")",
        "cortex_steam_engine=get_entity(",
        f"    {_prototype('steam-engine')},",
        f"    {parsed['steam_engine']},",
        ")",
        (
            "cortex_initial_coal_available=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_initial_coal_available < {initial_coal}:",
        "    raise RuntimeError('endogenous coal stock below copper-chain draw')",
        "cortex_initial_coal_draw=extract_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_buffer,",
        f"    quantity={initial_coal},",
        ")",
        f"if cortex_initial_coal_draw != {initial_coal}:",
        "    raise RuntimeError('failed exact endogenous coal draw for copper chain')",
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
            "cortex_construction_iron=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "if cortex_construction_iron>0:",
        "    extract_item(",
        f"        {_prototype('iron-plate')},",
        "        cortex_iron_furnace,",
        f"        quantity=min({iron_budget},cortex_construction_iron),",
        "    )",
        (
            "cortex_construction_iron_ready=inspect_inventory()"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            f"cortex_construction_iron_shortfall=max(0,{iron_budget}-"
            "cortex_construction_iron_ready)"
        ),
        "cortex_construction_topup_ore=0",
        "cortex_construction_topup_plates=0",
        "if cortex_construction_iron_shortfall>0:",
        (
            "    cortex_iron_buffer_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        "    if cortex_iron_buffer_available < cortex_construction_iron_shortfall:",
        f"        sleep({recovery_window})",
        (
            "        cortex_iron_buffer_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        "    if cortex_iron_buffer_available < cortex_construction_iron_shortfall:",
        "        raise RuntimeError('endogenous iron recovery below copper-chain construction budget')",
        "    cortex_construction_topup_ore=extract_item(",
        f"        {_prototype('iron-ore')},",
        "        cortex_iron_buffer,",
        "        quantity=cortex_construction_iron_shortfall,",
        "    )",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_iron_furnace,",
        f"        quantity={iron_furnace_refuel},",
        "    )",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('iron-ore')},",
        "        cortex_iron_furnace,",
        "        quantity=cortex_construction_iron_shortfall,",
        "    )",
        f"    sleep({iron_smelt_window})",
        (
            "    cortex_construction_topup_available="
            "inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "    if cortex_construction_topup_available < cortex_construction_iron_shortfall:",
        "        raise RuntimeError('copper-chain construction iron did not smelt in time')",
        "    cortex_construction_topup_plates=extract_item(",
        f"        {_prototype('iron-plate')},",
        "        cortex_iron_furnace,",
        "        quantity=cortex_construction_iron_shortfall,",
        "    )",
        (
            "cortex_construction_iron_ready=inspect_inventory()"
            f"[{_prototype('iron-plate')}]"
        ),
        f"if cortex_construction_iron_ready < {iron_budget}:",
        "    raise RuntimeError('copper-chain construction iron budget not met')",
        f"cortex_fast_reposition({parsed['stone']})",
        "harvest_resource(",
        f"    {parsed['stone']},",
        f"    quantity={stone},",
        ")",
        f"cortex_fast_reposition({parsed['wood']})",
        "harvest_resource(",
        f"    {parsed['wood']},",
        f"    quantity={wood},",
        ")",
        f"craft_item({_prototype('burner-mining-drill')},quantity=1)",
        f"craft_item({_prototype('wooden-chest')},quantity=1)",
        f"craft_item({_prototype('stone-furnace')},quantity=1)",
        f"cortex_fast_reposition({parsed['copper_extractor']})",
        "cortex_copper_extractor=place_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    position={parsed['copper_extractor']},",
        "    direction=Direction.DOWN,",
        ")",
        "cortex_copper_extractor=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_copper_extractor,",
        f"    quantity={copper_drill_fuel},",
        ")",
        "cortex_copper_buffer=place_entity_next_to(",
        f"    {_prototype('wooden-chest')},",
        "    cortex_copper_extractor.position,",
        "    direction=Direction.DOWN,",
        ")",
        f"sleep({copper_extract_window})",
        (
            "cortex_copper_ore_count=inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('copper-ore')}]"
        ),
        "cortex_copper_extractor_exists=cortex_copper_extractor is not None",
        "cortex_copper_extraction_live=(",
        "    cortex_copper_extractor_exists and cortex_copper_ore_count>0",
        ")",
        f"if cortex_copper_ore_count < {copper_ore_draw}:",
        "    raise RuntimeError('copper extractor produced insufficient ore')",
        "cortex_copper_ore_drawn=extract_item(",
        f"    {_prototype('copper-ore')},",
        "    cortex_copper_buffer,",
        f"    quantity={copper_ore_draw},",
        ")",
        f"cortex_fast_reposition({parsed['copper_furnace']})",
        "cortex_copper_furnace=place_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    position={parsed['copper_furnace']},",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_copper_furnace,",
        f"    quantity={copper_furnace_fuel},",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('copper-ore')},",
        "    cortex_copper_furnace,",
        f"    quantity={copper_ore_draw},",
        ")",
        f"sleep({copper_smelt_window})",
        "cortex_copper_furnace_live=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {parsed['copper_furnace']},",
        ")",
        "cortex_copper_furnace_exists=cortex_copper_furnace_live is not None",
        (
            "cortex_copper_plate_count=inspect_inventory(cortex_copper_furnace_live)"
            f"[{_prototype('copper-plate')}]"
        ),
        "cortex_copper_smelting_live=(",
        "    cortex_copper_furnace_exists and cortex_copper_plate_count>0",
        ")",
        "cortex_copper_plate_output_positive=cortex_copper_plate_count>0",
        (
            "cortex_survival_coal_available=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_survival_coal_available < {survival_coal}:",
        "    raise RuntimeError('endogenous coal stock below copper-chain survival draw')",
        "cortex_survival_coal_draw=extract_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_buffer,",
        f"    quantity={survival_coal},",
        ")",
        "cortex_iron_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_iron_extractor,quantity=1",
        ")",
        "cortex_coal_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_coal_extractor,quantity=1",
        ")",
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('coal')},cortex_iron_furnace,quantity=1",
        ")",
        "cortex_boiler=insert_item(",
        f"    {_prototype('coal')},cortex_boiler,quantity=1",
        ")",
        (
            "cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        f"if cortex_survival_iron_available < {survival_iron}:",
        f"    sleep({survival_recovery})",
        (
            "    cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        f"if cortex_survival_iron_available < {survival_iron}:",
        "    raise RuntimeError('endogenous iron stock below copper-chain survival draw')",
        "cortex_survival_iron_draw=extract_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_buffer,",
        f"    quantity={survival_iron},",
        ")",
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_furnace,",
        f"    quantity={survival_iron},",
        ")",
        (
            "cortex_iron_survival_before=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_survival_before=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_smelting_survival_before=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        f"sleep({survival_window})",
        (
            "cortex_iron_survival_after=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_survival_after=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_smelting_survival_after=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "cortex_iron_survival_growth=max(0,cortex_iron_survival_after-cortex_iron_survival_before)",
        "cortex_coal_survival_growth=max(0,cortex_coal_survival_after-cortex_coal_survival_before)",
        "cortex_smelting_survival_growth=max(0,cortex_smelting_survival_after-cortex_smelting_survival_before)",
        "cortex_steam_engine_live=get_entity(",
        f"    {_prototype('steam-engine')},",
        f"    {parsed['steam_engine']},",
        ")",
        "cortex_boiler_live=get_entity(",
        f"    {_prototype('boiler')},",
        f"    {parsed['boiler']},",
        ")",
        "cortex_steam_survival_amount=0.0",
        "for cortex_fluid in (cortex_steam_engine_live.fluid_box or []):",
        "    if 'steam' in str(cortex_fluid.get('name','')).lower():",
        "        cortex_steam_survival_amount+=float(cortex_fluid.get('amount',0) or 0)",
        "cortex_steam_survival_energy=float(cortex_steam_engine_live.energy or 0)",
        "cortex_iron_extraction_survives=cortex_iron_survival_growth>0",
        "cortex_coal_self_sufficiency_survives=cortex_coal_survival_growth>0",
        "cortex_iron_smelting_survives=cortex_smelting_survival_growth>0",
        "cortex_steam_power_survives=(",
        "    cortex_steam_engine_live is not None",
        "    and cortex_boiler_live is not None",
        "    and cortex_steam_survival_amount>0",
        "    and cortex_steam_survival_energy>0",
        ")",
        "print({",
        "    'copper_extraction_live':cortex_copper_extraction_live,",
        "    'copper_smelting_live':cortex_copper_smelting_live,",
        "    'copper_plate_output_positive':cortex_copper_plate_output_positive,",
        "    'copper_ore_count':cortex_copper_ore_count,",
        "    'copper_plate_count':cortex_copper_plate_count,",
        "    'copper_extractor_exists':cortex_copper_extractor_exists,",
        "    'copper_furnace_exists':cortex_copper_furnace_exists,",
        "    'construction_iron_ready':cortex_construction_iron_ready,",
        "    'construction_iron_shortfall':cortex_construction_iron_shortfall,",
        "    'construction_topup_ore':cortex_construction_topup_ore,",
        "    'construction_topup_plates':cortex_construction_topup_plates,",
        "    'iron_extraction_survives':cortex_iron_extraction_survives,",
        "    'coal_self_sufficiency_survives':cortex_coal_self_sufficiency_survives,",
        "    'iron_smelting_survives':cortex_iron_smelting_survives,",
        "    'steam_power_survives':cortex_steam_power_survives,",
        "    'iron_survival_growth':cortex_iron_survival_growth,",
        "    'coal_survival_growth':cortex_coal_survival_growth,",
        "    'smelting_survival_growth':cortex_smelting_survival_growth,",
        "    'steam_survival_amount':cortex_steam_survival_amount,",
        "    'steam_survival_energy':cortex_steam_survival_energy,",
        "})",
    ]
