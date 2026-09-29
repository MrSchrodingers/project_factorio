from __future__ import annotations

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
from factorio_ai_lab.cortex.grant_ledger import (
    LEDGER_ALREADY_CONSUMED,
    PersistentOptionGrantLedger,
)
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    OptionKind,
    OptionRequest,
)
from factorio_ai_lab.cortex.resource_extraction_option import (
    BOOTSTRAP_RESOURCES,
    compose_resource_extraction_option,
)
from factorio_ai_lab.cortex.structural_execute import (
    compile_structural_action,
)
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor
from factorio_ai_lab.planning.placement import ResourceSurvey

FIXED_NOW=datetime(2026,9,29,5,30,tzinfo=UTC)


def resource_survey() -> ResourceSurvey:
    rows=[]
    for x in range(-2,4):
        for y in range(-2,4):
            rows.append({
                "name":"iron-ore",
                "type":"resource",
                "position":{"x":float(x),"y":float(y)},
            })
    return ResourceSurvey.from_entities(
        rows,
        surveyed=(-10.0,-10.0,10.0,10.0),
    )


def extraction_plan():
    provenance=ActionProvenance(
        requested_by="f5-c-deterministic-baseline",
        source_component="tests.test_cortex_f5c_resource_extraction",
        code_revision="f5c-test-sha",
        run_id="f5c-test-run",
    )
    option=OptionRequest(
        option_id="f5c-test-run:iron-extraction",
        kind=OptionKind.ESTABLISH_RESOURCE_EXTRACTION,
        goal="establish endogenous iron extraction",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=60*60),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id="f5c-test-run:iron-extraction-action",
        family=ActionFamily.PLACEMENT,
        intent="establish_resource_extraction",
        provenance=provenance,
        targets=("iron-ore",),
        requires=("world_resources",),
        provides=("iron_extraction",),
    )
    result=compose_resource_extraction_option(
        option,
        action_request=action,
        world_entities=(),
        resources=resource_survey(),
        footprints={
            "burner-mining-drill":(2,2),
            "wooden-chest":(1,1),
        },
    )
    assert result.ready is True
    assert result.plan is not None
    return result.plan


@dataclass
class FakeAction:
    agent_idx:int
    code:str
    game_state:dict[str,Any] | None


def fake_action_factory(agent_idx:int,code:str,game_state):
    return FakeAction(agent_idx,code,deepcopy(game_state))


class FakeExtractionEnvironment:
    def __init__(self, *, succeeds: bool) -> None:
        self.succeeds=succeeds
        self.initial_ticks=10_000
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
        self.last_action=None

    def get_elapsed_ticks(self) -> int:
        return self.ticks

    def reset(self, *, options=None, seed=None):
        del seed
        game_state=None if options is None else options.get("game_state")
        self.state=deepcopy(self.initial if game_state is None else game_state)
        self.ticks=self.initial_ticks
        return {"state":deepcopy(self.state)}

    def step(self, action: FakeAction):
        self.step_calls+=1
        self.last_action=action
        self.ticks+=600
        if self.succeeds:
            self.state.update({
                "resource_patch_valid":True,
                "drill_operational":True,
                "iron_ore_produced":8.0,
                "destination_reachable":True,
                "production_positive_during_validation_window":True,
                "extractor_exists":True,
                "buffer_iron_ore":8.0,
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


def tx(env: FakeExtractionEnvironment) -> TransactionalFLEExecutor:
    executor=TransactionalFLEExecutor(env,action_factory=fake_action_factory)
    executor.reset(seed=17,game_state=deepcopy(env.initial))
    return executor


def probe(env: FakeExtractionEnvironment):
    def measure(_prepared):
        return deepcopy(env.state)
    return measure


def attestation():
    return {
        "status":"active",
        "pid":123,
        "run_id":"f5c-test-run",
        "arena":"f5c-test",
        "owner":"test",
        "lease_id":"lease-1",
        "scope_id":"f5c-test:f5c-test-run:lease-1",
    }


def test_resource_extraction_plan_is_inert_world_harvest_bootstrap() -> None:
    plan=extraction_plan()

    assert plan.request.kind is OptionKind.ESTABLISH_RESOURCE_EXTRACTION
    assert plan.world_mutation is False
    assert plan.execute_authorized is False
    assert plan.prepared.preflight["bootstrap_external_injection"] is False
    assert plan.prepared.preflight["bootstrap_mode"]=="world_harvest_only"
    assert list(plan.bootstrap_resources)==list(BOOTSTRAP_RESOURCES)
    assert [row.op for row in plan.prepared.operations]==[
        "harvest_bootstrap_resources",
        "bootstrap_smelt_iron",
        "craft_extraction_cell",
        "place_extractor",
        "fuel_extractor",
        "place_output_buffer",
        "verify_postconditions",
    ]


def test_resource_extraction_compiles_inside_one_structural_transaction() -> None:
    plan=extraction_plan()
    compiled=compile_structural_action(plan.prepared,settle_seconds=60)

    assert compiled.ready is True
    assert compiled.compiled is not None
    code=compiled.compiled.code
    assert "harvest_resource(" in code
    assert "move_to(" in code
    assert "burner-mining-drill" not in code
    assert "place_entity(" in code
    assert "cortex_buffer_iron_ore" in code
    assert "external" not in code.lower()


def test_f5_a2_executes_resource_extraction_once(tmp_path: Path) -> None:
    plan=extraction_plan()
    ledger=PersistentOptionGrantLedger(tmp_path/"grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5c-test",
        reason="F5-C deterministic resource extraction",
        ttl_seconds=300,
        now=FIXED_NOW,
    )
    env=FakeExtractionEnvironment(succeeds=True)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=tx(env),
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is True
    assert execution.result is not None
    assert execution.result.status is ActionStatus.ACCEPTED
    assert execution.result.changed_world is True
    assert env.step_calls==1

    second=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=tx(FakeExtractionEnvironment(succeeds=True)),
        measure=probe(FakeExtractionEnvironment(succeeds=True)),
        tick_source=FakeExtractionEnvironment(succeeds=True),
        now=FIXED_NOW,
    )
    assert second.executed is False
    assert second.decision.allowed is False
    entry=ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is not None
    assert ledger.check(grant,now=FIXED_NOW).status==LEDGER_ALREADY_CONSUMED


def test_failed_capability_gate_rolls_back_but_consumes_grant(tmp_path: Path) -> None:
    plan=extraction_plan()
    ledger=PersistentOptionGrantLedger(tmp_path/"grants.sqlite3")
    bridge=F5BoundedAuthorityBridge(
        ledger=ledger,
        lease_attestor=attestation,
    )
    scope,grant=bridge.issue_a2_grant(
        plan,
        experiment_id="f5c-test",
        reason="F5-C rollback proof",
        ttl_seconds=300,
        now=FIXED_NOW,
    )
    env=FakeExtractionEnvironment(succeeds=False)
    executor=tx(env)

    execution=bridge.execute_a2(
        plan,
        grant=grant,
        scope=scope,
        executor=executor,
        measure=probe(env),
        tick_source=env,
        now=FIXED_NOW,
    )

    assert execution.executed is True
    assert execution.result is not None
    assert execution.result.status is ActionStatus.REJECTED
    assert execution.result.changed_world is False
    assert env.state==env.initial
    entry=ledger.get(grant.grant_id)
    assert entry is not None
    assert entry.consumed_at is not None
