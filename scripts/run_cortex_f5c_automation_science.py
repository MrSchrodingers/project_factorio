#!/usr/bin/env python3
"""F5-C endogenous automation-science capability runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fle.commons.models.game_state import GameState

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.automation_science_option import (
    INITIAL_COAL_DRAW,
    TARGET_PACKS,
    compose_automation_science_option,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_automation_science_v1"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
DASHBOARD_BUILD_INFO=Path(
    "/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json"
)
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_automation_science"
OWNER="run_cortex_f5c_automation_science"
DEFAULT_OPTION_SECONDS=220
DEFAULT_GRANT_TTL_SECONDS=600

ServiceStateReader=Callable[[],dict[str,str]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _write(path: Path,payload: Mapping[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(
        json.dumps(dict(payload),indent=2,sort_keys=True,default=str)+"\n",
        encoding="utf-8",
    )
    temp.replace(path)


def phase_state() -> dict[str,Any]:
    return _load(RUNS_DIR/"cortex_phase_state.json")


def _service_state() -> dict[str,str]:
    def run(action: str) -> str:
        done=subprocess.run(
            ["systemctl",action,"factorio-ai-evolution.service"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output=(done.stdout or done.stderr or "").strip()
        return output.splitlines()[0] if output else f"returncode:{done.returncode}"
    return {"active":run("is-active"),"enabled":run("is-enabled")}


def _assert_evolution_off(
    reader: ServiceStateReader=_service_state,
) -> dict[str,str]:
    state=dict(reader())
    if state!={"active":"inactive","enabled":"disabled"}:
        raise RuntimeError(f"legacy evolution must remain OFF: {state!r}")
    return state


def _intervention_count() -> int:
    payload=_load(INTERVENTION_LEDGER)
    rows=payload.get("interventions")
    if not isinstance(rows,list):
        raise TypeError("F5 intervention ledger interventions must be a list")
    return len(rows)


def _ledger_state(path: Path) -> dict[str,Any]:
    return {
        "path":str(path),
        "file_exists":path.exists(),
        "file_writable":(not path.exists()) or os.access(path,os.R_OK|os.W_OK),
        "parent_writable":os.access(path.parent,os.W_OK),
    }


def _read_build_commit(path: Path) -> dict[str,Any]:
    payload=_load(path)
    commit=payload.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError(f"build info missing commit: {path}")
    return payload


def _entity_at(
    rows: Any,
    *,
    name: str,
    position: tuple[float,float],
    tolerance: float=0.25,
) -> Mapping[str,Any] | None:
    if not isinstance(rows,list):
        return None
    for row in rows:
        if not isinstance(row,Mapping) or row.get("name")!=name:
            continue
        pos=row.get("position")
        if not isinstance(pos,Mapping):
            continue
        try:
            x=float(pos["x"]); y=float(pos["y"])
        except (KeyError,TypeError,ValueError):
            continue
        if abs(x-position[0])<=tolerance and abs(y-position[1])<=tolerance:
            return row
    return None


def _contents_count(row: Mapping[str,Any],item: str) -> float:
    raw=row.get("contents")
    if not isinstance(raw,list):
        return 0.0
    total=0.0
    for stack in raw:
        if not isinstance(stack,Mapping) or stack.get("name")!=item:
            continue
        value=stack.get("count")
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            total+=float(value)
    return total


def _promoted_positions(
    state: Mapping[str,Any],
) -> tuple[int,dict[str,tuple[float,float]],Path]:
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C baseline state missing")
    seed=baseline.get("seed")
    copper_state=baseline.get("copper_chain")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5-C base seed missing")
    if not isinstance(copper_state,Mapping):
        raise TypeError("promoted copper_chain state missing")
    artifact_raw=copper_state.get("artifact_path")
    if not isinstance(artifact_raw,str) or not artifact_raw:
        raise TypeError("promoted copper_chain artifact path missing")
    artifact=Path(artifact_raw)
    payload=_load(artifact)
    if (
        payload.get("status")!="completed"
        or payload.get("capability_promoted")!="copper_chain"
        or payload.get("transaction_committed") is not True
    ):
        raise RuntimeError("selected copper_chain artifact is not promoted")
    plan=payload.get("option_plan")
    rows=plan.get("positions") if isinstance(plan,Mapping) else None
    if not isinstance(rows,Mapping):
        raise TypeError("promoted copper positions unavailable")

    names=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","boiler","steam_engine",
        "copper_extractor","copper_furnace",
    )
    positions: dict[str,tuple[float,float]]={}
    for name in names:
        row=rows.get(name)
        if not isinstance(row,Mapping):
            raise TypeError(f"promoted position unavailable: {name}")
        positions[name]=(float(row["x"]),float(row["y"]))
    copper=positions["copper_extractor"]
    positions["copper_buffer"]=(copper[0]+0.5,copper[1]+1.5)
    return seed,positions,artifact


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_continuation_{seed}_automation_science_{commit[:12]}.json"
    )


def preflight(
    *,
    artifact: Path,
    revision: Mapping[str,Any] | None=None,
    service_state_reader: ServiceStateReader=_service_state,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=dict(code_revision() if revision is None else revision)
    if revision.get("dirty") is not False:
        raise RuntimeError("automation science requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("automation science commit is unavailable")
    if artifact.exists():
        raise FileExistsError(
            "automation-science artifact already exists for this commit"
        )
    evolution=_assert_evolution_off(service_state_reader)
    if _intervention_count()!=0:
        raise RuntimeError("automation science requires zero F5 interventions")
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError("automation science requires no active WorldLease")
    ledger=_ledger_state(ledger_path)
    if not ledger["parent_writable"] or not ledger["file_writable"]:
        raise RuntimeError(f"authority ledger is not writable: {ledger!r}")

    runtime_build=_read_build_commit(RUNTIME_BUILD_INFO)
    dashboard_build=_read_build_commit(DASHBOARD_BUILD_INFO)
    if runtime_build["commit"]!=commit or dashboard_build["commit"]!=commit:
        raise RuntimeError(
            "automation science requires runtime/dashboard on current commit"
        )

    state=phase_state()
    protocol=state.get("phase5_protocol")
    if not isinstance(protocol,Mapping):
        raise TypeError("phase5 protocol state missing")
    achieved=protocol.get("achieved_capabilities")
    expected=[
        "iron_extraction",
        "coal_self_sufficiency",
        "iron_smelting",
        "steam_power",
        "copper_chain",
    ]
    if achieved!=expected:
        raise RuntimeError(
            f"automation science requires promoted prefix {expected!r}, got {achieved!r}"
        )
    if protocol.get("next_capability")!="automation_science":
        raise RuntimeError("automation science is not the current F5-C frontier")

    seed,positions,copper_artifact=_promoted_positions(state)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
    finally:
        observer.close()
    rows=snapshot.get("entities")
    required_entities={
        "iron_extractor":"burner-mining-drill",
        "iron_buffer":"wooden-chest",
        "iron_furnace":"stone-furnace",
        "coal_extractor":"burner-mining-drill",
        "coal_buffer":"wooden-chest",
        "boiler":"boiler",
        "steam_engine":"steam-engine",
        "copper_extractor":"burner-mining-drill",
        "copper_buffer":"wooden-chest",
        "copper_furnace":"stone-furnace",
    }
    observed: dict[str,Mapping[str,Any]]={}
    for key,name in required_entities.items():
        row=_entity_at(rows,name=name,position=positions[key])
        if row is None:
            raise RuntimeError(
                f"promoted entity absent from WORLD: {key} at {positions[key]!r}"
            )
        observed[key]=row
    coal_stock=_contents_count(observed["coal_buffer"],"coal")
    if coal_stock < INITIAL_COAL_DRAW:
        raise RuntimeError(
            f"automation science requires {INITIAL_COAL_DRAW} endogenous coal, "
            f"observed {coal_stock}"
        )
    return {
        "status":"preflight_pass",
        "base_seed":seed,
        "code_revision":revision,
        "copper_artifact":str(copper_artifact),
        "phase_state":{
            "phase":state.get("phase"),
            "phase5_checkpoint":state.get("phase5_checkpoint"),
            "phase5_next_checkpoint":state.get("phase5_next_checkpoint"),
            "achieved_capabilities":achieved,
            "next_capability":protocol.get("next_capability"),
        },
        "positions":{
            name:{"x":pos[0],"y":pos[1]}
            for name,pos in positions.items()
        },
        "coal_stock":coal_stock,
        "world_entity_count":snapshot.get("entity_count"),
        "evolution":evolution,
        "intervention_count":0,
        "world_lease_state":lease,
        "authority_ledger":ledger,
        "runtime_build_info":runtime_build,
        "dashboard_build_info":dashboard_build,
        "world_mutation":False,
        "grant_issued":False,
        "option_executed_live":False,
        "world_reset":False,
        "external_resource_injection":False,
        "persistent_science_inventory":True,
        "uses_powered_manufacturing":False,
        "target_packs":TARGET_PACKS,
    }


def _option_request(*,run_id: str,commit: str,seconds: int) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:automation-science",
        kind=OptionKind.ESTABLISH_AUTOMATION_SCIENCE,
        goal=(
            "produce two endogenous automation-science batches while preserving "
            "iron, coal, smelting, steam and copper capabilities"
        ),
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_automation_science",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(
            requested_ticks=seconds*int(TICKS_PER_SECOND),
        ),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:automation-science-action",
        family=ActionFamily.CRAFT,
        intent="craft persistent automation science from endogenous inputs",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_automation_science",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=(
            "iron_extraction","coal_self_sufficiency","iron_smelting",
            "steam_power","copper_chain",
        ),
        provides=("automation_science",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    del prepared
    numeric=(
        "automation_science_inventory",
        "science_batch1",
        "science_batch2",
        "iron_plate_ready",
        "copper_plate_ready",
        "iron_topup_ore",
        "iron_topup_plates",
        "copper_topup_ore",
        "copper_topup_plates",
        "iron_survival_growth",
        "coal_survival_growth",
        "smelting_survival_growth",
        "copper_survival_growth",
        "copper_smelting_growth",
        "steam_survival_amount",
        "steam_survival_energy",
    )
    values={
        "automation_science_output_positive":bool(
            getattr(namespace,"cortex_automation_science_output_positive",False)
        ),
        "inputs_endogenous":bool(
            getattr(namespace,"cortex_inputs_endogenous",False)
        ),
        "production_sustained":bool(
            getattr(namespace,"cortex_production_sustained",False)
        ),
        "iron_extraction_survives":bool(
            getattr(namespace,"cortex_iron_extraction_survives",False)
        ),
        "coal_self_sufficiency_survives":bool(
            getattr(namespace,"cortex_coal_self_sufficiency_survives",False)
        ),
        "iron_smelting_survives":bool(
            getattr(namespace,"cortex_iron_smelting_survives",False)
        ),
        "steam_power_survives":bool(
            getattr(namespace,"cortex_steam_power_survives",False)
        ),
        "copper_chain_survives":bool(
            getattr(namespace,"cortex_copper_chain_survives",False)
        ),
    }
    for name in numeric:
        values[name]=float(getattr(namespace,f"cortex_{name}",0) or 0)
    return values


def run_automation_science(
    *,
    artifact: Path,
    option_seconds: int=DEFAULT_OPTION_SECONDS,
    grant_ttl_seconds: int=DEFAULT_GRANT_TTL_SECONDS,
    ledger_path: Path=DEFAULT_LEDGER,
    service_state_reader: ServiceStateReader=_service_state,
) -> dict[str,Any]:
    revision=code_revision()
    pf=preflight(
        artifact=artifact,
        revision=revision,
        service_state_reader=service_state_reader,
        ledger_path=ledger_path,
    )
    commit=str(revision["commit"])
    seed=int(pf["base_seed"])
    run_id=(
        f"cortex-f5c-automation-science-{seed}-"
        +datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "planning_stage":"starting",
        "capability":"automation_science",
        "base_seed":seed,
        "run_id":run_id,
        "code_revision":revision,
        "preflight":pf,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "world_reset":False,
        "external_resource_injection":False,
        "persistent_science_inventory":True,
        "uses_powered_manufacturing":False,
        "human_intervention_count":0,
        "grant_issued":False,
        "option_execution_attempts":0,
        "automatic_retry":False,
        "started_at":utc_now(),
    }
    env=None
    with FactorioWorldLease(run_id=run_id,arena=ARENA,owner=OWNER) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        _write(artifact,record)
        try:
            env=attach_live_factorio_environment()
            record["fle_eval_timeout_s"]=enforce_minimum_eval_timeout(
                env,minimum_seconds=300
            )
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"automation_science",
                    "progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace
            observer=FactorioObserver()
            try:
                after_attach=observer.snapshot()
            finally:
                observer.close()
            if after_attach.get("entity_count")!=pf["world_entity_count"]:
                raise RuntimeError(
                    "live FLE attachment changed WORLD before automation-science A2"
                )

            raw_positions=pf["positions"]
            if not isinstance(raw_positions,Mapping):
                raise TypeError("automation science preflight positions missing")
            positions={}
            for name,row in raw_positions.items():
                if not isinstance(row,Mapping):
                    raise TypeError(f"automation-science position invalid: {name}")
                positions[str(name)]=(float(row["x"]),float(row["y"]))

            option=_option_request(
                run_id=run_id,commit=commit,seconds=option_seconds
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_automation_science_option(
                option,
                action_request=action,
                positions=positions,
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(
                    f"automation science Option refused: {refusal}"
                )
            plan=composed.plan
            checkpoint=GameState.from_instance(instance)
            executor.game_state=checkpoint
            record["rollback_checkpoint"]=True
            before=_measure(namespace,plan.prepared)

            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(
                ledger=ledger,
                lease_attestor=lease.active_attestation,
            )
            scope,grant=bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason=(
                    "F5-C endogenous automation science preserving five incumbents"
                ),
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "automation science A2 validation refused: "
                    +json.dumps(validation.to_dict(),sort_keys=True)
                )
            record.update({
                "planning_stage":"grant_validated",
                "status":"grant_issued_pending_execution",
                "option_plan":plan.to_dict(),
                "scope":scope.to_dict(),
                "grant":grant.to_dict(),
                "grant_validation":validation.to_dict(),
                "measurement_before":before,
                "grant_issued":True,
            })
            _write(artifact,record)

            record["evolution_before_execute"]=_assert_evolution_off(
                service_state_reader
            )
            if lease.active_attestation()["scope_id"]!=scope.world_lease_id:
                raise RuntimeError(
                    "automation-science WorldLease scope changed before execute"
                )
            record["option_execution_attempts"]=1
            _write(artifact,record)

            execution=bridge.execute_a2(
                plan,
                grant=grant,
                scope=scope,
                executor=executor,
                measure=lambda prepared:_measure(namespace,prepared),
                tick_source=env,
                use_checkpoint_for_action=False,
            )
            after=_measure(namespace,plan.prepared)
            result=execution.result
            accepted=(
                execution.executed
                and result is not None
                and result.status.value=="accepted"
                and result.changed_world is True
            )
            capability_gate={
                "automation_science_output_positive":(
                    after["automation_science_output_positive"] is True
                    and after["automation_science_inventory"]>=TARGET_PACKS
                ),
                "inputs_endogenous":after["inputs_endogenous"] is True,
                "production_sustained":(
                    after["production_sustained"] is True
                    and after["science_batch1"]>0
                    and after["science_batch2"]>0
                ),
            }
            survival_gate={
                "previously_promoted":[
                    "iron_extraction","coal_self_sufficiency","iron_smelting",
                    "steam_power","copper_chain",
                ],
                "iron_extraction_survives":(
                    after["iron_extraction_survives"] is True
                    and after["iron_survival_growth"]>0
                ),
                "coal_self_sufficiency_survives":(
                    after["coal_self_sufficiency_survives"] is True
                    and after["coal_survival_growth"]>0
                ),
                "iron_smelting_survives":(
                    after["iron_smelting_survives"] is True
                    and after["smelting_survival_growth"]>0
                ),
                "steam_power_survives":(
                    after["steam_power_survives"] is True
                    and after["steam_survival_amount"]>0
                    and after["steam_survival_energy"]>0
                ),
                "copper_chain_survives":(
                    after["copper_chain_survives"] is True
                    and after["copper_survival_growth"]>0
                    and after["copper_smelting_growth"]>0
                ),
            }
            survival_gate["regressed"]=[
                name
                for name,key in (
                    ("iron_extraction","iron_extraction_survives"),
                    ("coal_self_sufficiency","coal_self_sufficiency_survives"),
                    ("iron_smelting","iron_smelting_survives"),
                    ("steam_power","steam_power_survives"),
                    ("copper_chain","copper_chain_survives"),
                )
                if not survival_gate[key]
            ]
            survival_gate["passed"]=not survival_gate["regressed"]
            promoted=(
                accepted
                and all(capability_gate.values())
                and survival_gate["passed"] is True
            )
            record.update({
                "status":"completed" if promoted else "rejected",
                "finished_at":utc_now(),
                "option_execution":execution.to_dict(),
                "measurement_final":after,
                "capability_gate":capability_gate,
                "survival_gate":survival_gate,
                "capability_promoted":(
                    "automation_science" if promoted else None
                ),
                "transaction_committed":promoted,
                "intervention_snapshot":executor.intervention_snapshot(),
                "trajectory":{
                    "state":before,
                    "candidate_options":[plan.to_dict()],
                    "memory_retrieval":{
                        "mode":"deterministic_baseline",
                        "used_for_selection":False,
                        "records":[],
                    },
                    "selected_option":{
                        "option_id":plan.request.option_id,
                        "kind":plan.request.kind.value,
                    },
                    "expected_effect":{
                        "capability":"automation_science",
                        "survival_required":survival_gate["previously_promoted"],
                    },
                    "authority_level":"A2",
                    "execution_trace":execution.to_dict(),
                    "postconditions":after,
                    "capability_delta":{
                        "promoted":[
                            "automation_science"
                        ] if promoted else [],
                        "regressed":survival_gate["regressed"],
                    },
                    "resource_cost":{
                        "external_resource_injection":False,
                        "world_reset":False,
                    },
                    "reward_components":{
                        "capability_gate":int(all(capability_gate.values())),
                        "survival_gate":int(survival_gate["passed"]),
                    },
                    "rollback":not promoted,
                },
            })
            _write(artifact,record)
            return record
        except Exception as exc:  # noqa: BLE001
            record.update({
                "status":"failed",
                "finished_at":utc_now(),
                "failure":{"type":type(exc).__name__,"message":str(exc)},
            })
            _write(artifact,record)
            return record
        finally:
            if env is not None:
                env.close()


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--artifact",type=Path,default=None)
    args=parser.parse_args()
    revision=code_revision()
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("automation-science code revision unavailable")
    state=phase_state()
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C baseline state unavailable")
    seed=baseline.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5-C base seed unavailable")
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(seed,commit).resolve()
    )
    if not args.execute:
        payload=preflight(artifact=artifact,revision=revision)
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_automation_science(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
