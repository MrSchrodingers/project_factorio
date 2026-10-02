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
    assert "_ensure_factorio_unpaused(instance)" in source
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


class _FakeGameControl:
    def __init__(self) -> None:
        self._is_paused=True


def test_ensure_factorio_unpaused_verifies_remote_state() -> None:
    instance=_FakeInstance(["false"])
    instance.game_control=_FakeGameControl()
    result=REPLAY._ensure_factorio_unpaused(instance)
    assert result=={"status":"unpaused","verified":True}
    assert instance.game_control._is_paused is False
    assert "game.tick_paused = false" in instance.rcon_client.commands[0]


def test_bootstrap_material_signature_ignores_positions_and_paths() -> None:
    a=[
        {
            "resource":"stone",
            "quantity":30,
            "position":{"x":1.0,"y":2.0},
            "validated_path_waypoints":10,
        },
        {
            "resource":"wood",
            "quantity":10,
            "radius":24,
            "position":{"x":3.0,"y":4.0},
            "validated_path_waypoints":20,
        },
    ]
    b=[
        {
            "resource":"stone",
            "quantity":30,
            "position":{"x":100.0,"y":200.0},
            "validated_path_waypoints":99,
        },
        {
            "resource":"wood",
            "quantity":10,
            "radius":24,
            "position":{"x":300.0,"y":400.0},
            "validated_path_waypoints":199,
        },
    ]
    assert REPLAY._bootstrap_material_signature(a)==REPLAY._bootstrap_material_signature(b)


def test_bootstrap_material_signature_detects_material_change() -> None:
    baseline=[{"resource":"coal","quantity":6}]
    changed=[{"resource":"coal","quantity":7}]
    assert (
        REPLAY._bootstrap_material_signature(baseline)
        !=REPLAY._bootstrap_material_signature(changed)
    )


def test_replay_source_rebases_coal_before_a2_grant() -> None:
    source=SCRIPT.read_text(encoding="utf-8")
    rebase_at=source.index("_rebase_coal_bootstrap_plan(")
    grant_at=source.rindex("bridge.issue_a2_grant(")
    assert rebase_at<grant_at
    assert "bootstrap_rebased_from_live_world" in source
    assert "external_resource_injection" in source


def _steam_plan_for_reserve_tests() -> Any:
    prepared=REPLAY.PreparedStructuralAction(
        action_id="steam:action",
        family=REPLAY.ActionFamily.PLACEMENT,
        intent="steam",
        binding="cortex.structural.steam_power",
        purpose="infrastructure",
        contract_version="cortex_structural_ops_v7",
        operations=(
            REPLAY.StructuralOperation(
                op="establish_steam_power",
                parameters={
                    "positions":{
                        "iron_extractor":{"x":15.0,"y":70.0},
                        "iron_buffer":{"x":15.5,"y":71.5},
                        "coal_extractor":{"x":15.0,"y":-4.0},
                        "coal_buffer":{"x":15.5,"y":-2.5},
                    },
                    "iron_trigger_ore_draw":56,
                    "initial_coal_draw":16,
                },
            ),
            REPLAY.StructuralOperation(
                op="verify_postconditions",
                parameters={
                    "conditions":[{
                        "name":"steam_generated",
                        "operator":"equals",
                        "state":"unknown",
                        "expected":True,
                        "hard":True,
                        "evidence":[],
                    }],
                },
            ),
        ),
        measurement_keys=("steam_generated",),
        preflight={},
    )
    return REPLAY.ReplayOptionPlan(
        request=REPLAY.OptionRequest(
            option_id="steam",
            kind=REPLAY.OptionKind.ESTABLISH_STEAM_POWER,
            goal="steam",
            provenance=REPLAY.ActionProvenance(
                requested_by="test",
                source_component="test",
                code_revision="abc",
                run_id="run",
            ),
            budget=REPLAY.OptionBudget(requested_ticks=21600),
            authority=REPLAY.ActionAuthority.SHADOW,
        ),
        action_request=REPLAY.ActionRequest(
            action_id="steam:action",
            family=REPLAY.ActionFamily.PLACEMENT,
            intent="steam",
            provenance=REPLAY.ActionProvenance(
                requested_by="test",
                source_component="test",
                code_revision="abc",
                run_id="run",
            ),
        ),
        prepared=prepared,
        termination_conditions=(),
        capability="steam_power",
        source_artifact="/tmp/steam.json",
    )


def test_reserve_recovery_plan_preserves_promoted_steam_thresholds() -> None:
    steam=_steam_plan_for_reserve_tests()
    plan=REPLAY._build_reserve_recovery_plan(
        steam_plan=steam,
        commit="abc",
        run_id="run",
    )
    assert plan.request.kind is REPLAY.OptionKind.RESTORE_PROMOTED_RESERVES
    assert plan.request.budget.requested_seconds==REPLAY.RESERVE_RECOVERY_OPTION_SECONDS
    operation=plan.prepared.operations[0]
    assert operation.op=="recover_promoted_reserves"
    assert operation.parameters["iron_target"]==56
    assert operation.parameters["coal_floor"]==16
    assert operation.parameters["iron_refuel_coal"]==3
    assert operation.parameters["coal_refuel_coal"]==1
    assert plan.prepared.preflight["external_resource_injection"] is False
    assert plan.prepared.preflight["promotion_credit"] is False


def test_observe_promoted_reserves_requires_both_original_thresholds() -> None:
    steam=_steam_plan_for_reserve_tests()
    spec=REPLAY._steam_reserve_spec(steam)
    snapshot={
        "entities":[
            {
                "name":"wooden-chest",
                "position":{"x":15.5,"y":71.5},
                "contents":[{"name":"iron-ore","count":56}],
            },
            {
                "name":"wooden-chest",
                "position":{"x":15.5,"y":-2.5},
                "contents":[{"name":"coal","count":15}],
            },
        ],
    }
    observed=REPLAY._observe_promoted_reserves(snapshot,spec)
    assert observed["iron_ready"] is True
    assert observed["coal_ready"] is False
    assert observed["ready"] is False


def test_replay_source_saves_reserve_recovery_before_steam_a2() -> None:
    source=SCRIPT.read_text(encoding="utf-8")
    assert "cortex-f5c-replay-03b-steam-reserves" in source
    assert "steam_power_reserve_recovery" in source
    assert "physical_after_reattach" in source
    assert "OptionKind.RESTORE_PROMOTED_RESERVES" in source
