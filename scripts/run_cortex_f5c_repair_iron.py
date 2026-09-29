#!/usr/bin/env python3
"""Technical recovery of a promoted F5-C iron-extraction capability.

Never resets Factorio and never promotes a new capability. It is eligible only
when phase-state already credits iron_extraction but the exact promoted
drill/chest are absent from the live WORLD.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
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
from factorio_ai_lab.cortex.live_canary import (
    resource_survey_from_overview,
    world_rows,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.resource_extraction_option import compose_resource_extraction_option
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.instrumentation.runtime import runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    enforce_minimum_eval_timeout,
    enforce_pathfinding_retry_floor,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_technical_recovery_v1"
DEFAULT_OPTION_SECONDS=60
DEFAULT_GRANT_TTL_SECONDS=300
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
ARENA="cortex_f5c_technical_recovery"
OWNER="run_cortex_f5c_repair_iron"


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


def _phase_state() -> dict[str,Any]:
    return _load(RUNS_DIR/"cortex_phase_state.json")


def _intervention_count() -> int:
    payload=_load(INTERVENTION_LEDGER)
    rows=payload.get("interventions")
    if not isinstance(rows,list):
        raise TypeError("F5 intervention ledger interventions must be a list")
    return len(rows)


def _lease_state() -> dict[str,Any]:
    return world_lease_state()


def _selected_promoted_iron(
    state: Mapping[str,Any],
) -> tuple[int,Path,tuple[float,float],tuple[float,float]]:
    protocol=state.get("phase5_protocol")
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(protocol,Mapping) or not isinstance(baseline,Mapping):
        raise TypeError("F5 phase-state baseline/protocol is unavailable")
    achieved=protocol.get("achieved_capabilities")
    if not isinstance(achieved,list) or "iron_extraction" not in achieved:
        raise RuntimeError("iron_extraction is not a promoted F5 capability")
    if baseline.get("iron_extraction_validated") is not True:
        raise RuntimeError("canonical iron_extraction evidence is not validated")
    seed=baseline.get("seed")
    raw=baseline.get("artifact_path")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("promoted iron seed is invalid")
    if not isinstance(raw,str) or not raw:
        raise TypeError("promoted iron artifact path is unavailable")
    artifact=Path(raw)
    payload=_load(artifact)
    if payload.get("capability_promoted")!="iron_extraction":
        raise RuntimeError("selected artifact did not promote iron_extraction")
    plan=payload.get("option_plan")
    prepared=plan.get("prepared") if isinstance(plan,Mapping) else None
    pf=prepared.get("preflight") if isinstance(prepared,Mapping) else None
    target=pf.get("target_position") if isinstance(pf,Mapping) else None
    if not isinstance(target,Mapping):
        raise TypeError("promoted iron target position is unavailable")
    x=float(target["x"])
    y=float(target["y"])
    return seed,artifact,(x,y),(x+0.5,y+1.5)


def _entity_at(
    rows: Any,
    *,
    name: str,
    position: tuple[float,float],
    tolerance: float=0.25,
) -> bool:
    if not isinstance(rows,list):
        return False
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
            return True
    return False


def _physical_entity_signature(rows: Any) -> list[dict[str,Any]]:
    """Canonical live-world signature excluding the administrative character."""
    signature: list[dict[str,Any]]=[]
    if not isinstance(rows,list):
        return signature
    for row in rows:
        if not isinstance(row,Mapping):
            continue
        name=str(row.get("name") or "")
        entity_type=str(row.get("type") or "")
        if name=="character" or entity_type=="character":
            continue
        position=row.get("position")
        if not isinstance(position,Mapping):
            continue
        try:
            x=float(position["x"])
            y=float(position["y"])
        except (KeyError,TypeError,ValueError):
            continue
        signature.append({
            "name":name,
            "type":entity_type,
            "x":x,
            "y":y,
        })
    return sorted(
        signature,
        key=lambda row:(row["name"],row["type"],row["x"],row["y"]),
    )


def _option_request(*,run_id: str,commit: str,option_seconds: int) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:iron-recovery",
        kind=OptionKind.ESTABLISH_RESOURCE_EXTRACTION,
        goal="restore promoted iron_extraction physical state after technical loss",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="scripts.run_cortex_f5c_repair_iron",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=option_seconds*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:iron-recovery-action",
        family=ActionFamily.PLACEMENT,
        intent="restore exact promoted burner iron extraction cell",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="scripts.run_cortex_f5c_repair_iron",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=("promoted_iron_evidence","observed_world_resources"),
        provides=("restored_iron_extraction",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    placement=prepared.preflight.get("placement")
    resource_tiles=placement.get("resource_tiles") if isinstance(placement,dict) else None
    iron=float(getattr(namespace,"cortex_buffer_iron_ore",0) or 0)
    return {
        "resource_patch_valid":(
            isinstance(resource_tiles,int) and not isinstance(resource_tiles,bool)
            and resource_tiles>0
        ),
        "drill_operational":bool(getattr(namespace,"cortex_drill_operational",False)),
        "iron_ore_produced":iron,
        "destination_reachable":bool(getattr(namespace,"cortex_destination_reachable",False)),
        "production_positive_during_validation_window":bool(
            getattr(namespace,"cortex_production_positive",False)
        ),
        "extractor_exists":bool(getattr(namespace,"cortex_extractor_exists",False)),
        "buffer_iron_ore":iron,
    }


def artifact_for(commit: str,seed: int) -> Path:
    return RUNS_DIR/"audits"/f"cortex_f5c_repair_iron_{seed}_{commit[:12]}.json"


def preflight(*,artifact: Path,revision: Mapping[str,Any] | None=None) -> dict[str,Any]:
    revision=dict(code_revision() if revision is None else revision)
    if revision.get("dirty") is not False:
        raise RuntimeError("technical recovery requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("technical recovery commit is unavailable")
    if artifact.exists():
        raise FileExistsError("technical recovery artifact already exists for this commit")
    state=_phase_state()
    seed,iron_artifact,extractor,buffer=_selected_promoted_iron(state)
    evolution=_service_state()
    if evolution!={"active":"inactive","enabled":"disabled"}:
        raise RuntimeError(f"legacy evolution must remain OFF: {evolution!r}")
    if _intervention_count()!=0:
        raise RuntimeError("technical recovery requires zero F5 interventions")
    lease=_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError("technical recovery requires no active WorldLease")
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
    finally:
        observer.close()
    rows=snapshot.get("entities")
    if (
        _entity_at(rows,name="burner-mining-drill",position=extractor)
        and _entity_at(rows,name="wooden-chest",position=buffer)
    ):
        raise RuntimeError("promoted iron entities are already live; repair refused")
    return {
        "status":"preflight_pass",
        "mode":"technical_recovery",
        "promotion_credit":False,
        "seed":seed,
        "code_revision":revision,
        "iron_artifact":str(iron_artifact),
        "promoted_extractor_position":{"x":extractor[0],"y":extractor[1]},
        "promoted_buffer_position":{"x":buffer[0],"y":buffer[1]},
        "world_entity_count":snapshot.get("entity_count"),
        "world_physical_signature":_physical_entity_signature(rows),
        "world_reset":False,
        "external_resource_injection":False,
        "grant_issued":False,
        "option_executed_live":False,
        "intervention_count":0,
        "evolution":evolution,
    }


def run_repair(
    *,
    artifact: Path,
    option_seconds: int=DEFAULT_OPTION_SECONDS,
    grant_ttl_seconds: int=DEFAULT_GRANT_TTL_SECONDS,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=code_revision()
    pf=preflight(artifact=artifact,revision=revision)
    commit=str(revision["commit"])
    seed=int(pf["seed"])
    target_row=pf["promoted_extractor_position"]
    target=(float(target_row["x"]),float(target_row["y"]))
    run_id=f"cortex-f5c-repair-iron-{seed}-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "mode":"technical_recovery",
        "promotion_credit":False,
        "capability":"iron_extraction",
        "seed":seed,
        "run_id":run_id,
        "code_revision":revision,
        "preflight":pf,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "world_reset":False,
        "external_resource_injection":False,
        "human_intervention_count":0,
        "option_execution_attempts":0,
        "started_at":utc_now(),
    }
    env=None
    with FactorioWorldLease(run_id=run_id,arena=ARENA,owner=OWNER) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        try:
            from fle.commons.models.game_state import GameState
            env=attach_live_factorio_environment()
            record["live_environment"]={
                "mode":"attach_existing_world",
                "clear_entities":False,
                "task_setup_called":False,
                "world_reset":False,
            }
            enforce_minimum_eval_timeout(env,minimum_seconds=300)
            record["fle_path_retry_floor"]=enforce_pathfinding_retry_floor(
                env,
                minimum_attempts=40,
            )
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"iron_extraction_recovery",
                    "progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace
            observer=FactorioObserver()
            try:
                after_attach=observer.snapshot()
                overview=observer.resource_overview(max_age_s=0.0)
            finally:
                observer.close()
            attached_rows=after_attach.get("entities")
            before_physical=pf.get("world_physical_signature")
            after_physical=_physical_entity_signature(attached_rows)
            if before_physical!=after_physical:
                raise RuntimeError(
                    "live FLE attachment changed physical WORLD before recovery A2"
                )
            record["world_after_live_attachment"]={
                "entity_count":after_attach.get("entity_count"),
                "physical_signature":after_physical,
                "character_only_delta":(
                    after_attach.get("entity_count")!=pf["world_entity_count"]
                    and before_physical==after_physical
                ),
                "world_reset":False,
            }
            resources=resource_survey_from_overview(overview)
            if not resources.tiles:
                raise RuntimeError("technical recovery resource overview is empty")
            physical_rows=world_rows(namespace,resources=False)
            footprints=runtime_entity_footprints(instance)
            option=_option_request(run_id=run_id,commit=commit,option_seconds=option_seconds)
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_resource_extraction_option(
                option,
                action_request=action,
                world_entities=physical_rows,
                resources=resources,
                footprints=footprints,
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"technical recovery Option refused: {refusal}")
            plan=composed.plan
            plan_target=plan.prepared.preflight.get("target_position")
            if not isinstance(plan_target,Mapping):
                raise TypeError("technical recovery plan target is unavailable")
            actual=(float(plan_target["x"]),float(plan_target["y"]))
            if actual!=target:
                raise RuntimeError(
                    "technical recovery target differs from promoted capability: "
                    f"{actual!r} != {target!r}"
                )
            checkpoint=GameState.from_instance(instance)
            executor.game_state=checkpoint
            record["rollback_checkpoint_sha256"]=hashlib.sha256(
                checkpoint.to_raw().encode()
            ).hexdigest()
            before=_measure(namespace,plan.prepared)
            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(ledger=ledger,lease_attestor=lease.active_attestation)
            scope,grant=bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason="F5-C technical recovery of promoted iron_extraction",
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "technical recovery A2 refused: "
                    +json.dumps(validation.to_dict(),sort_keys=True)
                )
            record.update({
                "status":"grant_issued_pending_execution",
                "option_plan":plan.to_dict(),
                "scope":scope.to_dict(),
                "grant":grant.to_dict(),
                "grant_validation":validation.to_dict(),
                "measurement_before":before,
                "grant_issued":True,
            })
            _write(artifact,record)
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
                execution.executed and result is not None
                and result.status.value=="accepted" and result.changed_world is True
            )
            gate={
                "resource_patch_valid":after["resource_patch_valid"] is True,
                "drill_operational":after["drill_operational"] is True,
                "iron_ore_produced":after["iron_ore_produced"]>0,
                "destination_reachable":after["destination_reachable"] is True,
                "production_positive_during_validation_window":(
                    after["production_positive_during_validation_window"] is True
                ),
            }
            restored=accepted and all(gate.values())
            record.update({
                "status":"completed" if restored else "rejected",
                "finished_at":utc_now(),
                "option_execution":execution.to_dict(),
                "measurement_final":after,
                "capability_gate":gate,
                "technical_recovery_completed":restored,
                "capability_promoted":None,
                "promotion_credit":False,
                "transaction_committed":restored,
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
        raise RuntimeError("technical recovery code revision is unavailable")
    state=_phase_state()
    seed,_,_,_=_selected_promoted_iron(state)
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(commit,seed).resolve()
    )
    if not args.execute:
        payload=preflight(artifact=artifact,revision=revision)
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_repair(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
