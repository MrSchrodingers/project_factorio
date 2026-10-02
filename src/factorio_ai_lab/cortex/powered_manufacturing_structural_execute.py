"""Compiler for the F5-C powered-manufacturing transactional Option."""

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


def _offset_position(
    raw: Mapping[str,Any],
    *,
    dx: float,
    dy: float,
) -> str:
    x=raw.get("x"); y=raw.get("y")
    if (
        not isinstance(x,Real)
        or isinstance(x,bool)
        or not isinstance(y,Real)
        or isinstance(y,bool)
    ):
        raise TypeError(f"invalid position {dict(raw)!r}")
    return f"Position(x={float(x)+float(dx)!r},y={float(y)+float(dy)!r})"


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


def compile_powered_manufacturing(
    operation: StructuralOperation,
) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("powered manufacturing requires frozen positions")
    required=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","boiler","steam_engine","power_pole",
        "copper_extractor","copper_buffer","copper_furnace",
    )
    parsed: dict[str,str]={}
    for name in required:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"powered manufacturing requires position {name}")
        parsed[name]=_position(raw)

    power_pole_raw=positions.get("power_pole")
    if not isinstance(power_pole_raw,Mapping):
        raise TypeError("powered manufacturing requires position power_pole")
    lab_position=_offset_position(power_pole_raw,dx=3.0,dy=0.0)
    assembler_position=_offset_position(power_pole_raw,dx=0.0,dy=-3.0)

    science=_positive_int(params,"science_packs")
    iron_target=_positive_int(params,"iron_plate_target")
    copper_target=_positive_int(params,"copper_plate_target")
    min_coal_stock=_positive_int(params,"min_coal_stock")
    coal_stock_target=_positive_int(params,"coal_stock_target")
    coal_amplification_cycles=_positive_int(params,"coal_amplification_cycles")
    coal_cycle_seconds=_positive_int(params,"coal_cycle_seconds")
    extraction_coal_draw=_positive_int(params,"extraction_coal_draw")
    iron_miner_refuel=_positive_int(params,"iron_miner_refuel")
    copper_miner_refuel=_positive_int(params,"copper_miner_refuel")
    ore_recovery=_positive_int(params,"ore_recovery_window_seconds")
    smelt_window=_positive_int(params,"smelt_window_seconds")
    research_window=_positive_int(params,"research_window_seconds")
    manufacturing_window=_positive_int(params,"manufacturing_window_seconds")
    survival_recovery=_positive_int(params,"survival_recovery_window_seconds")
    survival_window=_positive_int(params,"survival_window_seconds")

    if science<10:
        raise ValueError("Automation research requires at least 10 science packs")
    if iron_target<66:
        raise ValueError("powered manufacturing iron budget must cover causal DAG")
    if copper_target<24:
        raise ValueError("powered manufacturing copper budget must cover causal DAG")

    research_round=max(10,research_window//5)
    manufacturing_round=max(3,manufacturing_window//2)
    iron_batch1=(iron_target+1)//2
    iron_batch2=iron_target-iron_batch1
    smelt_batch1=max(1,(smelt_window*iron_batch1)//iron_target)
    smelt_batch2=smelt_window-smelt_batch1
    if iron_batch2<=0 or smelt_batch2<=0:
        raise ValueError("powered manufacturing requires two bounded iron batches")

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

cortex_science_buffer_before=inspect_inventory(cortex_copper_buffer)[{_prototype('automation-science-pack')}]
if cortex_science_buffer_before < {science}:
    raise RuntimeError('promoted automation-science buffer is incomplete')

cortex_initial_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_initial_coal_available < {min_coal_stock}:
    raise RuntimeError('endogenous coal below powered-manufacturing preflight stock')
cortex_coal_amplification_rounds=0
cortex_coal_amplification_growth=0
for cortex_coal_round in range({coal_amplification_cycles}):
    cortex_coal_stock=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
    if cortex_coal_stock >= {coal_stock_target}:
        break
    if cortex_coal_stock < 1:
        raise RuntimeError('coal amplification lost its endogenous seed')
    cortex_cycle_seed=extract_item(
        {_prototype('coal')},cortex_coal_buffer,quantity=1
    )
    cortex_coal_extractor=insert_item(
        {_prototype('coal')},cortex_coal_extractor,quantity=1
    )
    cortex_cycle_before=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
    sleep({coal_cycle_seconds})
    cortex_cycle_after=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
    cortex_cycle_growth=max(0,cortex_cycle_after-cortex_cycle_before)
    if cortex_cycle_growth <= 0:
        raise RuntimeError('promoted coal cycle produced no endogenous growth')
    cortex_coal_amplification_growth+=cortex_cycle_growth
    cortex_coal_amplification_rounds+=1
cortex_coal_stock_after_amplification=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_coal_stock_after_amplification < {coal_stock_target}:
    raise RuntimeError('promoted coal cycle did not reach manufacturing stock target')

cortex_extraction_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_extraction_coal_available < {extraction_coal_draw}:
    raise RuntimeError('coal stock below extraction fuel budget')
cortex_extraction_coal_draw=extract_item(
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
    raise RuntimeError('endogenous iron ore below powered-manufacturing budget')
if cortex_copper_ore_available < {copper_target}:
    raise RuntimeError('endogenous copper ore below powered-manufacturing budget')

cortex_iron_ore_draw_batch1=extract_item(
    {_prototype('iron-ore')},cortex_iron_buffer,quantity={iron_batch1}
)
if cortex_iron_ore_draw_batch1 < {iron_batch1}:
    raise RuntimeError('first powered-manufacturing iron transfer incomplete')
cortex_copper_ore_draw=extract_item(
    {_prototype('copper-ore')},cortex_copper_buffer,quantity={copper_target}
)
if cortex_copper_ore_draw < {copper_target}:
    raise RuntimeError('powered-manufacturing copper transfer incomplete')

cortex_process_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_process_coal_available < 12:
    raise RuntimeError('endogenous coal did not recover for smelting/research')
cortex_process_coal=extract_item(
    {_prototype('coal')},cortex_coal_buffer,quantity=12
)
cortex_iron_furnace=insert_item(
    {_prototype('coal')},cortex_iron_furnace,quantity=6
)
cortex_copper_furnace=insert_item(
    {_prototype('coal')},cortex_copper_furnace,quantity=2
)
cortex_boiler=insert_item(
    {_prototype('coal')},cortex_boiler,quantity=4
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
    raise RuntimeError('powered-manufacturing first iron smelting batch incomplete')
if cortex_copper_plate_available < {copper_target}:
    raise RuntimeError('powered-manufacturing copper smelting incomplete')
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
    raise RuntimeError('second powered-manufacturing iron transfer incomplete')
cortex_iron_ore_draw=cortex_iron_ore_draw_batch1+cortex_iron_ore_draw_batch2
cortex_iron_furnace=insert_item(
    {_prototype('iron-ore')},cortex_iron_furnace,quantity={iron_batch2}
)
sleep({smelt_batch2})
cortex_iron_batch2_available=inspect_inventory(cortex_iron_furnace)[{_prototype('iron-plate')}]
if cortex_iron_batch2_available < {iron_batch2}:
    raise RuntimeError('powered-manufacturing second iron smelting batch incomplete')
cortex_iron_batch2_ready=extract_item(
    {_prototype('iron-plate')},cortex_iron_furnace,quantity={iron_batch2}
)
cortex_iron_plate_ready=cortex_iron_batch1_ready+cortex_iron_batch2_ready
if cortex_iron_plate_ready < {iron_target}:
    raise RuntimeError('powered-manufacturing iron plate budget incomplete')

craft_item({_prototype('copper-cable')},quantity=30)
craft_item({_prototype('electronic-circuit')},quantity=10)
craft_item({_prototype('iron-gear-wheel')},quantity=12)
craft_item({_prototype('transport-belt')},quantity=4)
craft_item({_prototype('lab')},quantity=1)

cortex_lab_position={lab_position}
cortex_place_exact_entity(
    cortex_lab_position,
    'lab',
    direction='north',
)
sleep(2)
cortex_lab=get_entity({_prototype('lab')},cortex_lab_position)
cortex_lab_energy_before=float(cortex_lab.energy or 0)
cortex_lab_status_before=str(cortex_lab.status)
if cortex_lab_energy_before <= 0:
    raise RuntimeError('Lab is not electrically supplied')

cortex_science_draw=extract_item(
    {_prototype('automation-science-pack')},
    cortex_copper_buffer,
    quantity={science},
)
if cortex_science_draw < {science}:
    raise RuntimeError('failed to recover promoted automation science')
cortex_lab=insert_item(
    {_prototype('automation-science-pack')},
    cortex_lab,
    quantity={science},
)
cortex_research_requirements=set_research(Technology.Automation)
cortex_research_remaining_count=0
for cortex_requirement in cortex_research_requirements:
    cortex_research_remaining_count+=cortex_requirement.count
for cortex_research_round in range(5):
    if cortex_research_remaining_count<=0:
        break
    sleep({research_round})
    cortex_research_remaining=get_research_progress(Technology.Automation)
    cortex_research_remaining_count=0
    for cortex_requirement in cortex_research_remaining:
        cortex_research_remaining_count+=cortex_requirement.count
if cortex_research_remaining_count>0:
    raise RuntimeError('Automation research did not complete in bounded window')
cortex_research_completed=True
cortex_lab=get_entity({_prototype('lab')},cortex_lab.position)
cortex_lab_energy_after=float(cortex_lab.energy or 0)
cortex_lab_status_after=str(cortex_lab.status)

craft_item({_prototype('copper-cable')},quantity=10)
craft_item({_prototype('electronic-circuit')},quantity=3)
craft_item({_prototype('iron-gear-wheel')},quantity=5)
craft_item({_prototype('assembling-machine-1')},quantity=1)

cortex_assembler_position={assembler_position}
cortex_place_exact_entity(
    cortex_assembler_position,
    'assembling-machine-1',
    direction='north',
)
sleep(2)
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},cortex_assembler_position
)
cortex_assembler_energy_before=float(cortex_assembler.energy or 0)
cortex_assembler_status_before=str(cortex_assembler.status)
cortex_assembler_electrical_id=getattr(cortex_assembler,'electrical_id',None)
if cortex_assembler_energy_before <= 0:
    raise RuntimeError('assembling-machine-1 is not electrically supplied')

cortex_assembler=set_entity_recipe(
    cortex_assembler,{_prototype('iron-gear-wheel')}
)
cortex_assembler=insert_item(
    {_prototype('iron-plate')},cortex_assembler,quantity=2
)
sleep({manufacturing_round})
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},cortex_assembler.position
)
cortex_gear_output=inspect_inventory(cortex_assembler)[{_prototype('iron-gear-wheel')}]
if cortex_gear_output <= 0:
    raise RuntimeError('powered assembler produced no iron gears')
extract_item(
    {_prototype('iron-gear-wheel')},
    cortex_assembler,
    quantity=cortex_gear_output,
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
    {_prototype('assembling-machine-1')},cortex_assembler.position
)
cortex_circuit_output=inspect_inventory(cortex_assembler)[{_prototype('electronic-circuit')}]
if cortex_circuit_output <= 0:
    raise RuntimeError('powered assembler produced no electronic circuits')
cortex_assembler_energy_after=float(cortex_assembler.energy or 0)
cortex_assembler_status_after=str(cortex_assembler.status)
cortex_assembler_electrical_id=getattr(cortex_assembler,'electrical_id',None)
cortex_assembler_powered=(
    cortex_assembler_energy_after>0
    and cortex_assembler_electrical_id is not None
)
cortex_iron_gear_output_positive=cortex_gear_output>0
cortex_electronic_circuit_output_positive=cortex_circuit_output>0

cortex_science_replenish_before=inspect_inventory()[{_prototype('automation-science-pack')}]
craft_item({_prototype('iron-gear-wheel')},quantity=1)
craft_item({_prototype('automation-science-pack')},quantity=1)
cortex_science_replenish_after=inspect_inventory()[{_prototype('automation-science-pack')}]
cortex_science_replenished=max(
    0,cortex_science_replenish_after-cortex_science_replenish_before
)
if cortex_science_replenished<1:
    raise RuntimeError('automation-science capability did not survive research')
cortex_copper_buffer=insert_item(
    {_prototype('automation-science-pack')},
    cortex_copper_buffer,
    quantity=cortex_science_replenished,
)
cortex_science_buffer_after=inspect_inventory(cortex_copper_buffer)[{_prototype('automation-science-pack')}]
cortex_automation_science_survives=(
    cortex_science_replenished>0 and cortex_science_buffer_after>0
)

cortex_survival_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_survival_coal_available<6:
    sleep({survival_recovery})
    cortex_survival_coal_available=inspect_inventory(cortex_coal_buffer)[{_prototype('coal')}]
if cortex_survival_coal_available<6:
    raise RuntimeError('endogenous coal below powered-manufacturing survival reserve')
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
    raise RuntimeError('iron reserve unavailable for powered-manufacturing survival')
if cortex_survival_copper_available<2:
    raise RuntimeError('copper reserve unavailable for powered-manufacturing survival')
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
cortex_lab_position_x=float(cortex_lab.position.x)
cortex_lab_position_y=float(cortex_lab.position.y)
cortex_assembler_position_x=float(cortex_assembler.position.x)
cortex_assembler_position_y=float(cortex_assembler.position.y)

print({{
    'assembler_powered':cortex_assembler_powered,
    'iron_gear_output_positive':cortex_iron_gear_output_positive,
    'electronic_circuit_output_positive':cortex_electronic_circuit_output_positive,
    'gear_output':cortex_gear_output,
    'circuit_output':cortex_circuit_output,
    'research_completed':cortex_research_completed,
    'research_remaining_count':cortex_research_remaining_count,
    'science_buffer_before':cortex_science_buffer_before,
    'science_buffer_after':cortex_science_buffer_after,
    'coal_amplification_rounds':cortex_coal_amplification_rounds,
    'coal_amplification_growth':cortex_coal_amplification_growth,
    'coal_stock_after_amplification':cortex_coal_stock_after_amplification,
    'science_replenished':cortex_science_replenished,
    'automation_science_survives':cortex_automation_science_survives,
    'lab_energy_before':cortex_lab_energy_before,
    'lab_energy_after':cortex_lab_energy_after,
    'assembler_energy_before':cortex_assembler_energy_before,
    'assembler_energy_after':cortex_assembler_energy_after,
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
}})
"""
    return code.strip().splitlines()
