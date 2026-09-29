from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
    ActionStatus,
)
from factorio_ai_lab.cortex.coal_self_sufficiency_option import (
    CoalSelfSufficiencyOptionPlan,
    compose_coal_self_sufficiency_option,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import compile_structural_action
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor
from factorio_ai_lab.planning.placement import ResourceSurvey

FIXED_NOW=datetime(2026,9,29,7,0,tzinfo=UTC)


def coal_plan() -> CoalSelfSufficiencyOptionPlan:
    option=OptionRequest(
        option_id="f5c-coal-self-sufficiency",
        kind=OptionKind.ESTABLISH_COAL_SELF_SUFFICIENCY,
        goal="establish endogenous coal while preserving iron extraction",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component=(
                "factorio_ai_lab.cortex.coal_self_sufficiency_option"
            ),
            code_revision="f5c-coal-test-sha",
            run_id="f5c-coal-test-run",
        ),
        budget=OptionBudget(requested_ticks=120*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-coal-self-sufficiency-action",
        family=ActionFamily.PLACEMENT,
        intent="establish endogenous coal and preserve incumbent iron",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component=(
                "factorio_ai_lab.cortex.coal_self_sufficiency_option"
            ),
            code_revision="f5c-coal-test-sha",
            run_id="f5c-coal-test-run",
        ),
        requires=("iron_extraction","observed_world_resources"),
        provides=("coal_self_sufficiency",),
    )
    tiles={
        (x,y):"coal"
        for x in range(30,38)
        for y in range(30,38)
    }
    result=compose_coal_self_sufficiency_option(
        option,
        action_request=action,
        world_entities=(
            {
                "name":"burner-mining-drill",
                "position":{"x":15.0,"y":70.0},
                "direction":4,
            },
            {
                "name":"wooden-chest",
                "position":{"x":15.5,"y":71.5},
            },
        ),
        resources=ResourceSurvey(
            tiles=tiles,
            surveyed=(0.0,0.0,100.0,100.0),
        ),
        incumbent_iron_extractor_position=(15.0,70.0),
        incumbent_iron_buffer_position=(15.5,71.5),
        bootstrap_resources=(
            {
                "resource":"stone",
                "quantity":30,
                "position":{"x":-46.5,"y":-0.5},
                "validated_path_waypoints":51,
            },
            {
                "resource":"coal",
                "quantity":6,
                "position":{"x":15.5,"y":-0.5},
                "validated_path_waypoints":64,
            },
            {
                "resource":"iron-ore",
                "quantity":16,
                "position":{"x":15.5,"y":70.5},
                "validated_path_waypoints":80,
            },
            {
                "resource":"wood",
                "quantity":10,
                "radius":24,
                "position":{"x":0.125,"y":-18.625},
                "validated_path_waypoints":92,
            },
        ),
        footprints={
            "burner-mining-drill":(2,2),
            "wooden-chest":(1,1),
        },
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_coal_option_is_inert_and_preserves_f5_authority_model() -> None:
    plan=coal_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_COAL_SELF_SUFFICIENCY
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v5"
    assert plan.prepared.binding=="cortex.structural.coal_self_sufficiency"
    assert plan.prepared.preflight["bootstrap_external_injection"] is False
    names={row.name for row in plan.termination_conditions}
    assert {
        "coal_mined",
        "endogenous_coal_reaches_fuel_consumer",
        "external_bootstrap_fuel_retired",
        "coal_extractor_exists",
        "coal_endogenous_growth",
        "incumbent_iron_survives",
        "incumbent_iron_buffer_growth",
    }.issubset(names)


def test_compiled_coal_transaction_separates_bootstrap_and_endogenous_windows() -> None:
    plan=coal_plan()
    compilation=compile_structural_action(plan.prepared,settle_seconds=120)

    assert compilation.ready
    assert compilation.compiled is not None
    code=compilation.compiled.code
    ast.parse(code)
    assert "cortex_bootstrap_quarantine_count" in code
    assert "cortex_seed_fuel_remaining" in code
    assert "cortex_incumbent_iron_bootstrap_fuel" in code
    assert "cortex_incumbent_iron_bootstrap_removed" in code
    assert "cortex_incumbent_iron_bootstrap_inventory_remaining" in code
    assert "cortex_iron_buffer_pre_endogenous" in code
    assert "cortex_endogenous_transfer" in code
    assert "cortex_coal_self_refuel" in code
    assert "cortex_iron_endogenous_refuel" in code
    assert "cortex_coal_endogenous_growth" in code
    assert "cortex_incumbent_iron_buffer_growth" in code
    assert "sleep(45)" in code
    assert "sleep(30)" in code
    assert "sleep(20)" in code
    assert code.count("harvest_resource(")==4
    assert code.count("cortex_fast_reposition(")==5
    assert "nearest(" not in code
    assert "move_to(" not in code
    assert (
        code.index("cortex_incumbent_iron_bootstrap_removed")
        < code.index("sleep(30)")
        < code.index("cortex_iron_buffer_pre_endogenous")
        < code.index("cortex_iron_endogenous_refuel")
        < code.index("sleep(20)")
    )
    assert (
        "cortex_incumbent_iron_bootstrap_inventory_remaining==0"
        in code
    )
    assert "cortex_incumbent_iron_bootstrap_removed>=1" not in code
    assert "cortex_processor_output" not in code


def test_coal_compile_refuses_budget_shorter_than_causal_windows() -> None:
    plan=coal_plan()
    compilation=compile_structural_action(plan.prepared,settle_seconds=90)

    assert compilation.ready is False
    assert compilation.refusal is not None
    assert "causal windows" in compilation.refusal.detail


@dataclass
class FakeAction:
    agent_idx: int
    code: str
    game_state: dict[str,Any] | None


def action_factory(agent_idx: int,code: str,game_state: Any | None):
    return FakeAction(agent_idx,code,deepcopy(game_state))


class FakeCoalEnvironment:
    def __init__(self, *, promotes: bool) -> None:
        self.promotes=promotes
        self.initial_ticks=30_000
        self.ticks=self.initial_ticks
        self.initial={
            "coal_mined":False,
            "endogenous_coal_reaches_fuel_consumer":False,
            "external_bootstrap_fuel_retired":False,
            "coal_extractor_exists":False,
            "coal_endogenous_growth":0.0,
            "incumbent_iron_survives":True,
            "incumbent_iron_buffer_growth":0.0,
        }
        self.state=deepcopy(self.initial)
        self.step_calls=0

    def get_elapsed_ticks(self) -> int:
        return self.ticks

    def reset(self, *, options=None, seed=None):
        del seed
        game_state=None if options is None else options.get("game_state")
        self.state=deepcopy(self.initial if game_state is None else game_state)
        self.ticks=self.initial_ticks
        return {"state":deepcopy(self.state)}

    def step(self,action: FakeAction):
        self.step_calls+=1
        self.ticks+=7200
        if self.promotes:
            self.state.update({
                "coal_mined":True,
                "endogenous_coal_reaches_fuel_consumer":True,
                "external_bootstrap_fuel_retired":True,
                "coal_extractor_exists":True,
                "coal_endogenous_growth":4.0,
                "incumbent_iron_survives":True,
                "incumbent_iron_buffer_growth":5.0,
            })
        return (
            {"raw_text":action.code},
            1.0,
            False,
            False,
            {
                "output_game_state":deepcopy(self.state),
                "error_occurred":False,
                "ticks":self.ticks,
            },
        )

    def close(self) -> None:
        pass


def lease_attestation() -> dict[str,object]:
    return {
        "status":"active",
        "pid":123,
        "run_id":"f5c-coal-test-run",
        "arena":"f5c-coal-continuation",
        "owner":"f5-control-plane",
        "lease_id":"lease-coal",
        "scope_id":"f5c-coal-continuation:f5c-coal-test-run:lease-coal",
    }


def test_coal_crosses_one_a2_boundary_and_commits_with_iron_survival(
    tmp_path: Path,
) -> None:
    plan=coal_plan()
    ledger=PersistentOptionGrantLedger(tmp_path/"grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lease_attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5c-coal-continuation",
        reason="test coal self-sufficiency",
        ttl_seconds=180,
        now=FIXED_NOW,
    )
    env=FakeCoalEnvironment(promotes=True)
    executor=TransactionalFLEExecutor(env,action_factory=action_factory)
    executor.reset(seed=1)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=executor,
        measure=lambda prepared:dict(env.state),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is True
    assert execution.result is not None
    assert execution.result.status is ActionStatus.ACCEPTED
    assert execution.result.changed_world is True
    assert env.state["coal_mined"] is True
    assert env.state["incumbent_iron_survives"] is True
    assert env.step_calls==1


def test_coal_rolls_back_when_incumbent_iron_does_not_survive(
    tmp_path: Path,
) -> None:
    plan=coal_plan()
    ledger=PersistentOptionGrantLedger(tmp_path/"grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lease_attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5c-coal-continuation",
        reason="test coal regression rollback",
        ttl_seconds=180,
        now=FIXED_NOW,
    )
    env=FakeCoalEnvironment(promotes=False)
    executor=TransactionalFLEExecutor(env,action_factory=action_factory)
    executor.reset(seed=1)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=executor,
        measure=lambda prepared:dict(env.state),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is True
    assert execution.result is not None
    assert execution.result.status is ActionStatus.REJECTED
    assert execution.result.changed_world is False
    assert env.state==env.initial
    assert env.step_calls==1


def test_coal_artifact_path_is_bound_to_commit() -> None:
    import importlib.util

    path=(
        Path(__file__).resolve().parents[1]
        /"scripts"
        /"run_cortex_f5c_coal_self_sufficiency.py"
    )
    spec=importlib.util.spec_from_file_location(
        "run_cortex_f5c_coal_self_sufficiency",
        path,
    )
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    artifact=module.artifact_for_revision(
        245044303,
        "deadbeefcafe0123456789abcdef0123456789",
    )
    assert artifact.name==(
        "cortex_f5c_continuation_245044303_"
        "coal_self_sufficiency_deadbeefcafe.json"
    )
    with pytest.raises(ValueError,match="hexadecimal git commit"):
        module.artifact_for_revision(245044303,"not-a-git-sha")


def test_coal_runner_uses_non_provisioning_live_attachment() -> None:
    source=(
        Path(__file__).resolve().parents[1]
        /"scripts"
        /"run_cortex_f5c_coal_self_sufficiency.py"
    ).read_text()

    assert "attach_live_factorio_environment()" in source
    assert 'gym.make("open_play"' not in source
    assert "list_environments()" not in source
    assert '"task_setup_called":False' in source
    assert "live FLE attachment changed the promoted iron WORLD before A2" in source



def test_live_fle_attach_preserves_existing_entities() -> None:
    source=(
        Path(__file__).parents[1]
        / "src/factorio_ai_lab/integrations/fle.py"
    ).read_text()

    assert "clear_entities=False" in source
    assert "peaceful=False" in source


def test_coal_live_action_uses_checkpoint_only_for_rollback() -> None:
    source=(
        Path(__file__).parents[1]
        / "scripts/run_cortex_f5c_coal_self_sufficiency.py"
    ).read_text()

    assert "use_checkpoint_for_action=False" in source
    assert "use_checkpoint_for_action=True" not in source


def _coal_runner_module():
    import importlib.util

    path=(
        Path(__file__).resolve().parents[1]
        /"scripts"
        /"run_cortex_f5c_coal_self_sufficiency.py"
    )
    spec=importlib.util.spec_from_file_location(
        "run_cortex_f5c_coal_transition_contract",
        path,
    )
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("status","requires_refuel"),
    (("working",False),("no_fuel",True)),
)
def test_iron_transition_entry_accepts_working_or_no_fuel(
    status: str,
    requires_refuel: bool,
) -> None:
    module=_coal_runner_module()

    entry=module._iron_transition_entry({
        "status":status,
        "coal_fuel":0,
        "fuel_remaining":0,
    })

    assert entry["status"]==status
    assert entry["requires_endogenous_refuel"] is requires_refuel
    assert entry["coal_fuel"]==0.0
    assert entry["fuel_remaining"]==0.0


