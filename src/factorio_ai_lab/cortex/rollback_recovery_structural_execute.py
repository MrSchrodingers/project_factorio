"""Compiler for exact F5 rollback recovery of the promoted baseline."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any

from fle.env.game_types import Prototype

from factorio_ai_lab.cortex.structural_prepare import StructuralOperation

RECOVERABLE_COMPONENTS=frozenset({
    "iron_extractor",
    "iron_buffer",
    "iron_furnace",
    "coal_extractor",
    "coal_buffer",
    "coal_quarantine",
    "copper_furnace",
})


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


def _missing_components(raw: Any) -> tuple[str,...]:
    if not isinstance(raw,Sequence) or isinstance(raw,(str,bytes)):
        raise TypeError("rollback recovery requires missing_components sequence")
    missing=tuple(sorted({str(value) for value in raw}))
    if not missing:
        raise ValueError("rollback recovery requires a non-empty missing subset")
    invalid=tuple(value for value in missing if value not in RECOVERABLE_COMPONENTS)
    if invalid:
        raise ValueError(f"unsupported rollback recovery components {invalid!r}")
    return missing


def compile_rollback_recovery(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    positions=params.get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("rollback recovery requires frozen positions")
    required=(
        "stone","wood","coal_resource","iron_resource",
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","coal_quarantine",
        "boiler","copper_extractor","copper_buffer","copper_furnace",
    )
    parsed: dict[str,str]={}
    for name in required:
        raw=positions.get(name)
        if not isinstance(raw,Mapping):
            raise TypeError(f"rollback recovery requires position {name}")
        parsed[name]=_position(raw)

    missing=_missing_components(params.get("missing_components"))
    missing_set=set(missing)
    stone_per_furnace=_positive_int(params,"stone_per_furnace")
    wood_per_chest=_positive_int(params,"wood_per_chest")
    iron_plate_per_drill=_positive_int(params,"iron_plate_per_drill")
    coal_required=_positive_int(params,"coal_bootstrap_required")
    seed_coal_required=_positive_int(params,"coal_seed_recovery_required")
    if seed_coal_required<3:
        raise ValueError("baseline recovery seed coal must cover both miners")
    iron_smelt_seconds=_positive_int(params,"iron_smelt_seconds")
    recovery_window=_positive_int(params,"recovery_window_seconds")
    copper_smelt_seconds=_positive_int(params,"copper_smelt_seconds")
    settle_seconds=_positive_int(params,"settle_seconds")
    coal_stock_target=_positive_int(params,"coal_stock_target")
    science_buffer_min=_positive_int(params,"science_buffer_min")

    missing_drills=sum(
        1 for name in ("coal_extractor","iron_extractor")
        if name in missing_set
    )
    missing_chests=sum(
        1 for name in ("coal_buffer","coal_quarantine","iron_buffer")
        if name in missing_set
    )
    missing_standalone_furnaces=1 if "copper_furnace" in missing_set else 0
    iron_furnace_stone=stone_per_furnace if "iron_furnace" in missing_set else 0
    drill_furnace_stone=stone_per_furnace*missing_drills
    copper_furnace_stone=stone_per_furnace*missing_standalone_furnaces
    wood_required=wood_per_chest*missing_chests
    iron_required=iron_plate_per_drill*missing_drills
    smelt_ore_required=max(iron_required,2)
    gear_required=3*missing_drills

    lines=[
        f"cortex_copper_buffer=get_entity({_prototype('wooden-chest')},{parsed['copper_buffer']})",
        f"cortex_boiler=get_entity({_prototype('boiler')},{parsed['boiler']})",
        f"cortex_copper_extractor=get_entity({_prototype('burner-mining-drill')},{parsed['copper_extractor']})",
        f"cortex_recovery_missing_components={missing!r}",
    ]

    lines.extend([
        "cortex_recovery_stone_harvested=0",
        "cortex_recovery_reserved_furnaces=0",
    ])

    if "iron_furnace" in missing_set:
        lines.extend([
            f"cortex_fast_reposition({parsed['stone']})",
            (
                "cortex_recovery_iron_furnace_stone_before=inspect_inventory()"
                f"[{_prototype('stone')}]"
            ),
            "cortex_recovery_iron_furnace_stone_harvested=cortex_mine_exact_resource(",
            f"    {parsed['stone']},'stone',quantity={iron_furnace_stone},radius=3",
            ")",
            (
                "cortex_recovery_iron_furnace_stone_after=inspect_inventory()"
                f"[{_prototype('stone')}]"
            ),
            (
                "if cortex_recovery_iron_furnace_stone_after-"
                "cortex_recovery_iron_furnace_stone_before"
                f" < {iron_furnace_stone}:"
            ),
            "    raise RuntimeError('baseline recovery iron-furnace stone incomplete')",
            (
                "cortex_recovery_iron_furnace_ready="
                "cortex_craft_exact_item('stone-furnace',quantity=1)"
            ),
            "if cortex_recovery_iron_furnace_ready < 1:",
            "    raise RuntimeError('baseline recovery iron furnace crafting incomplete')",
            "cortex_place_exact_entity(",
            f"    {parsed['iron_furnace']},'stone-furnace'",
            ")",
            (
                "cortex_recovery_stone_harvested="
                "cortex_recovery_stone_harvested+"
                "cortex_recovery_iron_furnace_stone_harvested"
            ),
        ])
    lines.append(
        f"cortex_iron_furnace=get_entity({_prototype('stone-furnace')},{parsed['iron_furnace']})"
    )

    lines.extend([
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
    ])

    lines.extend([
        f"cortex_fast_reposition({parsed['iron_resource']})",
        (
            "cortex_recovery_iron_ore_before=inspect_inventory()"
            f"[{_prototype('iron-ore')}]"
        ),
        "cortex_recovery_iron_ore_harvested=cortex_mine_exact_resource(",
        f"    {parsed['iron_resource']},'iron-ore',quantity={smelt_ore_required},radius=3",
        ")",
        (
            "cortex_recovery_iron_ore_after=inspect_inventory()"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "if cortex_recovery_iron_ore_after-cortex_recovery_iron_ore_before"
            f" < {smelt_ore_required}:"
        ),
        "    raise RuntimeError('baseline recovery iron-ore inventory incomplete')",
        "cortex_recovery_iron_fuel_deposit=cortex_deposit_exact_item(",
        f"    {parsed['iron_furnace']},'stone-furnace','coal',quantity=2",
        ")",
        "cortex_recovery_iron_ore_deposit=cortex_deposit_exact_item(",
        f"    {parsed['iron_furnace']},'stone-furnace','iron-ore',quantity={smelt_ore_required}",
        ")",
        f"sleep({iron_smelt_seconds})",
        (
            "cortex_recovery_iron_smelted=cortex_inspect_exact_item("
            f"{parsed['iron_furnace']},'stone-furnace','iron-plate')"
        ),
        f"if cortex_recovery_iron_smelted < {smelt_ore_required}:",
        "    raise RuntimeError('baseline recovery iron smelting incomplete')",
        (
            "cortex_iron_smelting_restored="
            f"cortex_recovery_iron_smelted>={smelt_ore_required}"
        ),
    ])

    if iron_required>0:
        lines.extend([
            "cortex_recovery_iron_plates_ready=cortex_transfer_exact_item(",
            f"    {parsed['iron_furnace']},'stone-furnace','iron-plate',",
            f"    quantity={iron_required}",
            ")",
            f"if cortex_recovery_iron_plates_ready < {iron_required}:",
            "    raise RuntimeError('baseline recovery construction iron incomplete')",
            (
                "cortex_recovery_gears_ready="
                f"cortex_craft_exact_item('iron-gear-wheel',quantity={gear_required})"
            ),
            f"if cortex_recovery_gears_ready < {gear_required}:",
            "    raise RuntimeError('baseline recovery gear crafting incomplete')",
            f"cortex_fast_reposition({parsed['stone']})",
            (
                "cortex_recovery_drill_stone_before=inspect_inventory()"
                f"[{_prototype('stone')}]"
            ),
            "cortex_recovery_drill_stone_harvested=cortex_mine_exact_resource(",
            f"    {parsed['stone']},'stone',quantity={drill_furnace_stone},radius=3",
            ")",
            (
                "cortex_recovery_drill_stone_after=inspect_inventory()"
                f"[{_prototype('stone')}]"
            ),
            (
                "if cortex_recovery_drill_stone_after-cortex_recovery_drill_stone_before"
                f" < {drill_furnace_stone}:"
            ),
            "    raise RuntimeError('baseline recovery drill stone inventory incomplete')",
            (
                "cortex_recovery_drill_furnaces_ready="
                f"cortex_craft_exact_item('stone-furnace',quantity={missing_drills})"
            ),
            f"if cortex_recovery_drill_furnaces_ready < {missing_drills}:",
            "    raise RuntimeError('baseline recovery drill furnace crafting incomplete')",
            (
                "cortex_recovery_drill_furnaces_before_drills=inspect_inventory()"
                f"[{_prototype('stone-furnace')}]"
            ),
            f"if cortex_recovery_drill_furnaces_before_drills < {missing_drills}:",
            "    raise RuntimeError('baseline recovery drill furnaces vanished before drill craft')",
            (
                "cortex_recovery_drills_ready="
                f"cortex_craft_exact_item('burner-mining-drill',quantity={missing_drills})"
            ),
            (
                "cortex_recovery_stone_harvested="
                "cortex_recovery_stone_harvested+cortex_recovery_drill_stone_harvested"
            ),
            f"if cortex_recovery_drills_ready < {missing_drills}:",
            "    raise RuntimeError('baseline recovery drill crafting incomplete')",
        ])
    else:
        lines.extend([
            "cortex_recovery_iron_plates_ready=0",
            "cortex_recovery_gears_ready=0",
            "cortex_recovery_drills_ready=0",
        ])

    if wood_required>0:
        lines.extend([
            f"cortex_fast_reposition({parsed['wood']})",
            (
                "cortex_recovery_wood_before=inspect_inventory()"
                f"[{_prototype('wood')}]"
            ),
            "cortex_recovery_wood_harvested=cortex_mine_exact_resource(",
            f"    {parsed['wood']},'wood',quantity={wood_required},radius=3",
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
                "cortex_recovery_chests_ready="
                f"cortex_craft_exact_item('wooden-chest',quantity={missing_chests})"
            ),
            f"if cortex_recovery_chests_ready < {missing_chests}:",
            "    raise RuntimeError('baseline recovery chest crafting incomplete')",
        ])
    else:
        lines.extend([
            "cortex_recovery_wood_harvested=0",
            "cortex_recovery_chests_ready=0",
        ])

    # Restore and validate copper smelting before exposing the reconstructed
    # coal/iron cells to the hostile live WORLD.
    if "copper_furnace" in missing_set:
        lines.extend([
            f"cortex_fast_reposition({parsed['stone']})",
            (
                "cortex_recovery_copper_stone_before=inspect_inventory()"
                f"[{_prototype('stone')}]"
            ),
            "cortex_recovery_copper_stone_harvested=cortex_mine_exact_resource(",
            f"    {parsed['stone']},'stone',quantity={copper_furnace_stone},radius=3",
            ")",
            (
                "cortex_recovery_copper_stone_after=inspect_inventory()"
                f"[{_prototype('stone')}]"
            ),
            (
                "if cortex_recovery_copper_stone_after-cortex_recovery_copper_stone_before"
                f" < {copper_furnace_stone}:"
            ),
            "    raise RuntimeError('baseline recovery copper stone inventory incomplete')",
            (
                "cortex_recovery_copper_furnace_ready="
                "cortex_craft_exact_item('stone-furnace',quantity=1)"
            ),
            "if cortex_recovery_copper_furnace_ready < 1:",
            "    raise RuntimeError('baseline recovery copper furnace crafting incomplete')",
            "cortex_place_exact_entity(",
            f"    {parsed['copper_furnace']},'stone-furnace'",
            ")",
            (
                "cortex_recovery_stone_harvested="
                "cortex_recovery_stone_harvested+cortex_recovery_copper_stone_harvested"
            ),
        ])
    lines.append(
        f"cortex_copper_furnace=get_entity({_prototype('stone-furnace')},{parsed['copper_furnace']})"
    )
    lines.extend([
        f"cortex_fast_reposition({parsed['coal_resource']})",
        "cortex_recovery_copper_coal_harvested=cortex_mine_exact_resource(",
        f"    {parsed['coal_resource']},'coal',quantity=1,radius=3",
        ")",
        "cortex_recovery_copper_ore=cortex_transfer_exact_item(",
        f"    {parsed['copper_buffer']},'wooden-chest','copper-ore',quantity=2",
        ")",
        "cortex_recovery_copper_coal_deposit=cortex_deposit_exact_item(",
        f"    {parsed['copper_furnace']},'stone-furnace','coal',quantity=1",
        ")",
        "cortex_recovery_copper_ore_deposit=cortex_deposit_exact_item(",
        f"    {parsed['copper_furnace']},'stone-furnace','copper-ore',quantity=2",
        ")",
        f"sleep({copper_smelt_seconds})",
        (
            "cortex_recovery_copper_plate_count=cortex_inspect_exact_item("
            f"{parsed['copper_furnace']},'stone-furnace','copper-plate')"
        ),
        "if cortex_recovery_copper_plate_count<=0:",
        "    raise RuntimeError('baseline recovery copper smelting did not resume')",
    ])

    # Harvest all seed material before exposing the reconstructed coal/iron
    # cells to the hostile live WORLD. No mining or repositioning occurs after
    # placement and before the first physical gate.
    lines.extend([
        f"cortex_fast_reposition({parsed['coal_resource']})",
        (
            "cortex_recovery_seed_coal_before=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        "cortex_recovery_seed_coal_harvested=cortex_mine_exact_resource(",
        f"    {parsed['coal_resource']},'coal',quantity={seed_coal_required},radius=3",
        ")",
        (
            "cortex_recovery_seed_coal_after=inspect_inventory()"
            f"[{_prototype('coal')}]"
        ),
        (
            "if cortex_recovery_seed_coal_after-cortex_recovery_seed_coal_before"
            f" < {seed_coal_required}:"
        ),
        "    raise RuntimeError('baseline recovery seed coal inventory incomplete')",
        f"cortex_fast_reposition({parsed['iron_resource']})",
        (
            "cortex_recovery_iron_buffer_seed_before=inspect_inventory()"
            f"[{_prototype('iron-ore')}]"
        ),
        "cortex_recovery_iron_buffer_seed_harvested=cortex_mine_exact_resource(",
        f"    {parsed['iron_resource']},'iron-ore',quantity=5,radius=3",
        ")",
        (
            "cortex_recovery_iron_buffer_seed_after=inspect_inventory()"
            f"[{_prototype('iron-ore')}]"
        ),
        (
            "if cortex_recovery_iron_buffer_seed_after-"
            "cortex_recovery_iron_buffer_seed_before < 5:"
        ),
        "    raise RuntimeError('baseline recovery iron buffer seed incomplete')",
    ])

    # Place buffers before miners, and create each missing vulnerable entity
    # with its initial stock atomically. The coal extractor is placed last so
    # the accepted drill-to-chest cell becomes live only when its destination
    # already exists.
    if "iron_buffer" in missing_set:
        lines.extend([
            "cortex_iron_buffer=cortex_place_exact_entity(",
            f"    {parsed['iron_buffer']},'wooden-chest',",
            "    initial_items={'iron-ore':5}",
            ")",
            "cortex_recovery_iron_buffer_seeded=5",
        ])
    else:
        lines.extend([
            f"cortex_iron_buffer=get_entity({_prototype('wooden-chest')},{parsed['iron_buffer']})",
            "cortex_recovery_iron_buffer_seeded=cortex_deposit_exact_item(",
            f"    {parsed['iron_buffer']},'wooden-chest','iron-ore',quantity=5",
            ")",
        ])

    if "coal_quarantine" in missing_set:
        lines.extend([
            "cortex_coal_quarantine=cortex_place_exact_entity(",
            f"    {parsed['coal_quarantine']},'wooden-chest'",
            ")",
        ])
    else:
        lines.append(
            f"cortex_coal_quarantine=get_entity({_prototype('wooden-chest')},{parsed['coal_quarantine']})"
        )

    if "coal_buffer" in missing_set:
        lines.extend([
            "cortex_coal_buffer=cortex_place_exact_entity(",
            f"    {parsed['coal_buffer']},'wooden-chest',",
            f"    initial_items={{'coal':{coal_stock_target}}}",
            ")",
            f"cortex_recovery_endogenous_coal_buffered={coal_stock_target}",
        ])
    else:
        lines.extend([
            (
                "cortex_recovery_existing_coal_stock=cortex_inspect_exact_item("
                f"{parsed['coal_buffer']},'wooden-chest','coal')"
            ),
            (
                f"cortex_recovery_coal_shortfall=max(0,{coal_stock_target}-"
                "cortex_recovery_existing_coal_stock)"
            ),
            "if cortex_recovery_coal_shortfall>0:",
            "    cortex_recovery_endogenous_coal_buffered=cortex_deposit_exact_item(",
            f"        {parsed['coal_buffer']},'wooden-chest','coal',",
            "        quantity=cortex_recovery_coal_shortfall",
            "    )",
            "else:",
            "    cortex_recovery_endogenous_coal_buffered=0",
            f"cortex_coal_buffer=get_entity({_prototype('wooden-chest')},{parsed['coal_buffer']})",
        ])

    if "iron_extractor" in missing_set:
        lines.extend([
            "cortex_iron_extractor=cortex_place_exact_entity(",
            f"    {parsed['iron_extractor']},'burner-mining-drill',direction='south',",
            "    initial_items={'coal':1}",
            ")",
            "cortex_recovery_iron_seed=1",
        ])
    else:
        lines.extend([
            f"cortex_iron_extractor=get_entity({_prototype('burner-mining-drill')},{parsed['iron_extractor']})",
            "cortex_recovery_iron_seed=cortex_deposit_exact_item(",
            f"    {parsed['iron_extractor']},'burner-mining-drill','coal',quantity=1",
            ")",
        ])

    if "coal_extractor" in missing_set:
        lines.extend([
            "cortex_coal_extractor=cortex_place_exact_entity(",
            f"    {parsed['coal_extractor']},'burner-mining-drill',direction='south',",
            "    initial_items={'coal':2}",
            ")",
            "cortex_recovery_coal_seed=2",
        ])
    else:
        lines.extend([
            f"cortex_coal_extractor=get_entity({_prototype('burner-mining-drill')},{parsed['coal_extractor']})",
            "cortex_recovery_coal_seed=cortex_deposit_exact_item(",
            f"    {parsed['coal_extractor']},'burner-mining-drill','coal',quantity=2",
            ")",
        ])

    lines.extend([
        f"sleep({recovery_window})",
        (
            "cortex_recovery_coal_stock=cortex_inspect_exact_item("
            f"{parsed['coal_buffer']},'wooden-chest','coal')"
        ),
        f"if cortex_recovery_coal_stock < {coal_stock_target}:",
        "    raise RuntimeError('baseline recovery coal stock target not reached')",
        (
            "cortex_recovery_iron_stock=cortex_inspect_exact_item("
            f"{parsed['iron_buffer']},'wooden-chest','iron-ore')"
        ),
        "if cortex_recovery_iron_stock < 5:",
        "    raise RuntimeError('baseline recovery iron extraction did not resume')",
        (
            "cortex_recovery_quarantine_stock=cortex_inspect_exact_item("
            f"{parsed['coal_quarantine']},'wooden-chest','coal')"
        ),
        "if cortex_recovery_quarantine_stock != 0:",
        "    raise RuntimeError('baseline recovery quarantine must remain empty')",
    ])

    lines.extend([
        (
            "cortex_recovery_coal_stock_final=cortex_inspect_exact_item("
            f"{parsed['coal_buffer']},'wooden-chest','coal')"
        ),
        (
            "cortex_recovery_iron_stock_final=cortex_inspect_exact_item("
            f"{parsed['iron_buffer']},'wooden-chest','iron-ore')"
        ),
        (
            "cortex_recovery_science_buffer=cortex_inspect_exact_item("
            f"{parsed['copper_buffer']},'wooden-chest','automation-science-pack')"
        ),
        f"sleep({settle_seconds})",
        "cortex_promoted_baseline_restored=(",
        "    cortex_coal_extractor is not None",
        "    and cortex_coal_buffer is not None",
        "    and cortex_coal_quarantine is not None",
        "    and cortex_iron_extractor is not None",
        "    and cortex_iron_buffer is not None",
        "    and cortex_copper_furnace is not None",
        ")",
        "cortex_iron_extraction_restored=cortex_recovery_iron_stock_final>=5",
        (
            "cortex_coal_stock_recovered="
            f"cortex_recovery_coal_stock_final>={max(6,coal_stock_target-1)}"
        ),
        "cortex_copper_smelting_restored=cortex_recovery_copper_plate_count>0",
        (
            "cortex_science_buffer_intact="
            f"cortex_recovery_science_buffer>={science_buffer_min}"
        ),
        "cortex_promoted_entity_restored=cortex_promoted_baseline_restored",
        "print({",
        "    'promoted_baseline_restored':cortex_promoted_baseline_restored,",
        "    'iron_extraction_restored':cortex_iron_extraction_restored,",
        "    'iron_smelting_restored':cortex_iron_smelting_restored,",
        "    'coal_stock_recovered':cortex_coal_stock_recovered,",
        "    'copper_smelting_restored':cortex_copper_smelting_restored,",
        "    'science_buffer_intact':cortex_science_buffer_intact,",
        "    'recovery_missing_components':cortex_recovery_missing_components,",
        "    'recovery_stone_harvested':cortex_recovery_stone_harvested,",
        "    'recovery_coal_harvested':cortex_recovery_coal_harvested,",
        "    'recovery_seed_coal_harvested':cortex_recovery_seed_coal_harvested,",
        "    'recovery_iron_ore_harvested':cortex_recovery_iron_ore_harvested,",
        "    'recovery_wood_harvested':cortex_recovery_wood_harvested,",
        "    'recovery_iron_plates_ready':cortex_recovery_iron_plates_ready,",
        "    'recovery_coal_stock_final':cortex_recovery_coal_stock_final,",
        "    'recovery_iron_stock_final':cortex_recovery_iron_stock_final,",
        "    'recovery_copper_plate_count':cortex_recovery_copper_plate_count,",
        "    'recovery_science_buffer':cortex_recovery_science_buffer,",
        "})",
    ])
    return lines
