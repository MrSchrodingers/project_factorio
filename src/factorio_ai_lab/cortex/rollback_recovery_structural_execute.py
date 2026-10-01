"""Compiler for exact F5 rollback recovery of the promoted baseline."""

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


def _positive_int(params: Mapping[str,Any],name: str) -> int:
    value=params.get(name)
    if (
        not isinstance(value,Real) or isinstance(value,bool)
        or int(value)<=0 or float(value)!=float(int(value))
    ):
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def compile_rollback_recovery(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("rollback recovery requires frozen positions")
    required=(
        "stone","wood","coal_resource",
        "coal_extractor","coal_buffer","coal_quarantine",
        "iron_buffer","iron_furnace",
        "copper_buffer","copper_furnace",
    )
    parsed: dict[str,str]={}
    for name in required:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"rollback recovery requires position {name}")
        parsed[name]=_position(raw)

    stone_required=_positive_int(params,"stone_required")
    wood_required=_positive_int(params,"wood_required")
    coal_required=_positive_int(params,"coal_bootstrap_required")
    iron_required=_positive_int(params,"iron_plate_required")
    iron_smelt_seconds=_positive_int(params,"iron_smelt_seconds")
    coal_recovery_seconds=_positive_int(params,"coal_recovery_seconds")
    copper_smelt_seconds=_positive_int(params,"copper_smelt_seconds")
    settle_seconds=_positive_int(params,"settle_seconds")
    coal_stock_target=_positive_int(params,"coal_stock_target")
    science_buffer_min=_positive_int(params,"science_buffer_min")

    if stone_required<10:
        raise ValueError("baseline recovery requires stone for two furnaces")
    if wood_required<4:
        raise ValueError("baseline recovery requires wood for two chests")
    if coal_required<4:
        raise ValueError("baseline recovery requires endogenous coal bootstrap")
    if iron_required<9:
        raise ValueError("baseline recovery requires burner drill iron budget")

    return [
        f"cortex_iron_buffer=get_entity({_prototype('wooden-chest')},{parsed['iron_buffer']})",
        f"cortex_iron_furnace=get_entity({_prototype('stone-furnace')},{parsed['iron_furnace']})",
        f"cortex_copper_buffer=get_entity({_prototype('wooden-chest')},{parsed['copper_buffer']})",
        f"cortex_fast_reposition({parsed['stone']})",
        (
            "cortex_recovery_stone_before=inspect_inventory()"
            f"[{_prototype('stone')}]"
        ),
        "cortex_recovery_stone_harvested=cortex_mine_exact_resource(",
        f"    {parsed['stone']},'stone',quantity={stone_required},radius=3",
        ")",
        (
            "cortex_recovery_stone_after=inspect_inventory()"
            f"[{_prototype('stone')}]"
        ),
        (
            "if cortex_recovery_stone_after-cortex_recovery_stone_before"
            f" < {stone_required}:"
        ),
        "    raise RuntimeError('baseline recovery stone inventory incomplete')",
        f"craft_item({_prototype('stone-furnace')},quantity=2)",
        (
            "cortex_recovery_reserved_furnaces=inspect_inventory()"
            f"[{_prototype('stone-furnace')}]"
        ),
        "if cortex_recovery_reserved_furnaces < 2:",
        "    raise RuntimeError('baseline recovery furnace reservation incomplete')",
        f"cortex_fast_reposition({parsed['coal_resource']})",
        (
            "cortex_recovery_coal_before=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "cortex_recovery_coal_harvested=cortex_mine_exact_resource(",
        f"    {parsed['coal_resource']},'coal',quantity={coal_required},radius=3",
        ")",
        (
            "cortex_recovery_coal_after=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        (
            "if cortex_recovery_coal_after-cortex_recovery_coal_before"
            f" < {coal_required}:"
        ),
        "    raise RuntimeError('baseline recovery coal inventory incomplete')",
        f"cortex_fast_reposition({parsed['wood']})",
        (
            "cortex_recovery_wood_before=inspect_inventory()"
            f"[{_prototype('wood')}]"
        ),
        "cortex_recovery_wood_harvested=harvest_resource(",
        f"    {parsed['wood']},quantity={wood_required},radius=24",
        ")",
        (
            "cortex_recovery_wood_after=inspect_inventory()"
            f"[{_prototype('wood')}]"
        ),
        (
            "if cortex_recovery_wood_after-cortex_recovery_wood_before"
            f" < {wood_required}:"
        ),
        "    raise RuntimeError('baseline recovery wood inventory incomplete')",
        (
            "cortex_recovery_iron_existing=inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        "cortex_recovery_iron_existing_transfer=0",
        "if cortex_recovery_iron_existing>0:",
        "    cortex_recovery_iron_existing_transfer=cortex_transfer_exact_item(",
        f"        {parsed['iron_furnace']},'stone-furnace','iron-plate',",
        f"        quantity=min({iron_required},cortex_recovery_iron_existing),",
        "    )",
        "cortex_recovery_iron_ready=cortex_recovery_iron_existing_transfer",
        f"if cortex_recovery_iron_ready < {iron_required}:",
        (
            f"    cortex_recovery_iron_shortfall={iron_required}-"
            "cortex_recovery_iron_ready"
        ),
        (
            "    cortex_recovery_iron_ore_available="
            "inspect_inventory(cortex_iron_buffer)"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "    if cortex_recovery_iron_ore_available"
            " < cortex_recovery_iron_shortfall:"
        ),
        "        raise RuntimeError('baseline recovery iron reserve incomplete')",
        "    cortex_transfer_exact_item(",
        f"        {parsed['iron_buffer']},'wooden-chest','iron-ore',",
        "        quantity=cortex_recovery_iron_shortfall,",
        "    )",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('coal')},cortex_iron_furnace,quantity=1,",
        "    )",
        "    cortex_iron_furnace=insert_item(",
        f"        {_prototype('iron-ore')},cortex_iron_furnace,",
        "        quantity=cortex_recovery_iron_shortfall,",
        "    )",
        f"    sleep({iron_smelt_seconds})",
        (
            "    cortex_recovery_iron_smelted="
            "inspect_inventory(cortex_iron_furnace)"
            f"[{_prototype('iron-plate')}]"
        ),
        (
            "    if cortex_recovery_iron_smelted"
            " < cortex_recovery_iron_shortfall:"
        ),
        "        raise RuntimeError('baseline recovery iron smelting incomplete')",
        "    cortex_recovery_iron_smelt_transfer=cortex_transfer_exact_item(",
        f"        {parsed['iron_furnace']},'stone-furnace','iron-plate',",
        "        quantity=cortex_recovery_iron_shortfall,",
        "    )",
        "else:",
        "    cortex_recovery_iron_smelt_transfer=0",
        (
            "cortex_recovery_iron_plates_ready="
            "cortex_recovery_iron_existing_transfer"
            "+cortex_recovery_iron_smelt_transfer"
        ),
        f"if cortex_recovery_iron_plates_ready < {iron_required}:",
        (
            "    raise RuntimeError("
            "f'baseline recovery construction iron incomplete: "
            "existing={cortex_recovery_iron_existing_transfer}, "
            "smelted={cortex_recovery_iron_smelt_transfer}, "
            "total={cortex_recovery_iron_plates_ready}')"
        ),
        f"craft_item({_prototype('burner-mining-drill')},quantity=1)",
        f"craft_item({_prototype('wooden-chest')},quantity=2)",
        f"cortex_fast_reposition({parsed['coal_extractor']})",
        "cortex_coal_extractor=place_entity(",
        f"    {_prototype('burner-mining-drill')},",
        f"    position={parsed['coal_extractor']},direction=Direction.DOWN,",
        ")",
        "cortex_coal_buffer=place_entity(",
        f"    {_prototype('wooden-chest')},position={parsed['coal_buffer']},",
        ")",
        "cortex_coal_quarantine=place_entity(",
        f"    {_prototype('wooden-chest')},position={parsed['coal_quarantine']},",
        ")",
        "cortex_coal_extractor=insert_item(",
        f"    {_prototype('coal')},cortex_coal_extractor,quantity=1,",
        ")",
        f"sleep({coal_recovery_seconds})",
        (
            "cortex_recovery_coal_stock=inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        f"if cortex_recovery_coal_stock < {coal_stock_target}:",
        "    raise RuntimeError('baseline recovery coal stock target not reached')",
        (
            "cortex_recovery_quarantine_stock="
            "inspect_inventory(cortex_coal_quarantine)"
            f"[{_prototype('coal')}]"
        ),
        "if cortex_recovery_quarantine_stock != 0:",
        "    raise RuntimeError('baseline recovery quarantine must remain empty')",
        "cortex_recovery_copper_coal=cortex_transfer_exact_item(",
        f"    {parsed['coal_buffer']},'wooden-chest','coal',quantity=1,",
        ")",
        "cortex_recovery_copper_ore=cortex_transfer_exact_item(",
        f"    {parsed['copper_buffer']},'wooden-chest','copper-ore',quantity=2,",
        ")",
        f"cortex_fast_reposition({parsed['copper_furnace']})",
        "cortex_copper_furnace=place_entity(",
        f"    {_prototype('stone-furnace')},position={parsed['copper_furnace']},",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('coal')},cortex_copper_furnace,quantity=1,",
        ")",
        "cortex_copper_furnace=insert_item(",
        f"    {_prototype('copper-ore')},cortex_copper_furnace,quantity=2,",
        ")",
        f"sleep({copper_smelt_seconds})",
        (
            "cortex_recovery_copper_plate_count="
            "inspect_inventory(cortex_copper_furnace)"
            f"[{_prototype('copper-plate')}]"
        ),
        "if cortex_recovery_copper_plate_count<=0:",
        "    raise RuntimeError('baseline recovery copper smelting did not resume')",
        (
            "cortex_recovery_coal_stock_final="
            "inspect_inventory(cortex_coal_buffer)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_recovery_science_buffer="
            "inspect_inventory(cortex_copper_buffer)"
            f"[{_prototype('automation-science-pack')}]"
        ),
        f"sleep({settle_seconds})",
        "cortex_promoted_baseline_restored=(",
        "    cortex_coal_extractor is not None",
        "    and cortex_coal_buffer is not None",
        "    and cortex_coal_quarantine is not None",
        "    and cortex_copper_furnace is not None",
        ")",
        (
            "cortex_coal_stock_recovered="
            f"cortex_recovery_coal_stock_final>={max(11,coal_stock_target-1)}"
        ),
        "cortex_copper_smelting_restored=cortex_recovery_copper_plate_count>0",
        (
            "cortex_science_buffer_intact="
            f"cortex_recovery_science_buffer>={science_buffer_min}"
        ),
        "cortex_promoted_entity_restored=cortex_promoted_baseline_restored",
        "print({",
        "    'promoted_baseline_restored':cortex_promoted_baseline_restored,",
        "    'coal_stock_recovered':cortex_coal_stock_recovered,",
        "    'copper_smelting_restored':cortex_copper_smelting_restored,",
        "    'science_buffer_intact':cortex_science_buffer_intact,",
        "    'recovery_stone_harvested':cortex_recovery_stone_harvested,",
        "    'recovery_coal_harvested':cortex_recovery_coal_harvested,",
        "    'recovery_wood_harvested':cortex_recovery_wood_harvested,",
        "    'recovery_iron_plates_ready':cortex_recovery_iron_plates_ready,",
        "    'recovery_coal_stock_final':cortex_recovery_coal_stock_final,",
        "    'recovery_copper_plate_count':cortex_recovery_copper_plate_count,",
        "    'recovery_science_buffer':cortex_recovery_science_buffer,",
        "})",
    ]
