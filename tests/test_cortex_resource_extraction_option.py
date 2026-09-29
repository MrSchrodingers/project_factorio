from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
    ActionStatus,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.resource_extraction_option import (
    BOOTSTRAP_RESOURCES,
    ResourceExtractionOptionPlan,
    compose_resource_extraction_option,
)
from factorio_ai_lab.cortex.structural_execute import compile_structural_action
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor
from factorio_ai_lab.planning.placement import ResourceSurvey

FIXED_NOW=datetime(2026,9,29,5,0,tzinfo=UTC)


def extraction_plan() -> ResourceExtractionOptionPlan:
    option=OptionRequest(
        option_id="f5c1-iron-extraction",
        kind=OptionKind.ESTABLISH_RESOURCE_EXTRACTION,
        goal="establish automatic iron extraction from zero inventory",
        provenance=ActionProvenance(
            requested_by="f5-deterministic-baseline",
            source_component="factorio_ai_lab.cortex.resource_extraction_option",
            code_revision="f5c1-test-sha",
            run_id="f5c1-test-run",
        ),
        budget=OptionBudget(requested_ticks=60*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c1-iron-extraction-action",
        family=ActionFamily.PLACEMENT,
        intent="bootstrap and establish burner iron extraction",
        provenance=ActionProvenance(
            requested_by="f5-deterministic-baseline",
            source_component="factorio_ai_lab.cortex.resource_extraction_option",
            code_revision="f5c1-test-sha",
            run_id="f5c1-test-run",
        ),
        provides=("iron_extraction",),
    )
    tiles={
        (x,y):"iron-ore"
        for x in range(8,14)
        for y in range(8,14)
    }
    result=compose_resource_extraction_option(
        option,
        action_request=action,
        world_entities=(),
        resources=ResourceSurvey(
            tiles=tiles,
            surveyed=(0.0,0.0,30.0,30.0),
        ),
        footprints={
            "burner-mining-drill":(2,2),
            "wooden-chest":(1,1),
        },
    )
    assert result.ready
    assert result.plan is not None
    return result.plan


def test_compose_zero_inventory_extraction_is_inert_and_frozen() -> None:
    plan=extraction_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_RESOURCE_EXTRACTION
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.contract_version=="cortex_structural_ops_v4"
    assert plan.prepared.binding=="cortex.structural.resource_extraction"
    assert plan.prepared.preflight["bootstrap_mode"]=="world_harvest_only"
    assert plan.prepared.preflight["bootstrap_external_injection"] is False
    assert tuple(plan.bootstrap_resources)==BOOTSTRAP_RESOURCES
    names={condition.name for condition in plan.termination_conditions}
    assert {
        "resource_patch_valid",
        "drill_operational",
        "iron_ore_produced",
        "destination_reachable",
        "production_positive_during_validation_window",
        "extractor_exists",
        "buffer_iron_ore",
    }.issubset(names)


def test_compiled_extraction_bootstraps_from_world_without_fixture_inventory() -> None:
    plan=extraction_plan()
    compilation=compile_structural_action(plan.prepared,settle_seconds=60)

    assert compilation.ready
    assert compilation.compiled is not None
    code=compilation.compiled.code
    ast.parse(code)
    assert code.count("harvest_resource(")==4
    assert "Resource.Stone" in code
    assert "Resource.Coal" in code
    assert "Resource.IronOre" in code
    assert "Resource.Wood" in code
    assert "Prototype.StoneFurnace" in code
    assert "Prototype.BurnerMiningDrill" in code
    assert "Prototype.WoodenChest" in code
    assert "cortex_buffer_iron_ore" in code
    assert "cortex_processor_output" not in code
    assert "sleep(15)" in code


@dataclass
class FakeAction:
    agent_idx: int
    code: str
    game_state: dict[str,Any] | None


def action_factory(agent_idx: int,code: str,game_state: Any | None):
    return FakeAction(agent_idx,code,deepcopy(game_state))


class FakeExtractionEnvironment:
    def __init__(self, *, promotes: bool) -> None:
        self.promotes=promotes
        self.initial_ticks=20_000
        self.ticks=self.initial_ticks
        self.initial={
            "resource_patch_valid":True,
            "drill_operational":False,
            "iron_ore_produced":0.0,
            "destination_reachable":False,
            "production_positive_during_validation_window":False,
            "extractor_exists":False,
            "buffer_iron_ore":0.0,
        }
        self.state=deepcopy(self.initial)
        self.step_calls=0
        self.last_action: FakeAction | None=None

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
        self.last_action=action
        self.ticks+=3600
        if self.promotes:
            self.state.update({
                "drill_operational":True,
                "iron_ore_produced":3.0,
                "destination_reachable":True,
                "production_positive_during_validation_window":True,
                "extractor_exists":True,
                "buffer_iron_ore":3.0,
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
        "run_id":"f5c1-test-run",
        "arena":"f5c1-development",
        "owner":"f5-control-plane",
        "lease_id":"lease-1",
        "scope_id":"f5c1-development:f5c1-test-run:lease-1",
    }


def test_extraction_crosses_same_a2_boundary_and_commits_on_physical_output(
    tmp_path: Path,
) -> None:
    plan=extraction_plan()
    ledger=PersistentOptionGrantLedger(tmp_path/"grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lease_attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5c1-development",
        reason="test first physical F5 capability",
        ttl_seconds=120,
        now=FIXED_NOW,
    )
    env=FakeExtractionEnvironment(promotes=True)
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
    assert execution.result.observed_ticks==3600
    assert execution.result.feedback_budget is not None
    assert execution.result.feedback_budget.budget_overrun is False
    assert env.state["buffer_iron_ore"]==3.0
    assert env.step_calls==1
    assert env.last_action is not None
    assert "harvest_resource(" in env.last_action.code


def test_extraction_rolls_back_if_automatic_output_is_missing(
    tmp_path: Path,
) -> None:
    plan=extraction_plan()
    ledger=PersistentOptionGrantLedger(tmp_path/"grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=lease_attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5c1-development",
        reason="test rejected physical F5 capability",
        ttl_seconds=120,
        now=FIXED_NOW,
    )
    env=FakeExtractionEnvironment(promotes=False)
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
