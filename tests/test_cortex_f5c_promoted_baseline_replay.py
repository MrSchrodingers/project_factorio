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


class _FakeRcon:
    def __init__(self,responses: list[str]) -> None:
        self.responses=list(responses)
        self.commands: list[str]=[]

    def send_command(self,command: str) -> str:
        self.commands.append(command)
        if not self.responses:
            raise AssertionError("unexpected RCON command")
        return self.responses.pop(0)


class _FakeInstance:
    def __init__(self,responses: list[str]) -> None:
        self.rcon_client=_FakeRcon(responses)


def test_save_world_quiesces_storage_without_rehydrating() -> None:
    instance=_FakeInstance([
        "true",
        '{"ok":true,"removed_functions":17}',
        "Saving the map",
    ])
    result=REPLAY._save_world(instance,"unit-save")
    assert result["response"]=="Saving the map"
    assert result["storage_quiesce"]["removed_functions"]==17
    assert "game.tick_paused = true" in instance.rcon_client.commands[0]
    assert instance.rcon_client.commands[1].startswith("/sc ")
    assert "scrub(storage)" in instance.rcon_client.commands[1]
    assert "storage.__lua_script_checksums={}" in instance.rcon_client.commands[1]
    assert instance.rcon_client.commands[2]=="/server-save unit-save"
    source=REPLAY._save_world.__code__.co_names
    assert "_rehydrate_fle_runtime" not in source
    assert "setup_tools" not in source


def test_save_world_refuses_quiesce_without_removed_functions() -> None:
    instance=_FakeInstance([
        "true",
        '{"ok":true,"removed_functions":0}',
    ])
    with pytest.raises(RuntimeError,match="removed no Lua functions"):
        REPLAY._save_world(instance,"unsafe-save")
    assert len(instance.rcon_client.commands)==2
    assert all("/server-save" not in command for command in instance.rcon_client.commands)


def test_autosave_interval_parsing_and_guard() -> None:
    client=_FakeRcon([
        "Autosave every 5 minutes.",
        "Autosave interval changed.",
        "Autosaving disabled.",
        "Autosave interval changed.",
        "Autosave every 5 minutes.",
    ])
    assert REPLAY._autosave_interval(client)==5
    suspended=REPLAY._set_autosave_interval(client,0)
    assert suspended["observed_minutes"]==0
    restored=REPLAY._set_autosave_interval(client,5)
    assert restored["observed_minutes"]==5
    assert client.commands==[
        "/config get autosave-interval",
        "/config set autosave-interval 0",
        "/config get autosave-interval",
        "/config set autosave-interval 5",
        "/config get autosave-interval",
    ]


def test_replay_source_uses_fresh_fle_environment_per_step() -> None:
    source=SCRIPT.read_text(encoding="utf-8")
    assert "_attach_replay_environment(" in source
    assert "env.close()" in source
    assert "_set_autosave_interval(control,0)" in source
    assert "bind_tick_accurate_sleep_tool(env)" in source
    assert "save_runtime_rehydrate" not in source
    assert "_rehydrate_fle_runtime" not in source

def test_prior_replay_progress_requires_persisted_prefix(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits=tmp_path/"audits"
    audits.mkdir()
    monkeypatch.setattr(REPLAY,"RUNS_DIR",tmp_path)
    failed=audits/"cortex_f5c_promoted_baseline_replay_1773655334_old.json"
    failed.write_text(
        __import__("json").dumps({
            "steps":[{
                "index":1,
                "capability":"iron_extraction",
                "status":"accepted",
                "replay_prefix_after":0,
                "world_entity_count_after":0,
            }],
            "technical_replay_completed":False,
        }),
        encoding="utf-8",
    )
    current=audits/"cortex_f5c_promoted_baseline_replay_1773655334_current.json"
    assert REPLAY._latest_prior_replay_progress(1773655334,current)==0


def test_prior_replay_progress_counts_only_contiguous_persisted_prefix(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits=tmp_path/"audits"
    audits.mkdir()
    monkeypatch.setattr(REPLAY,"RUNS_DIR",tmp_path)
    prior=audits/"cortex_f5c_promoted_baseline_replay_1773655334_old.json"
    prior.write_text(
        __import__("json").dumps({
            "steps":[
                {"index":1,"status":"accepted","replay_prefix_after":1},
                {"index":2,"status":"accepted","replay_prefix_after":2},
                {"index":3,"status":"accepted","replay_prefix_after":2},
            ],
        }),
        encoding="utf-8",
    )
    current=audits/"cortex_f5c_promoted_baseline_replay_1773655334_current.json"
    assert REPLAY._latest_prior_replay_progress(1773655334,current)==2
