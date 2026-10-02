from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
from typing import Any

import pytest

ROOT=pathlib.Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"run_cortex_f5c_replay_promoted_baseline.py"


def _module() -> Any:
    scripts=str(ROOT/"scripts")
    if scripts not in sys.path:
        sys.path.insert(0,scripts)
    spec=importlib.util.spec_from_file_location(
        "run_cortex_f5c_replay_promoted_baseline",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module


REPLAY=_module()


POSITIONS={
    "iron_extractor":(15.0,70.0),
    "iron_buffer":(15.5,71.5),
    "iron_furnace":(20.0,69.0),
    "coal_extractor":(15.0,-4.0),
    "coal_buffer":(15.5,-2.5),
    "coal_quarantine":(15.5,-5.5),
    "boiler":(-4.0,3.5),
    "steam_engine":(2.5,9.5),
    "copper_extractor":(-71.0,70.0),
    "copper_buffer":(-70.5,71.5),
    "copper_furnace":(-63.0,69.0),
}


def _row(name: str,key: str,contents: list[dict[str,Any]] | None=None) -> dict[str,Any]:
    x,y=POSITIONS[key]
    row={"name":name,"position":{"x":x,"y":y}}
    if contents is not None:
        row["contents"]=contents
    return row


def _prefix_snapshot(prefix: int) -> dict[str,Any]:
    rows=[]
    if prefix>=1:
        rows += [
            _row("burner-mining-drill","iron_extractor"),
            _row("wooden-chest","iron_buffer"),
        ]
    if prefix>=2:
        rows += [
            _row("burner-mining-drill","coal_extractor"),
            _row("wooden-chest","coal_buffer"),
            _row("wooden-chest","coal_quarantine"),
        ]
    if prefix>=3:
        rows += [_row("stone-furnace","iron_furnace")]
    if prefix>=4:
        rows += [
            _row("boiler","boiler"),
            _row("steam-engine","steam_engine"),
        ]
    if prefix>=5:
        rows += [
            _row("burner-mining-drill","copper_extractor"),
            _row("wooden-chest","copper_buffer",[]),
            _row("stone-furnace","copper_furnace"),
        ]
    if prefix>=6:
        for row in rows:
            if row["name"]=="wooden-chest" and row["position"]=={
                "x":POSITIONS["copper_buffer"][0],
                "y":POSITIONS["copper_buffer"][1],
            }:
                row["contents"]=[{"name":"automation-science-pack","count":10}]
    return {"entities":rows}


@pytest.mark.parametrize("prefix",range(7))
def test_detect_replay_prefix_is_exact(prefix: int) -> None:
    assert REPLAY._detect_replay_prefix(
        _prefix_snapshot(prefix),
        POSITIONS,
    )==prefix


def test_detect_replay_prefix_refuses_hole() -> None:
    snapshot={
        "entities":[
            _row("burner-mining-drill","coal_extractor"),
            _row("wooden-chest","coal_buffer"),
            _row("wooden-chest","coal_quarantine"),
        ]
    }
    with pytest.raises(RuntimeError,match="non-contiguous"):
        REPLAY._detect_replay_prefix(snapshot,POSITIONS)


def test_replay_source_has_no_phase_promotion_or_world_reset() -> None:
    source=SCRIPT.read_text(encoding="utf-8")
    ast.parse(source)
    assert '"promotion_credit":False' in source
    assert '"phase_state_mutation":False' in source
    assert '"world_reset":False' in source
    assert "phase5_protocol" not in source or "achieved_capabilities" in source
    assert "cortex_phase_state.py" not in source
    assert "executor.reset(" not in source
    assert "F5BoundedAuthorityBridge" in source
    assert "issue_a2_grant" in source
    assert "max_executions" not in source
    assert "/server-save" in source


def test_replay_order_and_original_measure_probes_are_fixed() -> None:
    assert REPLAY.CAPABILITY_ORDER==(
        "iron_extraction",
        "coal_self_sufficiency",
        "iron_smelting",
        "steam_power",
        "copper_chain",
        "automation_science",
    )
    assert set(REPLAY.MEASURE)==set(REPLAY.CAPABILITY_ORDER)
    assert all(callable(REPLAY.MEASURE[name]) for name in REPLAY.CAPABILITY_ORDER)
