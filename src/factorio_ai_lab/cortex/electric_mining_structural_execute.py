"""Compiler for the F5-C electric-mining transactional Option."""

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


def compile_electric_mining(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("electric mining requires frozen positions")
    required=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","boiler","steam_engine","power_pole",
        "copper_extractor","copper_buffer","copper_furnace",
        "lab","assembler","electric_pole","electric_drill","electric_buffer",
    )
    parsed: dict[str,str]={}
    for name in required:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"electric mining requires position {name}")
        parsed[name]=_position(raw)

    tree=params.get("bootstrap_tree")
    if not isinstance(tree,Mapping):
        raise TypeError("electric mining requires frozen bootstrap tree")
    tree_name=tree.get("name")
    tree_direction=tree.get("direction",0)
    if (
        not isinstance(tree_name,str)
        or not tree_name.startswith("tree-")
        or not isinstance(tree_direction,int)
        or isinstance(tree_direction,bool)
    ):
        raise TypeError("electric mining bootstrap tree is invalid")
    tree_position=_position(tree)

    science=_positive_int(params,"research_science_packs")
    science_batch=_positive_int(params,"science_batch")
    iron_target=_positive_int(params,"iron_plate_target")
    copper_target=_positive_int(params,"copper_plate_target")
    min_coal_stock=_positive_int(params,"min_coal_stock")
    coal_stock_target=_positive_int(params,"coal_stock_target")
    coal_amplification_cycles=_positive_int(params,"coal_amplification_cycles")
    coal_cycle_seconds=_positive_int(params,"coal_cycle_seconds")
    extraction_coal_draw=_positive_int(params,"extraction_coal_draw")
    iron_miner_refuel=_positive_int(params,"iron_miner_refuel")
    copper_miner_refuel=_positive_int(params,"copper_miner_refuel")
    process_coal_draw=_positive_int(params,"process_coal_draw")
    iron_furnace_refuel=_positive_int(params,"iron_furnace_refuel")
    copper_furnace_refuel=_positive_int(params,"copper_furnace_refuel")
    boiler_refuel=_positive_int(params,"boiler_refuel")
    ore_recovery=_positive_int(params,"ore_recovery_window_seconds")
    smelt_window=_positive_int(params,"smelt_window_seconds")
    research_window=_positive_int(params,"research_window_seconds")
    electric_validation=_positive_int(params,"electric_validation_window_seconds")
    manufacturing_window=_positive_int(params,"manufacturing_window_seconds")
    survival_recovery=_positive_int(params,"survival_recovery_window_seconds")
    survival_window=_positive_int(params,"survival_window_seconds")

    if science<25 or science_batch<science+1:
        raise ValueError("electric mining requires 25 science plus one reserve")
    if iron_target<82:
        raise ValueError("electric mining iron budget must cover causal DAG")
    if copper_target<36:
        raise ValueError("electric mining copper budget must cover causal DAG")
    if process_coal_draw < (
        iron_furnace_refuel+copper_furnace_refuel+boiler_refuel
    ):
        raise ValueError("electric mining process coal budget is inconsistent")

    research_round=max(25,research_window//6)
    manufacturing_round=max(3,manufacturing_window//2)
    iron_batch1=(iron_target+1)//2
    iron_batch2=iron_target-iron_batch1
    smelt_batch1=max(1,(smelt_window*iron_batch1)//iron_target)
    smelt_batch2=smelt_window-smelt_batch1
    if iron_batch1>50 or iron_batch2>50:
        raise ValueError("electric mining iron furnace batches exceed one stack")
    if copper_target>50:
        raise ValueError("electric mining copper furnace batch exceeds one stack")
    if iron_batch2<=0 or smelt_batch2<=0:
        raise ValueError("electric mining requires two bounded iron batches")

    code=f"""
cortex_iron_extractor=get_entity({_prototype('burner-mining-drill')},{parsed['iron_extractor']})
cortex_iron_buffer=get_entity({_prototype('wooden-chest')},{parsed['iron_buffer']})
cortex_iron_furnace=get_entity({_prototype('stone-furnace')},{parsed['iron_furnace']})
cortex_coal_extractor=get_entity({_prototype('burner-mining-drill')},{parsed['coal_extractor']})
cortex_coal_buffer=get_entity({_prototype('wooden-chest')},{parsed['coal_buffer']})
cortex_boiler=get_entity({_prototype('boiler')},{parsed['boiler']})
cortex_steam_engine=get_entity({_prototype('steam-engine')},{parsed['steam_engine']})
cortex_power_pole=get_entity({_prototype('small-electric-pole')},{parsed['power_pole']})
cortex_copper_extractor=get_entity({_prototype('burner-mining-drill')},{parsed['copper_extractor']})
cortex_copper_buffer=get_entity({_prototype('wooden-chest')},{parsed['copper_buffer']})
cortex_copper_furnace=get_entity({_prototype('stone-furnace')},{parsed['copper_furnace']})
cortex_lab=get_entity({_prototype('lab')},{parsed['lab']})
cortex_assembler=get_entity({_prototype('assembling-machine-1')},{parsed['assembler']})

cortex_lab_energy_before=float(cortex_lab.energy or 0)
cortex_assembler_energy_before=float(cortex_assembler.energy or 0)
cortex_power_pole_electrical_id=getattr(cortex_power_pole,'electrical_id',None)
cortex_lab_electrical_id_before=getattr(cortex_lab,'electrical_id',None)
cortex_assembler_electrical_id_before=getattr(cortex_assembler,'electrical_id',None)
if (
    cortex_power_pole_electrical_id is None
    or cortex_lab_electrical_id_before is None
    or cortex_assembler_electrical_id_before is None
    or cortex_lab_electrical_id_before!=cortex_power_pole_electrical_id
    or cortex_assembler_electrical_id_before!=cortex_power_pole_electrical_id
):
    raise RuntimeError(
        'promoted powered manufacturing is not on promoted electrical network'
    )

cortex_initial_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_initial_coal_available < {min_coal_stock}:
    raise RuntimeError('endogenous coal below electric-mining preflight stock')
cortex_coal_amplification_rounds=0
cortex_coal_amplification_growth=0
for cortex_coal_round in range({coal_amplification_cycles}):
    cortex_coal_stock=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
    if cortex_coal_stock >= {coal_stock_target}:
        break
    if cortex_coal_stock < 1:
        raise RuntimeError('coal amplification lost its endogenous seed')
    extract_item({_prototype('coal')},cortex_coal_buffer,quantity=1)
    cortex_coal_extractor=insert_item(
        {_prototype('coal')},cortex_coal_extractor,quantity=1
    )
    cortex_cycle_before=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
    sleep({coal_cycle_seconds})
    cortex_cycle_after=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
    cortex_cycle_growth=max(0,cortex_cycle_after-cortex_cycle_before)
    if cortex_cycle_growth<=0:
        raise RuntimeError('promoted coal cycle produced no endogenous growth')
    cortex_coal_amplification_growth+=cortex_cycle_growth
    cortex_coal_amplification_rounds+=1
cortex_coal_stock_after_amplification=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_coal_stock_after_amplification < {coal_stock_target}:
    raise RuntimeError('coal cycle did not reach electric-mining stock target')

cortex_extraction_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_extraction_coal_available < {extraction_coal_draw}:
    raise RuntimeError('coal stock below electric-mining extraction fuel budget')
extract_item(
    {_prototype('coal')},cortex_coal_buffer,quantity={extraction_coal_draw}
)
cortex_iron_extractor=insert_item(
    {_prototype('coal')},cortex_iron_extractor,quantity={iron_miner_refuel}
)
cortex_copper_extractor=insert_item(
    {_prototype('coal')},cortex_copper_extractor,quantity={copper_miner_refuel}
)

sleep({ore_recovery})
cortex_iron_ore_available=inspect_inventory(cortex_iron_buffer)[{_prototype('iron-ore')}]
cortex_copper_ore_available=inspect_inventory(cortex_copper_buffer)[{_prototype('copper-ore')}]
if cortex_iron_ore_available < {iron_target}:
    raise RuntimeError('endogenous iron ore below electric-mining budget')
if cortex_copper_ore_available < {copper_target}:
    raise RuntimeError('endogenous copper ore below electric-mining budget')

cortex_iron_ore_draw_batch1=extract_item(
    {_prototype('iron-ore')},cortex_iron_buffer,quantity={iron_batch1}
)
cortex_copper_ore_draw=extract_item(
    {_prototype('copper-ore')},cortex_copper_buffer,quantity={copper_target}
)
if cortex_iron_ore_draw_batch1 < {iron_batch1}:
    raise RuntimeError('first electric-mining iron transfer incomplete')
if cortex_copper_ore_draw < {copper_target}:
    raise RuntimeError('electric-mining copper transfer incomplete')

cortex_process_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_process_coal_available < {process_coal_draw}:
    raise RuntimeError('endogenous coal did not recover for electric mining')
extract_item(
    {_prototype('coal')},cortex_coal_buffer,quantity={process_coal_draw}
)
cortex_iron_furnace=insert_item(
    {_prototype('coal')},cortex_iron_furnace,quantity={iron_furnace_refuel}
)
cortex_copper_furnace=insert_item(
    {_prototype('coal')},cortex_copper_furnace,quantity={copper_furnace_refuel}
)
cortex_boiler=insert_item(
    {_prototype('coal')},cortex_boiler,quantity={boiler_refuel}
)
cortex_iron_furnace=insert_item(
    {_prototype('iron-ore')},cortex_iron_furnace,quantity={iron_batch1}
)
cortex_copper_furnace=insert_item(
    {_prototype('copper-ore')},cortex_copper_furnace,quantity={copper_target}
)
sleep({smelt_batch1})

cortex_iron_batch1_available=inspect_inventory(cortex_iron_furnace)[{_prototype('iron-plate')}]
cortex_copper_plate_available=inspect_inventory(cortex_copper_furnace)[{_prototype('copper-plate')}]
if cortex_iron_batch1_available < {iron_batch1}:
    raise RuntimeError('electric-mining first iron smelting batch incomplete')
if cortex_copper_plate_available < {copper_target}:
    raise RuntimeError('electric-mining copper smelting incomplete')
cortex_iron_batch1_ready=extract_item(
    {_prototype('iron-plate')},cortex_iron_furnace,quantity={iron_batch1}
)
cortex_copper_plate_ready=extract_item(
    {_prototype('copper-plate')},cortex_copper_furnace,quantity={copper_target}
)

cortex_iron_ore_draw_batch2=extract_item(
    {_prototype('iron-ore')},cortex_iron_buffer,quantity={iron_batch2}
)
if cortex_iron_ore_draw_batch2 < {iron_batch2}:
    raise RuntimeError('second electric-mining iron transfer incomplete')
cortex_iron_furnace=insert_item(
    {_prototype('iron-ore')},cortex_iron_furnace,quantity={iron_batch2}
)
sleep({smelt_batch2})
cortex_iron_batch2_available=inspect_inventory(cortex_iron_furnace)[{_prototype('iron-plate')}]
if cortex_iron_batch2_available < {iron_batch2}:
    raise RuntimeError('electric-mining second iron smelting batch incomplete')
cortex_iron_batch2_ready=extract_item(
    {_prototype('iron-plate')},cortex_iron_furnace,quantity={iron_batch2}
)
cortex_iron_plate_ready=cortex_iron_batch1_ready+cortex_iron_batch2_ready
if cortex_iron_plate_ready < {iron_target}:
    raise RuntimeError('electric-mining iron plate budget incomplete')
if cortex_copper_plate_ready < {copper_target}:
    raise RuntimeError('electric-mining copper plate budget incomplete')

cortex_wood_harvested=harvest_resource(
    {tree_position},quantity=4,radius=0.25
)
if cortex_wood_harvested < 4:
    raise RuntimeError('endogenous tree did not yield electric-mining wood budget')

craft_item({_prototype('iron-gear-wheel')},quantity={science_batch})
craft_item({_prototype('automation-science-pack')},quantity={science_batch})
cortex_science_batch_ready=inspect_inventory()[{_prototype('automation-science-pack')}]
if cortex_science_batch_ready < {science_batch}:
    raise RuntimeError('endogenous electric-mining science batch incomplete')
cortex_science_fed=insert_item(
    {_prototype('automation-science-pack')},
    cortex_lab,
    quantity={science},
)
cortex_research_requirements=set_research('electric-mining-drill')
cortex_research_remaining_count=0
for cortex_requirement in cortex_research_requirements:
    cortex_research_remaining_count+=cortex_requirement.count
for cortex_research_round_index in range(6):
    if cortex_research_remaining_count<=0:
        break
    sleep({research_round})
    cortex_research_remaining=get_research_progress('electric-mining-drill')
    cortex_research_remaining_count=0
    for cortex_requirement in cortex_research_remaining:
        cortex_research_remaining_count+=cortex_requirement.count
if cortex_research_remaining_count>0:
    raise RuntimeError('Electric Mining Drill research did not complete in bounded window')
cortex_research_completed=True

craft_item({_prototype('copper-cable')},quantity=12)
craft_item({_prototype('electronic-circuit')},quantity=3)
craft_item({_prototype('iron-gear-wheel')},quantity=5)
craft_item({_prototype('small-electric-pole')},quantity=1)
craft_item({_prototype('wooden-chest')},quantity=1)
craft_item({_prototype('electric-mining-drill')},quantity=1)

cortex_place_exact_entity(
    {parsed['electric_pole']},
    'small-electric-pole',
    direction='north',
)
cortex_place_exact_entity(
    {parsed['electric_drill']},
    'electric-mining-drill',
    direction='north',
)
cortex_place_exact_entity(
    {parsed['electric_buffer']},
    'wooden-chest',
    direction='north',
)
sleep(2)

cortex_electric_pole=get_entity(
    {_prototype('small-electric-pole')},{parsed['electric_pole']}
)
cortex_electric_drill=get_entity(
    {_prototype('electric-mining-drill')},{parsed['electric_drill']}
)
cortex_electric_buffer=get_entity(
    {_prototype('wooden-chest')},{parsed['electric_buffer']}
)
cortex_electric_drop_position=getattr(cortex_electric_drill,'drop_position',None)
if cortex_electric_drop_position is None:
    raise RuntimeError('electric mining drill exposes no output position')
if (
    abs(float(cortex_electric_drop_position.x)-float(cortex_electric_buffer.position.x))>0.51
    or abs(float(cortex_electric_drop_position.y)-float(cortex_electric_buffer.position.y))>0.51
):
    raise RuntimeError('electric mining output chest is not aligned to drill output')
cortex_electric_drill_energy=float(cortex_electric_drill.energy or 0)
cortex_electric_drill_electrical_id=getattr(
    cortex_electric_drill,'electrical_id',None
)
cortex_electric_pole_electrical_id=getattr(
    cortex_electric_pole,'electrical_id',None
)
if cortex_electric_drill_energy<=0 or cortex_electric_drill_electrical_id is None:
    raise RuntimeError('electric mining drill is not electrically supplied')
if (
    cortex_electric_pole_electrical_id is None
    or cortex_electric_drill_electrical_id!=cortex_electric_pole_electrical_id
):
    raise RuntimeError('electric drill is not on the promoted electrical network')

cortex_electric_buffer_before=inspect_inventory(cortex_electric_buffer)[{_prototype('coal')}]
cortex_steam_load_before=float(cortex_steam_engine.energy or 0)
sleep({electric_validation})
cortex_electric_drill=get_entity(
    {_prototype('electric-mining-drill')},{parsed['electric_drill']}
)
cortex_electric_buffer=get_entity(
    {_prototype('wooden-chest')},{parsed['electric_buffer']}
)
cortex_electric_buffer_after=inspect_inventory(cortex_electric_buffer)[{_prototype('coal')}]
cortex_electric_output_growth=max(
    0,cortex_electric_buffer_after-cortex_electric_buffer_before
)
cortex_electric_drill_energy_after=float(cortex_electric_drill.energy or 0)
cortex_electric_drill_electrical_id=getattr(
    cortex_electric_drill,'electrical_id',None
)
cortex_steam_engine_live=get_entity(
    {_prototype('steam-engine')},{parsed['steam_engine']}
)
cortex_steam_load_after=float(cortex_steam_engine_live.energy or 0)
cortex_steam_load_amount=0.0
for cortex_fluid in (cortex_steam_engine_live.fluid_box or []):
    if 'steam' in str(cortex_fluid.get('name','')).lower():
        cortex_steam_load_amount+=float(cortex_fluid.get('amount',0) or 0)

cortex_electric_drill_powered=(
    cortex_electric_drill_energy_after>0
    and cortex_electric_drill_electrical_id is not None
)
cortex_ore_output_positive=cortex_electric_output_growth>0
cortex_power_survives_load=(
    cortex_electric_drill_powered
    and cortex_steam_load_after>0
    and cortex_steam_load_amount>0
)
if not cortex_ore_output_positive:
    raise RuntimeError('electric mining drill produced no buffered coal output')
if not cortex_power_survives_load:
    raise RuntimeError('promoted power network did not survive electric-drill load')

cortex_science_replenish_before=inspect_inventory()[{_prototype('automation-science-pack')}]
craft_item({_prototype('iron-gear-wheel')},quantity=1)
craft_item({_prototype('automation-science-pack')},quantity=1)
cortex_science_replenish_after=inspect_inventory()[{_prototype('automation-science-pack')}]
cortex_science_replenished=max(
    0,cortex_science_replenish_after-cortex_science_replenish_before
)
if cortex_science_replenished<1:
    raise RuntimeError('automation-science capability did not survive electric research')
cortex_copper_buffer=insert_item(
    {_prototype('automation-science-pack')},
    cortex_copper_buffer,
    quantity=cortex_science_replenished,
)
cortex_science_buffer_after=inspect_inventory(cortex_copper_buffer)[{_prototype('automation-science-pack')}]
cortex_automation_science_survives=(
    cortex_science_replenished>0 and cortex_science_buffer_after>0
)

cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_assembler=set_entity_recipe(
    cortex_assembler,{_prototype('iron-gear-wheel')}
)
cortex_assembler=insert_item(
    {_prototype('iron-plate')},cortex_assembler,quantity=2
)
sleep({manufacturing_round})
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_gear_output=inspect_inventory(cortex_assembler)[{_prototype('iron-gear-wheel')}]
if cortex_gear_output<=0:
    raise RuntimeError('powered manufacturing gear output regressed')
extract_item(
    {_prototype('iron-gear-wheel')},cortex_assembler,quantity=cortex_gear_output
)
cortex_assembler=set_entity_recipe(
    cortex_assembler,{_prototype('electronic-circuit')}
)
craft_item({_prototype('copper-cable')},quantity=4)
cortex_assembler=insert_item(
    {_prototype('iron-plate')},cortex_assembler,quantity=1
)
cortex_assembler=insert_item(
    {_prototype('copper-cable')},cortex_assembler,quantity=3
)
sleep({manufacturing_round})
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_circuit_output=inspect_inventory(cortex_assembler)[{_prototype('electronic-circuit')}]
cortex_assembler_energy_after=float(cortex_assembler.energy or 0)
cortex_assembler_electrical_id=getattr(cortex_assembler,'electrical_id',None)
cortex_powered_manufacturing_survives=(
    cortex_gear_output>0
    and cortex_circuit_output>0
    and cortex_assembler_energy_after>0
    and cortex_assembler_electrical_id is not None
)
if not cortex_powered_manufacturing_survives:
    raise RuntimeError('powered manufacturing capability regressed')

cortex_survival_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_survival_coal_available<6:
    sleep({survival_recovery})
    cortex_survival_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_survival_coal_available<6:
    raise RuntimeError('endogenous coal below electric-mining survival reserve')
extract_item({_prototype('coal')},cortex_coal_buffer,quantity=6)
cortex_iron_extractor=insert_item({_prototype('coal')},cortex_iron_extractor,quantity=1)
cortex_coal_extractor=insert_item({_prototype('coal')},cortex_coal_extractor,quantity=1)
cortex_iron_furnace=insert_item({_prototype('coal')},cortex_iron_furnace,quantity=1)
cortex_boiler=insert_item({_prototype('coal')},cortex_boiler,quantity=1)
cortex_copper_extractor=insert_item({_prototype('coal')},cortex_copper_extractor,quantity=1)
cortex_copper_furnace=insert_item({_prototype('coal')},cortex_copper_furnace,quantity=1)

cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)[{_prototype('iron-ore')}]
cortex_survival_copper_available=inspect_inventory(cortex_copper_buffer)[{_prototype('copper-ore')}]
if cortex_survival_iron_available<2 or cortex_survival_copper_available<2:
    sleep({survival_recovery})
    cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)[{_prototype('iron-ore')}]
    cortex_survival_copper_available=inspect_inventory(cortex_copper_buffer)[{_prototype('copper-ore')}]
if cortex_survival_iron_available<2:
    raise RuntimeError('iron reserve unavailable for electric-mining survival')
if cortex_survival_copper_available<2:
    raise RuntimeError('copper reserve unavailable for electric-mining survival')
extract_item({_prototype('iron-ore')},cortex_iron_buffer,quantity=2)
cortex_iron_furnace=insert_item({_prototype('iron-ore')},cortex_iron_furnace,quantity=2)
extract_item({_prototype('copper-ore')},cortex_copper_buffer,quantity=2)
cortex_copper_furnace=insert_item({_prototype('copper-ore')},cortex_copper_furnace,quantity=2)

cortex_iron_survival_before=inspect_inventory(cortex_iron_buffer)[{_prototype('iron-ore')}]
cortex_coal_survival_before=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
cortex_iron_smelting_before=inspect_inventory(cortex_iron_furnace)[{_prototype('iron-plate')}]
cortex_copper_survival_before=inspect_inventory(cortex_copper_buffer)[{_prototype('copper-ore')}]
cortex_copper_smelting_before=inspect_inventory(cortex_copper_furnace)[{_prototype('copper-plate')}]
sleep({survival_window})
cortex_iron_survival_after=inspect_inventory(cortex_iron_buffer)[{_prototype('iron-ore')}]
cortex_coal_survival_after=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
cortex_iron_smelting_after=inspect_inventory(cortex_iron_furnace)[{_prototype('iron-plate')}]
cortex_copper_survival_after=inspect_inventory(cortex_copper_buffer)[{_prototype('copper-ore')}]
cortex_copper_smelting_after=inspect_inventory(cortex_copper_furnace)[{_prototype('copper-plate')}]

cortex_iron_survival_growth=max(0,cortex_iron_survival_after-cortex_iron_survival_before)
cortex_coal_survival_growth=max(0,cortex_coal_survival_after-cortex_coal_survival_before)
cortex_smelting_survival_growth=max(0,cortex_iron_smelting_after-cortex_iron_smelting_before)
cortex_copper_survival_growth=max(0,cortex_copper_survival_after-cortex_copper_survival_before)
cortex_copper_smelting_growth=max(0,cortex_copper_smelting_after-cortex_copper_smelting_before)

cortex_steam_engine_live=get_entity({_prototype('steam-engine')},{parsed['steam_engine']})
cortex_steam_survival_amount=0.0
for cortex_fluid in (cortex_steam_engine_live.fluid_box or []):
    if 'steam' in str(cortex_fluid.get('name','')).lower():
        cortex_steam_survival_amount+=float(cortex_fluid.get('amount',0) or 0)
cortex_steam_survival_energy=float(cortex_steam_engine_live.energy or 0)

cortex_iron_extraction_survives=cortex_iron_survival_growth>0
cortex_coal_self_sufficiency_survives=cortex_coal_survival_growth>0
cortex_iron_smelting_survives=cortex_smelting_survival_growth>0
cortex_steam_power_survives=(
    cortex_steam_survival_amount>0 and cortex_steam_survival_energy>0
)
cortex_copper_chain_survives=(
    cortex_copper_survival_growth>0 and cortex_copper_smelting_growth>0
)

print({{
    'electric_drill_powered':cortex_electric_drill_powered,
    'ore_output_positive':cortex_ore_output_positive,
    'power_survives_load':cortex_power_survives_load,
    'electric_output_growth':cortex_electric_output_growth,
    'electric_buffer_coal_before':cortex_electric_buffer_before,
    'electric_buffer_coal_after':cortex_electric_buffer_after,
    'electric_drill_energy':cortex_electric_drill_energy_after,
    'electric_drill_electrical_id':cortex_electric_drill_electrical_id,
    'electric_pole_electrical_id':cortex_electric_pole_electrical_id,
    'research_completed':cortex_research_completed,
    'research_remaining_count':cortex_research_remaining_count,
    'science_batch_ready':cortex_science_batch_ready,
    'science_buffer_after':cortex_science_buffer_after,
    'science_replenished':cortex_science_replenished,
    'automation_science_survives':cortex_automation_science_survives,
    'powered_manufacturing_survives':cortex_powered_manufacturing_survives,
    'gear_output':cortex_gear_output,
    'circuit_output':cortex_circuit_output,
    'coal_amplification_rounds':cortex_coal_amplification_rounds,
    'coal_amplification_growth':cortex_coal_amplification_growth,
    'coal_stock_after_amplification':cortex_coal_stock_after_amplification,
    'wood_harvested':cortex_wood_harvested,
    'iron_extraction_survives':cortex_iron_extraction_survives,
    'coal_self_sufficiency_survives':cortex_coal_self_sufficiency_survives,
    'iron_smelting_survives':cortex_iron_smelting_survives,
    'steam_power_survives':cortex_steam_power_survives,
    'copper_chain_survives':cortex_copper_chain_survives,
    'iron_survival_growth':cortex_iron_survival_growth,
    'coal_survival_growth':cortex_coal_survival_growth,
    'smelting_survival_growth':cortex_smelting_survival_growth,
    'copper_survival_growth':cortex_copper_survival_growth,
    'copper_smelting_growth':cortex_copper_smelting_growth,
    'steam_survival_amount':cortex_steam_survival_amount,
    'steam_survival_energy':cortex_steam_survival_energy,
    'steam_load_before':cortex_steam_load_before,
    'steam_load_after':cortex_steam_load_after,
    'steam_load_amount':cortex_steam_load_amount,
}})
"""
    return code.strip().splitlines()
