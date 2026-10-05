"""Compiler for the F5-C logistic-science transactional Option."""

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


def _batches(total: int,count: int) -> list[int]:
    base=total//count
    extra=total%count
    values=[base+(1 if index<extra else 0) for index in range(count)]
    if any(value<=0 or value>50 for value in values):
        raise ValueError("smelting batch must fit one furnace input stack")
    return values


def compile_logistic_science(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("logistic science requires frozen positions")
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
            raise TypeError(f"logistic science requires position {name}")
        parsed[name]=_position(raw)

    science=_positive_int(params,"research_science_packs")
    science_batch=_positive_int(params,"science_batch")
    iron_target=_positive_int(params,"iron_plate_target")
    copper_target=_positive_int(params,"copper_plate_target")
    electric_coal_min=_positive_int(params,"electric_coal_min")
    electric_coal_target=_positive_int(params,"electric_coal_target")
    electric_coal_wait=_positive_int(
        params,"electric_coal_accumulation_seconds"
    )
    bootstrap_boiler=_positive_int(params,"bootstrap_boiler_refuel")
    iron_miner_refuel=_positive_int(params,"iron_miner_refuel")
    copper_miner_refuel=_positive_int(params,"copper_miner_refuel")
    iron_furnace_refuel=_positive_int(params,"iron_furnace_refuel")
    copper_furnace_refuel=_positive_int(params,"copper_furnace_refuel")
    boiler_refuel=_positive_int(params,"boiler_refuel")
    ore_recovery=_positive_int(params,"ore_recovery_window_seconds")
    smelt_batch_seconds=_positive_int(params,"smelt_batch_seconds")
    smelt_batch_count=_positive_int(params,"smelt_batch_count")
    research_window=_positive_int(params,"research_window_seconds")
    powered_window=_positive_int(
        params,"powered_manufacturing_window_seconds"
    )
    survival_recovery=_positive_int(params,"survival_recovery_window_seconds")
    survival_window=_positive_int(params,"survival_window_seconds")
    logistic_window=_positive_int(params,"logistic_output_window_seconds")
    soak_window=_positive_int(params,"sustainability_soak_seconds")

    if science<75 or science_batch<science+2:
        raise ValueError("logistic science requires 75 red science plus reserve")
    if iron_target<190 or copper_target<95:
        raise ValueError("logistic science raw-plate budget below causal DAG")
    if electric_coal_target<160:
        raise ValueError("logistic science electric-coal reserve below minimum")

    iron_batches=_batches(iron_target,smelt_batch_count)
    copper_batches=_batches(copper_target,2)
    research_round=max(30,research_window//8)
    powered_round=max(3,powered_window//2)

    smelt_lines: list[str]=[
        "cortex_logistic_iron_plate_ready=0",
        "cortex_logistic_copper_plate_ready=0",
    ]
    for index,iron_batch in enumerate(iron_batches):
        smelt_lines.extend([
            (
                f"cortex_iron_draw_{index}=extract_item("
                f"{_prototype('iron-ore')},cortex_iron_buffer,"
                f"quantity={iron_batch})"
            ),
            f"if cortex_iron_draw_{index} < {iron_batch}:",
            "    raise RuntimeError('logistic-science iron transfer incomplete')",
            (
                f"cortex_iron_furnace=insert_item({_prototype('iron-ore')},"
                f"cortex_iron_furnace,quantity={iron_batch})"
            ),
        ])
        if index<len(copper_batches):
            copper_batch=copper_batches[index]
            smelt_lines.extend([
                (
                    f"cortex_copper_draw_{index}=extract_item("
                    f"{_prototype('copper-ore')},cortex_copper_buffer,"
                    f"quantity={copper_batch})"
                ),
                f"if cortex_copper_draw_{index} < {copper_batch}:",
                (
                    "    raise RuntimeError("
                    "'logistic-science copper transfer incomplete')"
                ),
                (
                    f"cortex_copper_furnace=insert_item("
                    f"{_prototype('copper-ore')},cortex_copper_furnace,"
                    f"quantity={copper_batch})"
                ),
            ])
        smelt_lines.append(f"sleep({smelt_batch_seconds})")
        smelt_lines.extend([
            (
                f"cortex_iron_plate_batch_{index}=inspect_inventory("
                f"cortex_iron_furnace)[{_prototype('iron-plate')}]"
            ),
            f"if cortex_iron_plate_batch_{index} < {iron_batch}:",
            (
                "    raise RuntimeError("
                "'logistic-science iron smelting batch incomplete')"
            ),
            (
                f"cortex_logistic_iron_plate_ready+=extract_item("
                f"{_prototype('iron-plate')},cortex_iron_furnace,"
                f"quantity={iron_batch})"
            ),
        ])
        if index<len(copper_batches):
            copper_batch=copper_batches[index]
            smelt_lines.extend([
                (
                    f"cortex_copper_plate_batch_{index}=inspect_inventory("
                    f"cortex_copper_furnace)[{_prototype('copper-plate')}]"
                ),
                f"if cortex_copper_plate_batch_{index} < {copper_batch}:",
                (
                    "    raise RuntimeError("
                    "'logistic-science copper smelting batch incomplete')"
                ),
                (
                    f"cortex_logistic_copper_plate_ready+=extract_item("
                    f"{_prototype('copper-plate')},cortex_copper_furnace,"
                    f"quantity={copper_batch})"
                ),
            ])
    smelt_code="\n".join(smelt_lines)

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
cortex_electric_pole=get_entity({_prototype('small-electric-pole')},{parsed['electric_pole']})
cortex_electric_drill=get_entity({_prototype('electric-mining-drill')},{parsed['electric_drill']})
cortex_electric_buffer=get_entity({_prototype('wooden-chest')},{parsed['electric_buffer']})

cortex_promoted_network_id=getattr(cortex_power_pole,'electrical_id',None)
for cortex_promoted_consumer in (
    cortex_lab,cortex_assembler,cortex_electric_pole,cortex_electric_drill
):
    cortex_promoted_consumer_id=getattr(
        cortex_promoted_consumer,'electrical_id',None
    )
    if (
        cortex_promoted_network_id is None
        or cortex_promoted_consumer_id is None
        or cortex_promoted_consumer_id!=cortex_promoted_network_id
    ):
        raise RuntimeError('promoted electrical topology regressed before logistic science')

cortex_electric_coal_before=inspect_inventory(
    cortex_electric_buffer
)[{_prototype('coal')}]
if cortex_electric_coal_before < {electric_coal_min}:
    raise RuntimeError('electric coal buffer below logistic-science bootstrap minimum')
extract_item(
    {_prototype('coal')},cortex_electric_buffer,quantity={bootstrap_boiler}
)
cortex_boiler=insert_item(
    {_prototype('coal')},cortex_boiler,quantity={bootstrap_boiler}
)
sleep({electric_coal_wait})
cortex_electric_coal_after_accumulation=inspect_inventory(
    cortex_electric_buffer
)[{_prototype('coal')}]
if cortex_electric_coal_after_accumulation < {electric_coal_target}:
    raise RuntimeError('electric mining did not accumulate logistic-science coal budget')

cortex_logistic_coal_draw_total=(
    {iron_miner_refuel}+{copper_miner_refuel}+{iron_furnace_refuel}
    +{copper_furnace_refuel}+{boiler_refuel}
)
if cortex_electric_coal_after_accumulation < cortex_logistic_coal_draw_total:
    raise RuntimeError('electric coal budget below logistic-science fuel allocation')
extract_item(
    {_prototype('coal')},
    cortex_electric_buffer,
    quantity=cortex_logistic_coal_draw_total,
)
cortex_iron_extractor=insert_item(
    {_prototype('coal')},cortex_iron_extractor,quantity={iron_miner_refuel}
)
cortex_copper_extractor=insert_item(
    {_prototype('coal')},cortex_copper_extractor,quantity={copper_miner_refuel}
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

sleep({ore_recovery})
cortex_logistic_iron_ore_available=inspect_inventory(
    cortex_iron_buffer
)[{_prototype('iron-ore')}]
cortex_logistic_copper_ore_available=inspect_inventory(
    cortex_copper_buffer
)[{_prototype('copper-ore')}]
if cortex_logistic_iron_ore_available < {iron_target}:
    raise RuntimeError('endogenous iron ore below logistic-science budget')
if cortex_logistic_copper_ore_available < {copper_target}:
    raise RuntimeError('endogenous copper ore below logistic-science budget')

{smelt_code}
if cortex_logistic_iron_plate_ready < {iron_target}:
    raise RuntimeError('logistic-science iron plate budget incomplete')
if cortex_logistic_copper_plate_ready < {copper_target}:
    raise RuntimeError('logistic-science copper plate budget incomplete')

craft_item({_prototype('iron-gear-wheel')},quantity={science_batch})
craft_item({_prototype('automation-science-pack')},quantity={science_batch})
cortex_logistic_science_batch_ready=inspect_inventory()[
    {_prototype('automation-science-pack')}
]
if cortex_logistic_science_batch_ready < {science_batch}:
    raise RuntimeError('endogenous logistic-science research batch incomplete')
cortex_logistic_research_fed=insert_item(
    {_prototype('automation-science-pack')},
    cortex_lab,
    quantity={science},
)
cortex_logistic_research_requirements=set_research('logistic-science-pack')
cortex_logistic_research_remaining_count=0
for cortex_requirement in cortex_logistic_research_requirements:
    cortex_logistic_research_remaining_count+=cortex_requirement.count
for cortex_research_round_index in range(8):
    if cortex_logistic_research_remaining_count<=0:
        break
    sleep({research_round})
    cortex_logistic_research_remaining=get_research_progress(
        'logistic-science-pack'
    )
    cortex_logistic_research_remaining_count=0
    for cortex_requirement in cortex_logistic_research_remaining:
        cortex_logistic_research_remaining_count+=cortex_requirement.count
if cortex_logistic_research_remaining_count>0:
    raise RuntimeError('Logistic Science Pack research did not complete')
cortex_logistic_research_completed=True

cortex_science_replenish_before=inspect_inventory()[
    {_prototype('automation-science-pack')}
]
craft_item({_prototype('iron-gear-wheel')},quantity=1)
craft_item({_prototype('automation-science-pack')},quantity=1)
cortex_science_replenish_after=inspect_inventory()[
    {_prototype('automation-science-pack')}
]
cortex_science_replenished=max(
    0,cortex_science_replenish_after-cortex_science_replenish_before
)
if cortex_science_replenished<1:
    raise RuntimeError('automation science regressed during logistic research')
cortex_copper_buffer=insert_item(
    {_prototype('automation-science-pack')},
    cortex_copper_buffer,
    quantity=cortex_science_replenished,
)
cortex_science_buffer_after=inspect_inventory(
    cortex_copper_buffer
)[{_prototype('automation-science-pack')}]
cortex_automation_science_survives=(
    cortex_science_replenished>0 and cortex_science_buffer_after>0
)

cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_existing_circuit_output=inspect_inventory(
    cortex_assembler
)[{_prototype('electronic-circuit')}]
if cortex_existing_circuit_output>0:
    extract_item(
        {_prototype('electronic-circuit')},
        cortex_assembler,
        quantity=cortex_existing_circuit_output,
    )
cortex_existing_gear_output=inspect_inventory(
    cortex_assembler
)[{_prototype('iron-gear-wheel')}]
if cortex_existing_gear_output>0:
    extract_item(
        {_prototype('iron-gear-wheel')},
        cortex_assembler,
        quantity=cortex_existing_gear_output,
    )
cortex_assembler=set_entity_recipe(
    cortex_assembler,{_prototype('iron-gear-wheel')}
)
cortex_assembler=insert_item(
    {_prototype('iron-plate')},cortex_assembler,quantity=2
)
sleep({powered_round})
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_gear_output=inspect_inventory(
    cortex_assembler
)[{_prototype('iron-gear-wheel')}]
if cortex_gear_output<=0:
    raise RuntimeError('powered manufacturing gear output regressed')
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
sleep({powered_round})
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_circuit_output=inspect_inventory(
    cortex_assembler
)[{_prototype('electronic-circuit')}]
cortex_assembler_energy_after=float(cortex_assembler.energy or 0)
cortex_assembler_electrical_id=getattr(cortex_assembler,'electrical_id',None)
cortex_powered_manufacturing_survives=(
    cortex_gear_output>0
    and cortex_circuit_output>0
    and cortex_assembler_energy_after>0
    and cortex_assembler_electrical_id==cortex_promoted_network_id
)
if not cortex_powered_manufacturing_survives:
    raise RuntimeError('powered manufacturing capability regressed')
extract_item(
    {_prototype('electronic-circuit')},
    cortex_assembler,
    quantity=cortex_circuit_output,
)

cortex_survival_coal_available=inspect_inventory(
    cortex_electric_buffer
)[{_prototype('coal')}]
if cortex_survival_coal_available<10:
    sleep({survival_recovery})
    cortex_survival_coal_available=inspect_inventory(
        cortex_electric_buffer
    )[{_prototype('coal')}]
if cortex_survival_coal_available<10:
    raise RuntimeError('electric coal reserve unavailable for final survival')
extract_item({_prototype('coal')},cortex_electric_buffer,quantity=10)
cortex_iron_extractor=insert_item(
    {_prototype('coal')},cortex_iron_extractor,quantity=1
)
cortex_coal_extractor=insert_item(
    {_prototype('coal')},cortex_coal_extractor,quantity=1
)
cortex_iron_furnace=insert_item(
    {_prototype('coal')},cortex_iron_furnace,quantity=1
)
cortex_boiler=insert_item({_prototype('coal')},cortex_boiler,quantity=3)
cortex_copper_extractor=insert_item(
    {_prototype('coal')},cortex_copper_extractor,quantity=1
)
cortex_copper_furnace=insert_item(
    {_prototype('coal')},cortex_copper_furnace,quantity=1
)

cortex_survival_iron_available=inspect_inventory(
    cortex_iron_buffer
)[{_prototype('iron-ore')}]
cortex_survival_copper_available=inspect_inventory(
    cortex_copper_buffer
)[{_prototype('copper-ore')}]
if cortex_survival_iron_available<2 or cortex_survival_copper_available<2:
    sleep({survival_recovery})
    cortex_survival_iron_available=inspect_inventory(
        cortex_iron_buffer
    )[{_prototype('iron-ore')}]
    cortex_survival_copper_available=inspect_inventory(
        cortex_copper_buffer
    )[{_prototype('copper-ore')}]
if cortex_survival_iron_available<2:
    raise RuntimeError('iron reserve unavailable for logistic-science survival')
if cortex_survival_copper_available<2:
    raise RuntimeError('copper reserve unavailable for logistic-science survival')
extract_item({_prototype('iron-ore')},cortex_iron_buffer,quantity=2)
cortex_iron_furnace=insert_item(
    {_prototype('iron-ore')},cortex_iron_furnace,quantity=2
)
extract_item({_prototype('copper-ore')},cortex_copper_buffer,quantity=2)
cortex_copper_furnace=insert_item(
    {_prototype('copper-ore')},cortex_copper_furnace,quantity=2
)

cortex_iron_survival_before=inspect_inventory(
    cortex_iron_buffer
)[{_prototype('iron-ore')}]
cortex_coal_survival_before=inspect_inventory(
    cortex_coal_buffer
)[{_prototype('coal')}]
cortex_iron_smelting_before=inspect_inventory(
    cortex_iron_furnace
)[{_prototype('iron-plate')}]
cortex_copper_survival_before=inspect_inventory(
    cortex_copper_buffer
)[{_prototype('copper-ore')}]
cortex_copper_smelting_before=inspect_inventory(
    cortex_copper_furnace
)[{_prototype('copper-plate')}]
cortex_electric_survival_before=inspect_inventory(
    cortex_electric_buffer
)[{_prototype('coal')}]
sleep({survival_window})
cortex_iron_survival_after=inspect_inventory(
    cortex_iron_buffer
)[{_prototype('iron-ore')}]
cortex_coal_survival_after=inspect_inventory(
    cortex_coal_buffer
)[{_prototype('coal')}]
cortex_iron_smelting_after=inspect_inventory(
    cortex_iron_furnace
)[{_prototype('iron-plate')}]
cortex_copper_survival_after=inspect_inventory(
    cortex_copper_buffer
)[{_prototype('copper-ore')}]
cortex_copper_smelting_after=inspect_inventory(
    cortex_copper_furnace
)[{_prototype('copper-plate')}]
cortex_electric_survival_after=inspect_inventory(
    cortex_electric_buffer
)[{_prototype('coal')}]

cortex_iron_survival_growth=max(
    0,cortex_iron_survival_after-cortex_iron_survival_before
)
cortex_coal_survival_growth=max(
    0,cortex_coal_survival_after-cortex_coal_survival_before
)
cortex_smelting_survival_growth=max(
    0,cortex_iron_smelting_after-cortex_iron_smelting_before
)
cortex_copper_survival_growth=max(
    0,cortex_copper_survival_after-cortex_copper_survival_before
)
cortex_copper_smelting_growth=max(
    0,cortex_copper_smelting_after-cortex_copper_smelting_before
)
cortex_electric_survival_growth=max(
    0,cortex_electric_survival_after-cortex_electric_survival_before
)

cortex_steam_engine_live=get_entity(
    {_prototype('steam-engine')},{parsed['steam_engine']}
)
cortex_steam_survival_amount=0.0
for cortex_fluid in (cortex_steam_engine_live.fluid_box or []):
    if 'steam' in str(cortex_fluid.get('name','')).lower():
        cortex_steam_survival_amount+=float(cortex_fluid.get('amount',0) or 0)
cortex_steam_survival_energy=float(cortex_steam_engine_live.energy or 0)

cortex_electric_drill=get_entity(
    {_prototype('electric-mining-drill')},{parsed['electric_drill']}
)
cortex_electric_pole=get_entity(
    {_prototype('small-electric-pole')},{parsed['electric_pole']}
)
cortex_electric_drill_survival_energy=float(
    cortex_electric_drill.energy or 0
)
cortex_electric_drill_survival_electrical_id=getattr(
    cortex_electric_drill,'electrical_id',None
)
cortex_electric_pole_survival_electrical_id=getattr(
    cortex_electric_pole,'electrical_id',None
)

cortex_iron_extraction_survives=cortex_iron_survival_growth>0
cortex_coal_self_sufficiency_survives=cortex_coal_survival_growth>0
cortex_iron_smelting_survives=cortex_smelting_survival_growth>0
cortex_steam_power_survives=(
    cortex_steam_survival_amount>0 and cortex_steam_survival_energy>0
)
cortex_copper_chain_survives=(
    cortex_copper_survival_growth>0 and cortex_copper_smelting_growth>0
)
cortex_electric_mining_survives=(
    cortex_electric_survival_growth>0
    and cortex_electric_drill_survival_energy>0
    and cortex_electric_drill_survival_electrical_id is not None
    and cortex_electric_pole_survival_electrical_id is not None
    and cortex_electric_drill_survival_electrical_id
        ==cortex_electric_pole_survival_electrical_id
)
cortex_all_promoted_capabilities_alive=(
    cortex_iron_extraction_survives
    and cortex_coal_self_sufficiency_survives
    and cortex_iron_smelting_survives
    and cortex_steam_power_survives
    and cortex_copper_chain_survives
    and cortex_automation_science_survives
    and cortex_powered_manufacturing_survives
    and cortex_electric_mining_survives
)
if not cortex_all_promoted_capabilities_alive:
    raise RuntimeError('one or more promoted capabilities regressed before green science')

craft_item({_prototype('copper-cable')},quantity=6)
craft_item({_prototype('electronic-circuit')},quantity=2)
craft_item({_prototype('iron-gear-wheel')},quantity=4)
craft_item({_prototype('transport-belt')},quantity=2)
craft_item({_prototype('inserter')},quantity=2)
cortex_logistic_belts_ready=inspect_inventory()[
    {_prototype('transport-belt')}
]
cortex_logistic_inserters_ready=inspect_inventory()[
    {_prototype('inserter')}
]
if cortex_logistic_belts_ready<2 or cortex_logistic_inserters_ready<2:
    raise RuntimeError('endogenous green-science intermediates incomplete')
cortex_inputs_endogenous=True

cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_assembler=set_entity_recipe(
    cortex_assembler,{_prototype('logistic-science-pack')}
)
cortex_assembler=insert_item(
    {_prototype('transport-belt')},cortex_assembler,quantity=2
)
cortex_assembler=insert_item(
    {_prototype('inserter')},cortex_assembler,quantity=2
)
cortex_logistic_output_before=inspect_inventory(
    cortex_assembler
)[{_prototype('logistic-science-pack')}]
sleep({logistic_window})
cortex_logistic_output_first=inspect_inventory(
    cortex_assembler
)[{_prototype('logistic-science-pack')}]
cortex_logistic_first_growth=max(
    0,cortex_logistic_output_first-cortex_logistic_output_before
)
if cortex_logistic_first_growth<=0:
    raise RuntimeError('logistic science produced no first output')
extract_item(
    {_prototype('logistic-science-pack')},
    cortex_assembler,
    quantity=cortex_logistic_output_first,
)
sleep({soak_window})
cortex_logistic_output_second=inspect_inventory(
    cortex_assembler
)[{_prototype('logistic-science-pack')}]
cortex_logistic_science_output_positive=cortex_logistic_first_growth>0
cortex_sustainability_soak_passed=cortex_logistic_output_second>0
cortex_assembler=get_entity(
    {_prototype('assembling-machine-1')},{parsed['assembler']}
)
cortex_logistic_assembler_energy=float(cortex_assembler.energy or 0)
cortex_logistic_assembler_electrical_id=getattr(
    cortex_assembler,'electrical_id',None
)
cortex_sustainability_soak_passed=(
    cortex_sustainability_soak_passed
    and cortex_logistic_assembler_energy>0
    and cortex_logistic_assembler_electrical_id==cortex_promoted_network_id
)
if not cortex_sustainability_soak_passed:
    raise RuntimeError('logistic science sustainability soak failed')

print({{
    'logistic_science_output_positive':cortex_logistic_science_output_positive,
    'inputs_endogenous':cortex_inputs_endogenous,
    'all_promoted_capabilities_alive':cortex_all_promoted_capabilities_alive,
    'sustainability_soak_passed':cortex_sustainability_soak_passed,
    'research_completed':cortex_logistic_research_completed,
    'research_remaining_count':cortex_logistic_research_remaining_count,
    'science_batch_ready':cortex_logistic_science_batch_ready,
    'iron_plate_ready':cortex_logistic_iron_plate_ready,
    'copper_plate_ready':cortex_logistic_copper_plate_ready,
    'electric_coal_before':cortex_electric_coal_before,
    'electric_coal_after_accumulation':cortex_electric_coal_after_accumulation,
    'logistic_belts_ready':cortex_logistic_belts_ready,
    'logistic_inserters_ready':cortex_logistic_inserters_ready,
    'logistic_first_growth':cortex_logistic_first_growth,
    'logistic_second_output':cortex_logistic_output_second,
    'logistic_assembler_energy':cortex_logistic_assembler_energy,
    'logistic_assembler_electrical_id':cortex_logistic_assembler_electrical_id,
    'science_replenished':cortex_science_replenished,
    'science_buffer_after':cortex_science_buffer_after,
    'gear_output':cortex_gear_output,
    'circuit_output':cortex_circuit_output,
    'iron_survival_growth':cortex_iron_survival_growth,
    'coal_survival_growth':cortex_coal_survival_growth,
    'smelting_survival_growth':cortex_smelting_survival_growth,
    'copper_survival_growth':cortex_copper_survival_growth,
    'copper_smelting_growth':cortex_copper_smelting_growth,
    'electric_survival_growth':cortex_electric_survival_growth,
    'steam_survival_amount':cortex_steam_survival_amount,
    'steam_survival_energy':cortex_steam_survival_energy,
    'electric_drill_survival_energy':cortex_electric_drill_survival_energy,
    'electric_drill_survival_electrical_id':cortex_electric_drill_survival_electrical_id,
    'electric_pole_survival_electrical_id':cortex_electric_pole_survival_electrical_id,
    'automation_science_survives':cortex_automation_science_survives,
    'powered_manufacturing_survives':cortex_powered_manufacturing_survives,
    'electric_mining_survives':cortex_electric_mining_survives,
    'iron_extraction_survives':cortex_iron_extraction_survives,
    'coal_self_sufficiency_survives':cortex_coal_self_sufficiency_survives,
    'iron_smelting_survives':cortex_iron_smelting_survives,
    'steam_power_survives':cortex_steam_power_survives,
    'copper_chain_survives':cortex_copper_chain_survives,
}})
"""
    return code.strip().splitlines()
