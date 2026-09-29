#!/usr/bin/env python3
"""F5-C deterministic autonomous baseline, first physical capability.

Without --execute this script is a read-only preflight.  With --execute it
initializes exactly one preregistered development open-play episode and attempts
exactly one A2 resource-extraction Option.  There is no retry loop, no scheduler
and no learned policy authority.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.live_canary import available_inventory, world_rows
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.resource_extraction_option import (
    compose_resource_extraction_option,
)
from factorio_ai_lab.instrumentation.runtime import runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    enforce_minimum_eval_timeout,
    list_environments,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.planning.placement import WorldSurvey
from factorio_ai_lab.runtime import WORLD_LEASE_STATE, FactorioWorldLease

SCHEMA_VERSION="cortex_f5c_deterministic_baseline_v1"
DEFAULT_SEED=1619515465
DEFAULT_OPTION_SECONDS=60
DEFAULT_GRANT_TTL_SECONDS=300
ARENA="cortex_f5c_deterministic_baseline"
OWNER="run_cortex_f5c_deterministic_baseline"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
MANIFEST=Path("configs/cortex_f5_autonomy_v1.json")
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"

ServiceStateReader=Callable[[],Mapping[str,str]]
PhaseStateReader=Callable[[],Mapping[str,Any]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def artifact_for_seed(seed: int) -> Path:
    return RUNS_DIR/"audits"/f"cortex_f5c_development_{seed}_iron_extraction.json"


def _load_object(path: Path) -> dict[str,Any]:
    payload=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload,dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def _atomic_json(path: Path,payload: Mapping[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(
        json.dumps(dict(payload),indent=2,sort_keys=True,default=str)+"\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def phase_state() -> dict[str,Any]:
    path=RUNS_DIR/"cortex_phase_state.json"
    if not path.exists():
        return {}
    try:
        return _load_object(path)
    except (OSError,json.JSONDecodeError,TypeError):
        return {}


def evolution_service_state() -> dict[str,str]:
    def run(*args: str) -> str:
        done=subprocess.run(
            ["systemctl",*args,"factorio-ai-evolution"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output=(done.stdout or done.stderr or "").strip()
        return output.splitlines()[0] if output else f"returncode:{done.returncode}"
    return {"active":run("is-active"),"enabled":run("is-enabled")}


def read_world_lease_state(path: Path=WORLD_LEASE_STATE) -> dict[str,Any]:
    if not path.exists():
        return {"status":"absent"}
    return _load_object(path)


def _assert_evolution_off(
    reader: ServiceStateReader=evolution_service_state,
) -> dict[str,str]:
    state=dict(reader())
    active=str(state.get("active") or "")
    enabled=str(state.get("enabled") or "")
    if active!="inactive" or enabled!="disabled":
        raise RuntimeError(
            "F5-C requires factorio-ai-evolution inactive+disabled; "
            f"got active={active!r} enabled={enabled!r}"
        )
    return {"active":active,"enabled":enabled}


def _assert_f5b_ready(
    reader: PhaseStateReader=phase_state,
) -> dict[str,Any]:
    state=dict(reader())
    bridge=state.get("phase5_authority_bridge")
    if not isinstance(bridge,dict):
        bridge={}
    if (
        state.get("phase")!="F5"
        or state.get("phase5_checkpoint")!="F5-B"
        or state.get("phase5_next_checkpoint")!="F5-C"
        or bridge.get("validated") is not True
        or bridge.get("ambient_authority")!="A0"
        or bridge.get("max_bounded_grant")!="A2"
        or bridge.get("max_executions")!=1
        or bridge.get("continuous_authority") is not False
        or bridge.get("world_mutation") is not False
    ):
        raise RuntimeError(
            "F5-C requires validated F5-B with ambient A0 and A2 one-shot boundary"
        )
    return {
        "phase":"F5",
        "phase5_checkpoint":"F5-B",
        "phase5_next_checkpoint":"F5-C",
        "authority_bridge_validated":True,
        "ambient_authority":"A0",
        "max_bounded_grant":"A2",
        "max_executions":1,
    }


def _manifest(path: Path=MANIFEST) -> dict[str,Any]:
    return _load_object(path)


def _development_seeds(manifest: Mapping[str,Any]) -> tuple[int,...]:
    partitions=manifest.get("seed_partitions")
    if not isinstance(partitions,Mapping):
        raise TypeError("F5 manifest seed_partitions must be an object")
    raw=partitions.get("development")
    if not isinstance(raw,list) or not raw:
        raise RuntimeError("F5 manifest development partition is unavailable")
    if not all(isinstance(value,int) and not isinstance(value,bool) for value in raw):
        raise RuntimeError("F5 development partition contains non-integer seed")
    return tuple(raw)


def _intervention_count(path: Path=INTERVENTION_LEDGER) -> int:
    payload=_load_object(path)
    rows=payload.get("interventions")
    if not isinstance(rows,list):
        raise TypeError("F5 intervention ledger interventions must be a list")
    return len(rows)


def preflight_f5c(
    *,
    seed: int,
    artifact: Path,
    revision: Mapping[str,Any],
    service_state_reader: ServiceStateReader=evolution_service_state,
    phase_state_reader: PhaseStateReader=phase_state,
    manifest_path: Path=MANIFEST,
    intervention_ledger_path: Path=INTERVENTION_LEDGER,
    lease_state_path: Path=WORLD_LEASE_STATE,
) -> dict[str,Any]:
    manifest=_manifest(manifest_path)
    development=_development_seeds(manifest)
    if seed not in development:
        raise ValueError(
            f"seed {seed} is not in frozen F5 development partition"
        )
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit.strip():
        raise RuntimeError("F5-C requires exact code revision")
    if revision.get("dirty") is not False:
        raise RuntimeError("F5-C requires clean committed source before live execution")
    if artifact.exists():
        raise FileExistsError(
            "F5-C development artifact already exists; inspect it instead of rerunning"
        )
    phase=_assert_f5b_ready(phase_state_reader)
    evolution=_assert_evolution_off(service_state_reader)
    lease=read_world_lease_state(lease_state_path)
    if str(lease.get("status") or "")=="active":
        raise RuntimeError(
            "F5-C preflight found active persisted WorldLease: "
            + json.dumps(lease,sort_keys=True,default=str)[:1200]
        )
    interventions=_intervention_count(intervention_ledger_path)
    if interventions!=0:
        raise RuntimeError(
            f"F5-C zero-intervention baseline requires empty ledger; got {interventions}"
        )
    return {
        "schema_version":SCHEMA_VERSION,
        "status":"preflight_pass",
        "seed":seed,
        "partition":"development",
        "code_revision":dict(revision),
        "phase_state":phase,
        "evolution":evolution,
        "world_lease_state":lease,
        "intervention_count":interventions,
        "world_mutation":False,
        "world_reset":False,
        "grant_issued":False,
        "option_executed_live":False,
        "continuous_authority":False,
        "ambient_authority":"A0",
        "max_bounded_grant":"A2",
        "recorded_at":utc_now(),
    }


def _nonempty_inventory(rows: list[dict[str,Any]]) -> dict[str,float]:
    inventory=available_inventory(rows)
    return {
        name:value
        for name,value in inventory.items()
        if name!="character" and float(value)>0
    }


def _option_request(
    *,
    run_id: str,
    commit: str,
    option_seconds: int,
) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:iron-extraction",
        kind=OptionKind.ESTABLISH_RESOURCE_EXTRACTION,
        goal="establish endogenous automatic iron extraction from empty inventory",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_deterministic_baseline",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(
            requested_ticks=int(option_seconds)*int(TICKS_PER_SECOND)
        ),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:iron-extraction-action",
        family=ActionFamily.PLACEMENT,
        intent="bootstrap and establish burner iron extraction",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_deterministic_baseline",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=("observed_world_resources",),
        provides=("iron_extraction",),
    )


def _namespace_measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    placement=prepared.preflight.get("placement")
    resource_tiles=(
        placement.get("resource_tiles")
        if isinstance(placement,dict)
        else None
    )
    resource_patch_valid=(
        isinstance(resource_tiles,int)
        and not isinstance(resource_tiles,bool)
        and resource_tiles>0
    )
    iron=float(getattr(namespace,"cortex_buffer_iron_ore",0) or 0)
    return {
        "resource_patch_valid":resource_patch_valid,
        "drill_operational":bool(
            getattr(namespace,"cortex_drill_operational",False)
        ),
        "iron_ore_produced":iron,
        "destination_reachable":bool(
            getattr(namespace,"cortex_destination_reachable",False)
        ),
        "production_positive_during_validation_window":bool(
            getattr(namespace,"cortex_production_positive",False)
        ),
        "extractor_exists":bool(
            getattr(namespace,"cortex_extractor_exists",False)
        ),
        "buffer_iron_ore":iron,
    }


def _trajectory(
    *,
    plan: Any,
    execution: Any,
    before: Mapping[str,Any],
    after: Mapping[str,Any],
) -> dict[str,Any]:
    result=execution.result
    accepted=(
        result is not None
        and getattr(result.status,"value",None)=="accepted"
        and result.changed_world is True
    )
    return {
        "state":dict(before),
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
        "expected_effect":[
            row.to_dict() for row in plan.predicted_effects
        ],
        "authority_level":"A2",
        "execution_trace":(
            None if result is None else result.to_dict()
        ),
        "postconditions":(
            []
            if result is None or result.action_result is None
            else [
                row.to_dict()
                for row in result.action_result.postconditions
            ]
        ),
        "capability_delta":{
            "promoted":["iron_extraction"] if accepted else [],
            "regressed":[],
        },
        "resource_cost":{
            "bootstrap_mode":"world_harvest_only",
            "external_resource_injection":False,
            "resources":[dict(row) for row in plan.bootstrap_resources],
        },
        "rollback":not accepted,
        "reward_components":{
            "hard_physical_success":1.0 if accepted else 0.0,
            "authority_violation":0.0,
            "human_intervention":0.0,
        },
        "next_state":dict(after),
    }


def run_f5c(
    *,
    seed: int,
    option_seconds: int,
    grant_ttl_seconds: int,
    artifact: Path,
    ledger_path: Path,
    revision: Mapping[str,Any] | None=None,
    service_state_reader: ServiceStateReader=evolution_service_state,
    phase_state_reader: PhaseStateReader=phase_state,
) -> dict[str,Any]:
    if option_seconds<=45:
        raise ValueError("F5-C option_seconds must exceed 45s bootstrap smelting")
    if grant_ttl_seconds<=0:
        raise ValueError("grant_ttl_seconds must be positive")
    revision=dict(revision or code_revision())
    preflight=preflight_f5c(
        seed=seed,
        artifact=artifact,
        revision=revision,
        service_state_reader=service_state_reader,
        phase_state_reader=phase_state_reader,
    )
    commit=str(revision["commit"])
    run_id=datetime.now(UTC).strftime(
        f"cortex-f5c-dev-{seed}-%Y%m%dT%H%M%SZ"
    )
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "run_id":run_id,
        "seed":seed,
        "partition":"development",
        "capability":"iron_extraction",
        "code_revision":revision,
        "preflight":preflight,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "automatic_retry":False,
        "option_execution_attempts":0,
        "world_reset":False,
        "world_reset_credit_scope":"environment_initialization_only",
        "external_resource_injection":False,
        "started_at":utc_now(),
    }

    env=None
    ledger=None
    grant=None
    with FactorioWorldLease(
        run_id=run_id,
        arena=ARENA,
        owner=OWNER,
    ) as lease:
        attestation=lease.active_attestation()
        record["world_lease"]=dict(attestation)
        record["evolution_after_lease"]=_assert_evolution_off(
            service_state_reader
        )
        try:
            import gym

            list_environments()
            env=gym.make("open_play",run_idx=0)
            record["fle_eval_timeout_s"]=enforce_minimum_eval_timeout(
                env,
                minimum_seconds=300,
            )
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"iron_extraction",
                    "run_status":record.get("status"),
                    "progress":"F5-C",
                },
            )
            executor.reset(seed=seed)
            record["world_reset"]=True
            record["world_reset_at"]=utc_now()
            record["status"]="episode_initialized"
            _atomic_json(artifact,record)

            instance=env.unwrapped.instance
            namespace=instance.namespace
            physical_rows=world_rows(namespace,resources=False)
            injected=_nonempty_inventory(physical_rows)
            record["initial_nonempty_inventory"]=injected
            if injected:
                raise RuntimeError(
                    "F5-C open_play must start empty; injected inventory: "
                    + json.dumps(injected,sort_keys=True)
                )

            rows=world_rows(namespace,resources=True)
            footprints=runtime_entity_footprints(instance)
            survey=WorldSurvey.from_entities(
                rows,
                footprints=footprints,
                surveyed=(-500.0,-500.0,500.0,500.0),
            )
            if survey.resources is None:
                raise RuntimeError("F5-C resource survey returned no resources")

            option=_option_request(
                run_id=run_id,
                commit=commit,
                option_seconds=option_seconds,
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_resource_extraction_option(
                option,
                action_request=action,
                world_entities=survey.entities,
                resources=survey.resources,
                footprints=footprints,
            )
            if not composed.ready or composed.plan is None:
                refusal=(
                    None if composed.refusal is None
                    else composed.refusal.to_dict()
                )
                raise RuntimeError(
                    f"F5-C resource extraction composition refused: {refusal}"
                )
            plan=composed.plan
            before=_namespace_measure(namespace,plan.prepared)

            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(
                ledger=ledger,
                lease_attestor=lease.active_attestation,
            )
            scope,grant=bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason=(
                    "F5-C deterministic development baseline: exactly one "
                    "iron-extraction Option"
                ),
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(
                plan,
                grant=grant,
                scope=scope,
            )
            if not validation.allowed:
                raise RuntimeError(
                    "F5-C A2 validation refused: "
                    + json.dumps(validation.to_dict(),sort_keys=True)
                )

            record.update({
                "status":"grant_issued_pending_execution",
                "option_plan":plan.to_dict(),
                "scope":scope.to_dict(),
                "grant":grant.to_dict(),
                "grant_validation":validation.to_dict(),
                "measurement_before":before,
                "grant_issued":True,
                "option_execution_attempts":0,
            })
            _atomic_json(artifact,record)

            record["evolution_before_execute"]=_assert_evolution_off(
                service_state_reader
            )
            lease_before=lease.active_attestation()
            if lease_before["scope_id"]!=scope.world_lease_id:
                raise RuntimeError("F5-C WorldLease scope changed before execution")
            record["world_lease_before_execute"]=lease_before
            record["option_execution_attempts"]=1
            _atomic_json(artifact,record)

            execution=bridge.execute_a2(
                plan,
                grant=grant,
                scope=scope,
                executor=executor,
                measure=lambda prepared:_namespace_measure(namespace,prepared),
                tick_source=env,
                use_checkpoint_for_action=True,
            )
            after=_namespace_measure(namespace,plan.prepared)
            result=execution.result
            accepted=(
                execution.executed
                and result is not None
                and result.status.value=="accepted"
                and result.changed_world is True
            )
            capability_gate={
                "resource_patch_valid":after["resource_patch_valid"] is True,
                "drill_operational":after["drill_operational"] is True,
                "iron_ore_produced":after["iron_ore_produced"]>0,
                "destination_reachable":after["destination_reachable"] is True,
                "production_positive_during_validation_window":(
                    after["production_positive_during_validation_window"] is True
                ),
            }
            survival_gate={
                "previously_promoted":[],
                "regressed":[],
                "passed":True,
            }
            interventions=executor.intervention_snapshot()
            record.update({
                "status":"completed" if accepted else "rejected",
                "finished_at":utc_now(),
                "option_execution":execution.to_dict(),
                "measurement_final":after,
                "capability_gate":capability_gate,
                "survival_gate":survival_gate,
                "capability_promoted":(
                    "iron_extraction"
                    if accepted and all(capability_gate.values())
                    else None
                ),
                "transaction_committed":accepted,
                "rollback_observed":not accepted,
                "interventions":interventions,
                "human_intervention_count":0,
                "external_resource_injection":False,
                "trajectory":_trajectory(
                    plan=plan,
                    execution=execution,
                    before=before,
                    after=after,
                ),
            })
            if accepted and not all(capability_gate.values()):
                record["status"]="failed_gate_inconsistency"
                record["capability_promoted"]=None
        except Exception as exc:  # noqa: BLE001
            record.update({
                "status":"failed",
                "finished_at":utc_now(),
                "failure":{
                    "type":type(exc).__name__,
                    "detail":str(exc),
                },
            })
            if ledger is not None and grant is not None:
                entry=ledger.get(grant.grant_id)
                record["ledger_entry_after_failure"]=(
                    None if entry is None else entry.to_dict()
                )
        finally:
            if env is not None:
                env.close()
            _atomic_json(artifact,record)
    return record


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--seed",type=int,default=DEFAULT_SEED)
    parser.add_argument(
        "--option-seconds",
        type=int,
        default=DEFAULT_OPTION_SECONDS,
    )
    parser.add_argument(
        "--grant-ttl-seconds",
        type=int,
        default=DEFAULT_GRANT_TTL_SECONDS,
    )
    parser.add_argument("--artifact",type=Path,default=None)
    parser.add_argument("--ledger",type=Path,default=DEFAULT_LEDGER)
    args=parser.parse_args()

    artifact=args.artifact or artifact_for_seed(args.seed)
    revision=code_revision()
    if not args.execute:
        payload=preflight_f5c(
            seed=args.seed,
            artifact=artifact,
            revision=revision,
        )
        print(json.dumps(payload,indent=2,sort_keys=True))
        return 0

    payload=run_f5c(
        seed=args.seed,
        option_seconds=args.option_seconds,
        grant_ttl_seconds=args.grant_ttl_seconds,
        artifact=artifact,
        ledger_path=args.ledger,
        revision=revision,
    )
    print(json.dumps(payload,indent=2,sort_keys=True,default=str))
    return 0 if payload.get("status")=="completed" else 1


if __name__=="__main__":
    raise SystemExit(main())
