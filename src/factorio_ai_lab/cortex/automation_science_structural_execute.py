"""Compiler for the F5-C automation-science transactional Option."""

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


def compile_automation_science(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("automation science requires frozen positions")
    required=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer",
        "boiler","steam_engine",
        "copper_extractor","copper_buffer","copper_furnace",
    )
    parsed: dict[str,str]={}
    for name in required:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"automation science requires position {name}")
        parsed[name]=_position(raw)

    target=_positive_int(params,"target_packs")
    batch=_positive_int(params,"batch_packs")
    iron_target=_positive_int(params,"iron_plate_target")
    copper_target=_positive_int(params,"copper_plate_target")
    initial_coal=_positive_int(params,"initial_coal_draw")
    iron_refuel=_positive_int(params,"iron_initial_refuel")
    coal_refuel=_positive_int(params,"coal_initial_refuel")
    copper_refuel=_positive_int(params,"copper_initial_refuel")
    survival_coal=_positive_int(params,"survival_coal_target")
    survival_iron=_positive_int(params,"survival_iron_ore_draw")
    survival_copper=_positive_int(params,"survival_copper_ore_draw")
    iron_recovery=_positive_int(params,"iron_recovery_window_seconds")
    iron_smelt=_positive_int(params,"iron_smelt_window_seconds")
    copper_smelt=_positive_int(params,"copper_smelt_window_seconds")
    batch_gap=_positive_int(params,"batch_gap_seconds")
    survival_recovery=_positive_int(params,"survival_recovery_window_seconds")
    survival_window=_positive_int(params,"survival_window_seconds")

    if batch*2!=target:
        raise ValueError("automation science requires exactly two equal batches")
    if iron_target < target*2:
        raise ValueError("iron plate target cannot cover gear inputs")
    if copper_target < target:
        raise ValueError("copper plate target cannot cover science inputs")

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
        "cortex_copper_extractor=get_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    {parsed['copper_extractor']},",
        ")",
        "cortex_copper_buffer=get_entity(",
        f"    {_prototype('wooden-chest')},",
        f"    {parsed['copper_buffer']},",
        ")",
        "cortex_copper_furnace=get_entity(",
        f"    {_prototype('stone-furnace')},",
        f"    {parsed['copper_furnace']},",
        ")",
        (
            "cortex_initial_coal_available=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_initial_coal_available < {initial_coal}:",
        "    raise RuntimeError('endogenous coal below automation-science bootstrap')",
        "cortex_initial_coal_draw=extract_item(",
        f"    {_prototype('coal')},",
        "    cortex_coal_buffer,",
        f"    quantity={initial_coal},",
        ")",
        "cortex_iron_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_iron_extractor,quantity={iron_refuel}",
        ")",
        "cortex_coal_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_coal_extractor,quantity={coal_refuel}",
        ")",
        "cortex_copper_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_copper_extractor,quantity={copper_refuel}",
        ")",
        (
            "cortex_iron_plate_existing=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "cortex_iron_plate_drawn=0",
        "if cortex_iron_plate_existing>0:",
        "    cortex_iron_plate_drawn=extract_item(",
        f"        {_prototype('iron-plate')},",
        "        cortex_iron_furnace,",
        f"        quantity=min({iron_target},cortex_iron_plate_existing),",
        "    )",
        (
            f"cortex_iron_plate_shortfall=max(0,{iron_target}-"
            "inspect_inventory()["
            f"{_prototype('iron-plate')}])"
        ),
        "cortex_iron_topup_ore=0",
        "cortex_iron_topup_plates=0",
        "if cortex_iron_plate_shortfall>0:",
        (
            "    cortex_iron_ore_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        "    if cortex_iron_ore_available < cortex_iron_plate_shortfall:",
        f"        sleep({iron_recovery})",
        (
            "        cortex_iron_ore_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        "    if cortex_iron_ore_available < cortex_iron_plate_shortfall:",
        "        raise RuntimeError('endogenous iron below automation-science input budget')",
        "    cortex_iron_topup_ore=extract_item(",
        f"        {_prototype('iron-ore')},",
        "        cortex_iron_buffer,",
        "        quantity=cortex_iron_plate_shortfall,",
        "    )",
        (
            f"    if cortex_iron_furnace.fuel[{_prototype('coal')}]<1:"
        ),
        "        cortex_iron_furnace=insert_item(",
        f"            {_prototype('coal')},",
        "            cortex_iron_furnace,",
        "            quantity=1,",
        "        )",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('iron-ore')},",
        "        cortex_iron_furnace,",
        "        quantity=cortex_iron_plate_shortfall,",
        "    )",
        f"    sleep({iron_smelt})",
        (
            "    cortex_iron_topup_available=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "    if cortex_iron_topup_available < cortex_iron_plate_shortfall:",
        "        raise RuntimeError('automation-science iron top-up did not smelt')",
        "    cortex_iron_topup_plates=extract_item(",
        f"        {_prototype('iron-plate')},",
        "        cortex_iron_furnace,",
        "        quantity=cortex_iron_plate_shortfall,",
        "    )",
        (
            "cortex_iron_plate_ready=inspect_inventory()"
            f"[{_prototype('iron-plate')}]"
        ),
        f"if cortex_iron_plate_ready < {iron_target}:",
        "    raise RuntimeError('automation-science iron plates unavailable')",
        (
            "cortex_copper_plate_existing=inspect_inventory(cortex_copper_furnace)"
            f"[{_prototype('copper-plate')}]"
        ),
        "cortex_copper_plate_drawn=0",
        "if cortex_copper_plate_existing>0:",
        "    cortex_copper_plate_drawn=extract_item(",
        f"        {_prototype('copper-plate')},",
        "        cortex_copper_furnace,",
        f"        quantity=min({copper_target},cortex_copper_plate_existing),",
        "    )",
        (
            f"cortex_copper_plate_shortfall=max(0,{copper_target}-"
            "inspect_inventory()["
            f"{_prototype('copper-plate')}])"
        ),
        "cortex_copper_topup_ore=0",
        "cortex_copper_topup_plates=0",
        "if cortex_copper_plate_shortfall>0:",
        (
            "    cortex_copper_ore_available=inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('copper-ore')}]"
        ),
        "    if cortex_copper_ore_available < cortex_copper_plate_shortfall:",
        "        raise RuntimeError('endogenous copper below automation-science input budget')",
        "    cortex_copper_topup_ore=extract_item(",
        f"        {_prototype('copper-ore')},",
        "        cortex_copper_buffer,",
        "        quantity=cortex_copper_plate_shortfall,",
        "    )",
        (
            f"    if cortex_copper_furnace.fuel[{_prototype('coal')}]<1:"
        ),
        "        cortex_copper_furnace=insert_item(",
        f"            {_prototype('coal')},",
        "            cortex_copper_furnace,",
        "            quantity=1,",
        "        )",
        "    cortex_copper_furnace=insert_item(",
        f"        {_prototype('copper-ore')},",
        "        cortex_copper_furnace,",
        "        quantity=cortex_copper_plate_shortfall,",
        "    )",
        f"    sleep({copper_smelt})",
        (
            "    cortex_copper_topup_available=inspect_inventory(cortex_copper_furnace)"
            f"[{_prototype('copper-plate')}]"
        ),
        "    if cortex_copper_topup_available < cortex_copper_plate_shortfall:",
        "        raise RuntimeError('automation-science copper top-up did not smelt')",
        "    cortex_copper_topup_plates=extract_item(",
        f"        {_prototype('copper-plate')},",
        "        cortex_copper_furnace,",
        "        quantity=cortex_copper_plate_shortfall,",
        "    )",
        (
            "cortex_copper_plate_ready=inspect_inventory()"
            f"[{_prototype('copper-plate')}]"
        ),
        f"if cortex_copper_plate_ready < {copper_target}:",
        "    raise RuntimeError('automation-science copper plates unavailable')",
        (
            "cortex_science_before=inspect_inventory()"
            f"[{_prototype('automation-science-pack')}]"
        ),
        f"craft_item({_prototype('iron-gear-wheel')},quantity={target})",
        (
            "cortex_gear_ready=inspect_inventory()"
            f"[{_prototype('iron-gear-wheel')}]"
        ),
        f"if cortex_gear_ready < {target}:",
        "    raise RuntimeError('automation-science gear crafting incomplete')",
        f"craft_item({_prototype('automation-science-pack')},quantity={batch})",
        (
            "cortex_science_after_batch1=inspect_inventory()"
            f"[{_prototype('automation-science-pack')}]"
        ),
        f"sleep({batch_gap})",
        f"craft_item({_prototype('automation-science-pack')},quantity={batch})",
        (
            "cortex_science_after_batch2=inspect_inventory()"
            f"[{_prototype('automation-science-pack')}]"
        ),
        (
            "cortex_science_batch1=max(0,cortex_science_after_batch1-"
            "cortex_science_before)"
        ),
        (
            "cortex_science_batch2=max(0,cortex_science_after_batch2-"
            "cortex_science_after_batch1)"
        ),
        (
            "cortex_automation_science_inventory=max(0,"
            "cortex_science_after_batch2-cortex_science_before)"
        ),
        (
            f"cortex_inputs_endogenous=(cortex_iron_plate_ready>={iron_target} "
            f"and cortex_copper_plate_ready>={copper_target} "
            f"and cortex_gear_ready>={target})"
        ),
        (
            "cortex_automation_science_output_positive=("
            "cortex_automation_science_inventory>0)"
        ),
        (
            f"cortex_production_sustained=(cortex_science_batch1>={batch} "
            f"and cortex_science_batch2>={batch})"
        ),
        (
            "cortex_survival_coal_player=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        (
            f"cortex_survival_coal_shortfall=max(0,{survival_coal}-"
            "cortex_survival_coal_player)"
        ),
        "if cortex_survival_coal_shortfall>0:",
        (
            "    cortex_survival_coal_buffer=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        "    if cortex_survival_coal_buffer < cortex_survival_coal_shortfall:",
        "        raise RuntimeError('endogenous coal below automation-science survival reserve')",
        "    extract_item(",
        f"        {_prototype('coal')},",
        "        cortex_coal_buffer,",
        "        quantity=cortex_survival_coal_shortfall,",
        "    )",
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
        "cortex_copper_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_copper_extractor,quantity=1",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('coal')},cortex_copper_furnace,quantity=1",
        ")",
        (
            "cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "cortex_survival_copper_available=inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('copper-ore')}]"
        ),
        (
            f"if cortex_survival_iron_available < {survival_iron} "
            f"or cortex_survival_copper_available < {survival_copper}:"
        ),
        f"    sleep({survival_recovery})",
        (
            "    cortex_survival_iron_available=inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "    cortex_survival_copper_available=inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('copper-ore')}]"
        ),
        f"if cortex_survival_iron_available < {survival_iron}:",
        "    raise RuntimeError('iron reserve unavailable for science survival')",
        f"if cortex_survival_copper_available < {survival_copper}:",
        "    raise RuntimeError('copper reserve unavailable for science survival')",
        "extract_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_buffer,",
        f"    quantity={survival_iron},",
        ")",
        "cortex_iron_furnace=insert_item(",
        f"    {_prototype('iron-ore')},",
        "    cortex_iron_furnace,",
        f"    quantity={survival_iron},",
        ")",
        "extract_item(",
        f"    {_prototype('copper-ore')},",
        "    cortex_copper_buffer,",
        f"    quantity={survival_copper},",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('copper-ore')},",
        "    cortex_copper_furnace,",
        f"    quantity={survival_copper},",
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
            "cortex_iron_smelting_before=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_copper_survival_before=inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('copper-ore')}]"
        ),
        (
            "cortex_copper_smelting_before=inspect_inventory(cortex_copper_furnace)"
            f"[{_prototype('copper-plate')}]"
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
            "cortex_iron_smelting_after=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "cortex_copper_survival_after=inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('copper-ore')}]"
        ),
        (
            "cortex_copper_smelting_after=inspect_inventory(cortex_copper_furnace)"
            f"[{_prototype('copper-plate')}]"
        ),
        "cortex_iron_survival_growth=max(0,cortex_iron_survival_after-cortex_iron_survival_before)",
        "cortex_coal_survival_growth=max(0,cortex_coal_survival_after-cortex_coal_survival_before)",
        "cortex_smelting_survival_growth=max(0,cortex_iron_smelting_after-cortex_iron_smelting_before)",
        "cortex_copper_survival_growth=max(0,cortex_copper_survival_after-cortex_copper_survival_before)",
        "cortex_copper_smelting_growth=max(0,cortex_copper_smelting_after-cortex_copper_smelting_before)",
        "cortex_steam_engine_live=get_entity(",
        f"    {_prototype('steam-engine')},",
        f"    {parsed['steam_engine']},",
        ")",
        "cortex_steam_survival_amount=0.0",
        "for cortex_fluid in (cortex_steam_engine_live.fluid_box or []):",
        "    if 'steam' in str(cortex_fluid.get('name','')).lower():",
        "        cortex_steam_survival_amount+=float(cortex_fluid.get('amount',0) or 0)",
        "cortex_steam_survival_energy=float(cortex_steam_engine_live.energy or 0)",
        "cortex_iron_extraction_survives=cortex_iron_survival_growth>0",
        "cortex_coal_self_sufficiency_survives=cortex_coal_survival_growth>0",
        "cortex_iron_smelting_survives=cortex_smelting_survival_growth>0",
        (
            "cortex_steam_power_survives=("
            "cortex_steam_survival_amount>0 and cortex_steam_survival_energy>0)"
        ),
        (
            "cortex_copper_chain_survives=("
            "cortex_copper_survival_growth>0 and cortex_copper_smelting_growth>0)"
        ),
        "print({",
        "    'automation_science_output_positive':cortex_automation_science_output_positive,",
        "    'inputs_endogenous':cortex_inputs_endogenous,",
        "    'production_sustained':cortex_production_sustained,",
        "    'automation_science_inventory':cortex_automation_science_inventory,",
        "    'science_batch1':cortex_science_batch1,",
        "    'science_batch2':cortex_science_batch2,",
        "    'iron_plate_ready':cortex_iron_plate_ready,",
        "    'copper_plate_ready':cortex_copper_plate_ready,",
        "    'iron_topup_ore':cortex_iron_topup_ore,",
        "    'iron_topup_plates':cortex_iron_topup_plates,",
        "    'copper_topup_ore':cortex_copper_topup_ore,",
        "    'copper_topup_plates':cortex_copper_topup_plates,",
        "    'iron_extraction_survives':cortex_iron_extraction_survives,",
        "    'coal_self_sufficiency_survives':cortex_coal_self_sufficiency_survives,",
        "    'iron_smelting_survives':cortex_iron_smelting_survives,",
        "    'steam_power_survives':cortex_steam_power_survives,",
        "    'copper_chain_survives':cortex_copper_chain_survives,",
        "    'iron_survival_growth':cortex_iron_survival_growth,",
        "    'coal_survival_growth':cortex_coal_survival_growth,",
        "    'smelting_survival_growth':cortex_smelting_survival_growth,",
        "    'copper_survival_growth':cortex_copper_survival_growth,",
        "    'copper_smelting_growth':cortex_copper_smelting_growth,",
        "    'steam_survival_amount':cortex_steam_survival_amount,",
        "    'steam_survival_energy':cortex_steam_survival_energy,",
        "})",
    ]
