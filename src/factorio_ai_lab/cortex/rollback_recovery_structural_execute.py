"""Compiler for exact F5 rollback recovery of the promoted copper furnace."""

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
    stone=positions.get("stone")
    furnace=positions.get("copper_furnace")
    if not isinstance(stone,Mapping) or not isinstance(furnace,Mapping):
        raise TypeError("rollback recovery requires stone and copper_furnace positions")
    stone_pos=_position(stone)
    furnace_pos=_position(furnace)
    stone_required=_positive_int(params,"stone_required")
    settle=_positive_int(params,"settle_seconds")
    if stone_required!=5:
        raise ValueError("stone furnace recovery must use exactly five endogenous stone")

    return [
        f"cortex_fast_reposition({stone_pos})",
        (
            "cortex_recovery_stone_before=inspect_inventory()"
            f"[{_prototype('stone')}]"
        ),
        "cortex_recovery_stone_harvested=harvest_resource(",
        f"    {stone_pos},quantity={stone_required},radius=3",
        ")",
        (
            "cortex_recovery_stone_after_primary=inspect_inventory()"
            f"[{_prototype('stone')}]"
        ),
        (
            f"cortex_recovery_stone_missing=max(0,{stone_required}-"
            "(cortex_recovery_stone_after_primary-cortex_recovery_stone_before))"
        ),
        "cortex_recovery_stone_fallback_harvested=0",
        "if cortex_recovery_stone_missing>0:",
        "    cortex_recovery_stone_nearest=nearest(Resource.Stone)",
        "    cortex_fast_reposition(cortex_recovery_stone_nearest)",
        "    cortex_recovery_stone_fallback_harvested=harvest_resource(",
        "        cortex_recovery_stone_nearest,",
        "        quantity=cortex_recovery_stone_missing,",
        "        radius=3,",
        "    )",
        (
            "cortex_recovery_stone_after=inspect_inventory()"
            f"[{_prototype('stone')}]"
        ),
        (
            "cortex_recovery_stone_inventory_growth="
            "max(0,cortex_recovery_stone_after-cortex_recovery_stone_before)"
        ),
        f"if cortex_recovery_stone_inventory_growth < {stone_required}:",
        "    raise RuntimeError('rollback recovery stone did not reach inventory')",
        f"craft_item({_prototype('stone-furnace')},quantity=1)",
        (
            "cortex_recovery_furnace_inventory=inspect_inventory()"
            f"[{_prototype('stone-furnace')}]"
        ),
        "if cortex_recovery_furnace_inventory < 1:",
        "    raise RuntimeError('rollback recovery furnace crafting incomplete')",
        f"cortex_fast_reposition({furnace_pos})",
        "cortex_recovered_furnace=place_entity(",
        f"    {_prototype('stone-furnace')},position={furnace_pos}",
        ")",
        f"sleep({settle})",
        "cortex_recovered_furnace=get_entity(",
        f"    {_prototype('stone-furnace')},{furnace_pos}",
        ")",
        "cortex_promoted_entity_restored=(cortex_recovered_furnace is not None)",
    ]
