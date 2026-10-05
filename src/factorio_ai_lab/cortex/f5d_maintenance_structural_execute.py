"""Compiler for one bounded F5-D endogenous refuel repair."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def compile_autonomous_refuel(operation: StructuralOperation) -> list[str]:
    params=operation.parameters
    source=params.get("source")
    targets=params.get("targets")
    dose=params.get("dose")
    reserve=params.get("source_reserve")
    if not isinstance(source,Mapping):
        raise TypeError("autonomous refuel requires frozen source")
    if not isinstance(targets,Sequence) or isinstance(targets,(str,bytes)) or not targets:
        raise TypeError("autonomous refuel requires frozen targets")
    if not isinstance(dose,int) or isinstance(dose,bool) or dose<=0:
        raise ValueError("autonomous refuel dose must be positive")
    if not isinstance(reserve,int) or isinstance(reserve,bool) or reserve<0:
        raise ValueError("autonomous refuel reserve must be non-negative")

    source_entity=str(source.get("entity_name") or "")
    if source_entity!="wooden-chest":
        raise ValueError("autonomous refuel source must be a wooden-chest")
    source_position=_position(source)

    frozen_targets: list[tuple[str,str]]= []
    for raw in targets:
        if not isinstance(raw,Mapping):
            raise TypeError("autonomous refuel target must be a mapping")
        entity_name=str(raw.get("entity_name") or "")
        if entity_name not in {"burner-mining-drill","stone-furnace","boiler"}:
            raise ValueError(f"unsupported autonomous refuel target {entity_name!r}")
        frozen_targets.append((entity_name,_position(raw)))

    lines=[
        f"cortex_refuel_source=get_entity({_prototype(source_entity)},{source_position})",
        (
            "cortex_refuel_source_before=inspect_inventory(cortex_refuel_source)"
            f"[{_prototype('coal')}]"
        ),
        f"cortex_refuel_required={dose*len(frozen_targets)}",
        (
            f"if cortex_refuel_source_before < cortex_refuel_required+{reserve}: "
            "raise RuntimeError('endogenous coal reserve below autonomous refuel budget')"
        ),
        f"move_to({source_position})",
        "cortex_refuel_drawn=extract_item(",
        f"    {_prototype('coal')},",
        "    cortex_refuel_source,",
        "    quantity=cortex_refuel_required,",
        ")",
        "cortex_refuel_inserted=0",
    ]
    for index,(entity_name,position) in enumerate(frozen_targets):
        lines.extend([
            f"move_to({position})",
            f"cortex_refuel_target_{index}=get_entity({_prototype(entity_name)},{position})",
            f"cortex_refuel_target_{index}=insert_item(",
            f"    {_prototype('coal')},",
            f"    cortex_refuel_target_{index},",
            f"    quantity={dose},",
            ")",
            f"cortex_refuel_inserted+={dose}",
        ])
    lines.extend([
        (
            "cortex_refuel_source_after=inspect_inventory(cortex_refuel_source)"
            f"[{_prototype('coal')}]"
        ),
        (
            "cortex_autonomous_refuel_succeeded=("
            "cortex_refuel_drawn==cortex_refuel_required "
            "and cortex_refuel_inserted==cortex_refuel_required)"
        ),
        (
            "print({'cortex_refuel_drawn':cortex_refuel_drawn,"
            "'cortex_refuel_inserted':cortex_refuel_inserted,"
            "'cortex_refuel_source_before':cortex_refuel_source_before,"
            "'cortex_refuel_source_after':cortex_refuel_source_after,"
            "'cortex_autonomous_refuel_succeeded':"
            "cortex_autonomous_refuel_succeeded})"
        ),
    ])
    return lines
