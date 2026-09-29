"""Compiler for the F5-C steam-power transactional Option."""

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


def compile_steam_power(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    routes=params.get("route_validations")
    if not isinstance(positions,Mapping):
        raise TypeError("steam power requires frozen positions")
    if not isinstance(routes,Mapping):
        raise TypeError("steam power requires observed route validations")

    required_positions=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer",
        "stone","copper","wood","water",
    )
    parsed={}
    for name in required_positions:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"steam power requires position {name}")
        parsed[name]=_position(raw)

    for name in ("stone","copper","wood","water"):
        route=routes.get(name)
        if not isinstance(route,Mapping):
            raise TypeError(f"steam power missing route validation {name}")
        if route.get("validator")!="observed_weighted_astar_v1":
            raise ValueError(f"steam power route {name} is not observed-A* validated")
        waypoints=route.get("path_waypoints")
        if not isinstance(waypoints,int) or isinstance(waypoints,bool) or waypoints<=0:
            raise ValueError(f"steam power route {name} has no validated waypoints")

    iron_draw=_positive_int(params,"iron_trigger_ore_draw")
    copper_ore=_positive_int(params,"copper_trigger_ore")
    stone=_positive_int(params,"stone_bootstrap")
    wood=_positive_int(params,"wood_bootstrap")
    initial_coal=_positive_int(params,"initial_coal_draw")
    iron_refuel=_positive_int(params,"iron_initial_refuel")
    coal_refuel=_positive_int(params,"coal_initial_refuel")
    iron_furnace_coal=_positive_int(params,"iron_furnace_trigger_coal")
    copper_furnace_coal=_positive_int(params,"copper_furnace_coal")
    boiler_coal=_positive_int(params,"boiler_coal")
    pipe_topup_coal=_positive_int(params,"pipe_topup_coal")
    pipe_smelt_seconds=_positive_int(params,"pipe_smelt_seconds_per_plate")
    pipe_min_window=_positive_int(params,"pipe_min_topup_window_seconds")
    survival_coal=_positive_int(params,"survival_coal_draw")
    coal_reserve=_positive_int(params,"coal_operating_reserve")
    coal_reserve_window=_positive_int(params,"coal_reserve_recovery_window_seconds")
    survival_iron=_positive_int(params,"iron_survival_ore_draw")
    reserve_window=_positive_int(params,"iron_reserve_recovery_window_seconds")
    iron_window=_positive_int(params,"iron_trigger_window_seconds")
    copper_window=_positive_int(params,"copper_trigger_window_seconds")
    power_window=_positive_int(params,"power_window_seconds")
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
        (
            "cortex_iron_stock_initial=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_coal_stock_initial=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_iron_stock_initial < {iron_draw}:",
        "    raise RuntimeError('endogenous iron stock below steam-power trigger draw')",
        f"if cortex_coal_stock_initial < {initial_coal}:",
        "    raise RuntimeError('endogenous coal stock below steam-power commissioning draw')",
        "cortex_iron_trigger_draw=extract_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_buffer,",
        f"    quantity={iron_draw},",
        ")",
        "cortex_initial_coal_draw=extract_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_buffer,",
        f"    quantity={initial_coal},",
        ")",
        f"if cortex_iron_trigger_draw != {iron_draw}:",
        "    raise RuntimeError('failed exact endogenous iron trigger draw')",
        f"if cortex_initial_coal_draw != {initial_coal}:",
        "    raise RuntimeError('failed exact endogenous coal commissioning draw')",
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
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_iron_furnace,",
        f"    quantity={iron_furnace_coal},",
        ")",
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_furnace,",
        f"    quantity={iron_draw},",
        ")",
        f"sleep({iron_window})",
        "cortex_iron_furnace=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {parsed['iron_furnace']},",
        ")",
        (
            "cortex_trigger_iron_plates=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "if cortex_trigger_iron_plates < 50:",
        "    raise RuntimeError('steam-power native iron-plate trigger not reached')",
        "cortex_iron_plates_drawn=extract_item(",
        f"    {_prototype('iron-plate')},",
        "    cortex_iron_furnace,",
        "    quantity=cortex_trigger_iron_plates,",
        ")",
        (
            "cortex_infrastructure_iron_after_trigger=inspect_inventory()"
            f"[{_prototype('iron-plate')}]"
        ),
        f"cortex_fast_reposition({parsed['stone']})",
        "harvest_resource(",
        f"    {parsed['stone']},",
        f"    quantity={stone},",
        ")",
        f"cortex_fast_reposition({parsed['copper']})",
        "harvest_resource(",
        f"    {parsed['copper']},",
        f"    quantity={copper_ore},",
        ")",
        f"cortex_fast_reposition({parsed['wood']})",
        "harvest_resource(",
        f"    {parsed['wood']},",
        f"    quantity={wood},",
        ")",
        f"craft_item({_prototype('stone-furnace')},quantity=1)",
        f"cortex_fast_reposition({parsed['copper']})",
        "cortex_copper_furnace=place_entity_next_to(",
        f"    {_prototype('stone-furnace')},",
        f"    {parsed['copper']},",
        "    direction=Direction.RIGHT,",
        "    spacing=2,",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_copper_furnace,",
        f"    quantity={copper_furnace_coal},",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('copper-ore')},",
        "    cortex_copper_furnace,",
        f"    quantity={copper_ore},",
        ")",
        f"sleep({copper_window})",
        "cortex_copper_furnace=get_entity(",
        f"    {_prototype('stone-furnace')},",
        "    cortex_copper_furnace.position,",
        ")",
        (
            "cortex_trigger_copper_plates=inspect_inventory(cortex_copper_furnace)"
            f"[{_prototype('copper-plate')}]"
        ),
        f"if cortex_trigger_copper_plates < {copper_ore}:",
        "    raise RuntimeError('electronics native copper-plate trigger not reached')",
        "cortex_copper_plates_drawn=extract_item(",
        f"    {_prototype('copper-plate')},",
        "    cortex_copper_furnace,",
        f"    quantity={copper_ore},",
        ")",
        "pickup_entity(cortex_copper_furnace)",
        (
            "cortex_coal_reserve_before=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        f"cortex_coal_reserve_target={coal_reserve}",
        "cortex_coal_reserve_recovery_refuel=0",
        "cortex_coal_reserve_recovery_window=0",
        "cortex_coal_reserve_draw=0",
        (
            "cortex_coal_reserve_shortfall=max("
            "0,cortex_coal_reserve_target-cortex_coal_reserve_before)"
        ),
        "if cortex_coal_reserve_shortfall>0:",
        (
            "    cortex_coal_reserve_buffer_available=inspect_inventory("
            "cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "    if cortex_coal_reserve_buffer_available < "
            "cortex_coal_reserve_shortfall:"
        ),
        (
            "        cortex_coal_reserve_player_available=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "        if cortex_coal_reserve_player_available < 1:",
        "            raise RuntimeError('no endogenous coal available for reserve recovery')",
        "        cortex_coal_extractor=insert_item(",
        f"            {_prototype('coal')},",
        "            cortex_coal_extractor,",
        "            quantity=1,",
        "        )",
        "        cortex_coal_reserve_recovery_refuel=1",
        f"        cortex_coal_reserve_recovery_window={coal_reserve_window}",
        "        sleep(cortex_coal_reserve_recovery_window)",
        (
            "    cortex_coal_reserve_player_now=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        (
            "    cortex_coal_reserve_shortfall=max("
            "0,cortex_coal_reserve_target-cortex_coal_reserve_player_now)"
        ),
        (
            "    cortex_coal_reserve_buffer_available=inspect_inventory("
            "cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "    if cortex_coal_reserve_buffer_available < "
            "cortex_coal_reserve_shortfall:"
        ),
        "        raise RuntimeError('endogenous coal reserve recovery failed')",
        "    if cortex_coal_reserve_shortfall>0:",
        "        cortex_coal_reserve_draw=extract_item(",
        f"            {_prototype('coal')},",
        "            cortex_coal_buffer,",
        "            quantity=cortex_coal_reserve_shortfall,",
        "        )",
        (
            "cortex_coal_reserve_ready=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "if cortex_coal_reserve_ready < cortex_coal_reserve_target:",
        "    raise RuntimeError('endogenous coal operating reserve not met')",
        f"craft_item({_prototype('small-electric-pole')},quantity=2)",
        f"craft_item({_prototype('inserter')},quantity=1)",
        f"craft_item({_prototype('offshore-pump')},quantity=1)",
        f"craft_item({_prototype('boiler')},quantity=1)",
        f"craft_item({_prototype('steam-engine')},quantity=1)",
        f"cortex_fast_reposition({parsed['water']})",
        "cortex_offshore_pump=place_entity(",
        f"    {_prototype('offshore-pump')},",
        f"    position={parsed['water']},",
        "    exact=False,",
        ")",
        "cortex_boiler_area=nearest_buildable(",
        f"    {_prototype('boiler')},",
        "    BuildingBox(width=9,height=8),",
        "    cortex_offshore_pump.position,",
        ")",
        "cortex_fast_reposition(cortex_boiler_area.center)",
        "cortex_boiler=place_entity(",
        f"    {_prototype('boiler')},",
        "    position=cortex_boiler_area.center,",
        "    direction=Direction.LEFT,",
        ")",
        "cortex_boiler=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_boiler,",
        f"    quantity={boiler_coal},",
        ")",
        (
            "cortex_boiler_coal_after_insert="
            f"cortex_boiler.fuel[{_prototype('coal')}]"
        ),
        "cortex_engine_area=nearest_buildable(",
        f"    {_prototype('steam-engine')},",
        "    BuildingBox(width=11,height=11),",
        "    cortex_boiler.position,",
        ")",
        "cortex_fast_reposition(cortex_engine_area.center)",
        "cortex_steam_engine=place_entity(",
        f"    {_prototype('steam-engine')},",
        "    position=cortex_engine_area.center,",
        "    direction=Direction.LEFT,",
        ")",
        "cortex_water_pipe_plan=connect_entities(",
        "    cortex_offshore_pump,",
        "    cortex_boiler,",
        f"    {_prototype('pipe')},",
        "    dry_run=True,",
        ")",
        "cortex_steam_pipe_plan=connect_entities(",
        "    cortex_boiler,",
        "    cortex_steam_engine,",
        f"    {_prototype('pipe')},",
        "    dry_run=True,",
        ")",
        (
            "cortex_water_pipe_required=int("
            "cortex_water_pipe_plan['number_of_entities_required'])"
        ),
        (
            "cortex_steam_pipe_required=int("
            "cortex_steam_pipe_plan['number_of_entities_required'])"
        ),
        (
            "cortex_pipe_required_total="
            "cortex_water_pipe_required+cortex_steam_pipe_required"
        ),
        (
            "cortex_pipe_available_before=inspect_inventory()"
            f"[{_prototype('pipe')}]"
        ),
        (
            "cortex_pipe_to_craft=max("
            "0,cortex_pipe_required_total-cortex_pipe_available_before)"
        ),
        (
            "cortex_pipe_iron_before_topup=inspect_inventory()"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_pipe_iron_shortfall=max("
            "0,cortex_pipe_to_craft-cortex_pipe_iron_before_topup)"
        ),
        "cortex_pipe_topup_ore=0",
        "cortex_pipe_topup_coal=0",
        "cortex_pipe_topup_plates=0",
        "cortex_pipe_topup_window=0",
        "if cortex_pipe_iron_shortfall>0:",
        (
            "    cortex_pipe_topup_iron_available=inspect_inventory("
            "cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "    cortex_pipe_topup_coal_available=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "    if cortex_pipe_topup_iron_available < cortex_pipe_iron_shortfall:",
        "        raise RuntimeError('endogenous iron stock below dynamic pipe top-up')",
        f"    if cortex_pipe_topup_coal_available < {pipe_topup_coal}:",
        "        raise RuntimeError('endogenous coal reserve below dynamic pipe top-up')",
        "    cortex_pipe_topup_ore=extract_item(",
        f"        {_prototype('iron-ore')},",
        "        cortex_iron_buffer,",
        "        quantity=cortex_pipe_iron_shortfall,",
        "    )",
        f"    cortex_pipe_topup_coal={pipe_topup_coal}",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('coal')},",
        "        cortex_iron_furnace,",
        f"        quantity={pipe_topup_coal},",
        "    )",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('iron-ore')},",
        "        cortex_iron_furnace,",
        "        quantity=cortex_pipe_iron_shortfall,",
        "    )",
        (
            f"    cortex_pipe_topup_window=max({pipe_min_window},"
            f"cortex_pipe_iron_shortfall*{pipe_smelt_seconds})"
        ),
        "    sleep(cortex_pipe_topup_window)",
        "    cortex_iron_furnace=get_entity(",
        f"        {_prototype('stone-furnace')},",
        f"        {parsed['iron_furnace']},",
        "    )",
        (
            "    cortex_pipe_topup_plate_available=inspect_inventory("
            "cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "    if cortex_pipe_topup_plate_available < "
            "cortex_pipe_iron_shortfall:"
        ),
        "        raise RuntimeError('dynamic pipe top-up did not smelt in time')",
        "    cortex_pipe_topup_plates=extract_item(",
        f"        {_prototype('iron-plate')},",
        "        cortex_iron_furnace,",
        "        quantity=cortex_pipe_iron_shortfall,",
        "    )",
        (
            "cortex_pipe_iron_ready=inspect_inventory()"
            f"[{_prototype('iron-plate')}]"
        ),
        "if cortex_pipe_iron_ready < cortex_pipe_to_craft:",
        "    raise RuntimeError('iron budget below dynamic pipe requirement')",
        "if cortex_pipe_to_craft>0:",
        "    craft_item(",
        f"        {_prototype('pipe')},",
        "        quantity=cortex_pipe_to_craft,",
        "    )",
        (
            "cortex_pipe_inventory_ready=inspect_inventory()"
            f"[{_prototype('pipe')}]"
        ),
        "if cortex_pipe_inventory_ready < cortex_pipe_required_total:",
        "    raise RuntimeError('crafted pipe inventory below dry-run requirement')",
        "cortex_water_pipes=connect_entities(",
        "    cortex_offshore_pump,",
        "    cortex_boiler,",
        f"    {_prototype('pipe')},",
        ")",
        "cortex_steam_pipes=connect_entities(",
        "    cortex_boiler,",
        "    cortex_steam_engine,",
        f"    {_prototype('pipe')},",
        ")",
        "cortex_consumer=place_entity_next_to(",
        f"    {_prototype('inserter')},",
        "    cortex_steam_engine.position,",
        "    direction=Direction.RIGHT,",
        "    spacing=0,",
        ")",
        "cortex_power_tap_count=0",
        "cortex_consumer_pole=None",
        (
            "for cortex_power_side in "
            "(Direction.LEFT,Direction.UP,Direction.DOWN,Direction.RIGHT):"
        ),
        "    try:",
        "        cortex_consumer_pole=place_entity_next_to(",
        f"            {_prototype('small-electric-pole')},",
        "            cortex_consumer.position,",
        "            direction=cortex_power_side,",
        "            spacing=0,",
        "        )",
        "        cortex_power_tap_count+=1",
        "        break",
        "    except Exception:",
        "        pass",
        "if cortex_consumer_pole is None:",
        "    raise RuntimeError('failed to place consumer power tap')",
        "sleep(1)",
        "cortex_consumer=get_entity(",
        f"    {_prototype('inserter')},",
        "    cortex_consumer.position,",
        ")",
        "cortex_engine_pole=None",
        "if cortex_consumer.electrical_id is None:",
        (
            "    for cortex_engine_side in "
            "(Direction.UP,Direction.DOWN,Direction.LEFT,Direction.RIGHT):"
        ),
        "        try:",
        "            cortex_engine_pole=place_entity_next_to(",
        f"                {_prototype('small-electric-pole')},",
        "                cortex_steam_engine.position,",
        "                direction=cortex_engine_side,",
        "                spacing=0,",
        "            )",
        "            cortex_power_tap_count+=1",
        "            break",
        "        except Exception:",
        "            pass",
        "    if cortex_engine_pole is None:",
        "        raise RuntimeError('failed to place engine power tap')",
        f"sleep({power_window})",
        "cortex_offshore_pump=get_entity(",
        f"    {_prototype('offshore-pump')},",
        "    cortex_offshore_pump.position,",
        ")",
        "cortex_boiler=get_entity(",
        f"    {_prototype('boiler')},",
        "    cortex_boiler.position,",
        ")",
        "cortex_steam_engine=get_entity(",
        f"    {_prototype('steam-engine')},",
        "    cortex_steam_engine.position,",
        ")",
        "cortex_consumer=get_entity(",
        f"    {_prototype('inserter')},",
        "    cortex_consumer.position,",
        ")",
        "cortex_water_amount=0.0",
        "for cortex_fluid in (cortex_offshore_pump.fluid_box or []):",
        "    if 'water' in str(cortex_fluid.get('name','')).lower():",
        "        cortex_water_amount+=float(cortex_fluid.get('amount',0) or 0)",
        "cortex_steam_amount=0.0",
        "for cortex_fluid in (cortex_steam_engine.fluid_box or []):",
        "    if 'steam' in str(cortex_fluid.get('name','')).lower():",
        "        cortex_steam_amount+=float(cortex_fluid.get('amount',0) or 0)",
        "cortex_steam_engine_energy=float(cortex_steam_engine.energy or 0)",
        "cortex_consumer_energy=float(cortex_consumer.energy or 0)",
        "cortex_steam_engine_exists=cortex_steam_engine is not None",
        "cortex_water_source_valid=cortex_water_amount>0",
        (
            "cortex_endogenous_fuel_reachable=("
            f"cortex_initial_coal_draw=={initial_coal} and "
            "cortex_boiler_coal_after_insert>0)"
        ),
        "cortex_steam_generated=cortex_steam_amount>0",
        "cortex_electrical_production_positive=cortex_steam_engine_energy>0",
        (
            "cortex_electric_consumer_supplied=("
            "cortex_consumer_energy>0 and "
            "cortex_consumer.electrical_id is not None)"
        ),
        (
            "cortex_survival_coal_available=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_survival_coal_available < {survival_coal}:",
        "    raise RuntimeError('endogenous coal reserve below survival draw')",
        f"cortex_survival_coal_draw={survival_coal}",
        "cortex_iron_extractor=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_iron_extractor,",
        "    quantity=1,",
        ")",
        "cortex_coal_extractor=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_extractor,",
        "    quantity=1,",
        ")",
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('coal')},",
        "    cortex_iron_furnace,",
        "    quantity=1,",
        ")",
        (
            "cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        "cortex_iron_reserve_recovery_refuel=0",
        "cortex_iron_reserve_after_recovery=cortex_survival_iron_available",
        f"if cortex_survival_iron_available < {survival_iron}:",
        (
            "    # The survival draw already refueled the iron drill immediately "
            "before this branch. Recovery reuses that endogenous fuel instead "
            "of consuming a second coal unit."
        ),
        f"    sleep({reserve_window})",
        (
            "    cortex_iron_reserve_after_recovery=inspect_inventory("
            "cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        f"if cortex_iron_reserve_after_recovery < {survival_iron}:",
        "    raise RuntimeError('endogenous iron reserve recovery failed')",
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
            "cortex_coal_survival_before=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_iron_survival_before=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_smelting_survival_before=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        f"sleep({survival_window})",
        (
            "cortex_coal_survival_after=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_iron_survival_after=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_smelting_survival_after=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_coal_survival_growth=max("
            "0,cortex_coal_survival_after-cortex_coal_survival_before)"
        ),
        (
            "cortex_iron_survival_growth=max("
            "0,cortex_iron_survival_after-cortex_iron_survival_before)"
        ),
        (
            "cortex_smelting_survival_growth=max("
            "0,cortex_smelting_survival_after-cortex_smelting_survival_before)"
        ),
        "cortex_iron_extractor_live=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {parsed['iron_extractor']},",
        ")",
        "cortex_coal_extractor_live=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {parsed['coal_extractor']},",
        ")",
        "cortex_iron_furnace_live=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {parsed['iron_furnace']},",
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
        (
            "cortex_iron_smelting_survives=("
            "cortex_iron_furnace_live is not None and "
            "cortex_smelting_survival_growth>0)"
        ),
        "print({",
        "    'water_source_valid':cortex_water_source_valid,",
        "    'endogenous_fuel_reachable':cortex_endogenous_fuel_reachable,",
        "    'steam_generated':cortex_steam_generated,",
        "    'electrical_production_positive':cortex_electrical_production_positive,",
        "    'electric_consumer_supplied':cortex_electric_consumer_supplied,",
        "    'water_amount':cortex_water_amount,",
        "    'steam_amount':cortex_steam_amount,",
        "    'steam_engine_energy':cortex_steam_engine_energy,",
        "    'electric_consumer_energy':cortex_consumer_energy,",
        "    'power_tap_count':cortex_power_tap_count,",
        (
            "    'consumer_electrical_id':"
            "(cortex_consumer.electrical_id or -1),"
        ),
        "    'trigger_iron_plates':cortex_trigger_iron_plates,",
        "    'trigger_copper_plates':cortex_trigger_copper_plates,",
        (
            "    'infrastructure_iron_after_trigger':"
            "cortex_infrastructure_iron_after_trigger,"
        ),
        "    'water_pipe_required':cortex_water_pipe_required,",
        "    'steam_pipe_required':cortex_steam_pipe_required,",
        "    'pipe_required_total':cortex_pipe_required_total,",
        "    'pipe_available_before':cortex_pipe_available_before,",
        "    'pipe_to_craft':cortex_pipe_to_craft,",
        "    'pipe_iron_shortfall':cortex_pipe_iron_shortfall,",
        "    'pipe_topup_ore':cortex_pipe_topup_ore,",
        "    'pipe_topup_coal':cortex_pipe_topup_coal,",
        "    'pipe_topup_plates':cortex_pipe_topup_plates,",
        "    'pipe_topup_window':cortex_pipe_topup_window,",
        "    'pipe_inventory_ready':cortex_pipe_inventory_ready,",
        "    'coal_reserve_before':cortex_coal_reserve_before,",
        "    'coal_reserve_target':cortex_coal_reserve_target,",
        "    'coal_reserve_shortfall':cortex_coal_reserve_shortfall,",
        "    'coal_reserve_draw':cortex_coal_reserve_draw,",
        (
            "    'coal_reserve_recovery_refuel':"
            "cortex_coal_reserve_recovery_refuel,"
        ),
        (
            "    'coal_reserve_recovery_window':"
            "cortex_coal_reserve_recovery_window,"
        ),
        "    'coal_reserve_ready':cortex_coal_reserve_ready,",
        "    'iron_extraction_survives':cortex_iron_extraction_survives,",
        "    'coal_self_sufficiency_survives':cortex_coal_self_sufficiency_survives,",
        "    'iron_smelting_survives':cortex_iron_smelting_survives,",
        "    'iron_survival_growth':cortex_iron_survival_growth,",
        "    'coal_survival_growth':cortex_coal_survival_growth,",
        "    'smelting_survival_growth':cortex_smelting_survival_growth,",
        (
            "    'iron_reserve_recovery_refuel':"
            "cortex_iron_reserve_recovery_refuel,"
        ),
        (
            "    'iron_reserve_after_recovery':"
            "cortex_iron_reserve_after_recovery,"
        ),
        "})",
    ]