def test_iron_transition_entry_rejects_unrelated_failure_state() -> None:
    module=_coal_runner_module()

    with pytest.raises(RuntimeError,match="unsupported transition state"):
        module._iron_transition_entry({
            "status":"disabled_by_control_behavior",
            "coal_fuel":0,
            "fuel_remaining":0,
        })


def test_no_fuel_transition_still_requires_post_refuel_iron_survival() -> None:
    source=(
        Path(__file__).resolve().parents[1]
        /"scripts"
        /"run_cortex_f5c_coal_self_sufficiency.py"
    ).read_text()

    assert '"no_fuel"' in source
    assert 'after["incumbent_iron_buffer_growth"]>0' in source
    assert 'after["incumbent_iron_survives"] is True' in source


def test_coal_option_requires_frozen_validated_bootstrap_route() -> None:
    plan=coal_plan()
    assert plan.prepared.preflight[
        "bootstrap_mode"
    ]=="path_validated_fast_reposition_then_harvest"
    assert plan.prepared.preflight["bootstrap_path_waypoints_total"]>0
    for row in plan.bootstrap_resources:
        assert isinstance(row.get("position"),dict)
        assert int(row.get("validated_path_waypoints",0))>0


def test_coal_runner_validates_route_before_grant_and_binds_reposition() -> None:
    source=(
        Path(__file__).resolve().parents[1]
        /"scripts"
        /"run_cortex_f5c_coal_self_sufficiency.py"
    ).read_text()

    assert "def _plan_bootstrap_route" in source
    assert "def _validated_path_waypoints" in source
    assert "bind_fast_reposition_tool(" in source
    assert 'record["bootstrap_route"]' in source
    assert "coal_target_validated_path_waypoints" in source
    assert source.index("_plan_bootstrap_route(")<source.index(
        "bridge.issue_a2_grant("
    )
