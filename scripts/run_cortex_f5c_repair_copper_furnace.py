#!/usr/bin/env python3
"""Bounded technical recovery of the promoted F5-C copper furnace.

This runner grants no capability credit. It is eligible only when the accepted
copper-chain evidence requires the exact furnace but live WORLD has lost that
entity. Replacement material is harvested from the same endogenous stone
resource recorded by the accepted copper-chain Option.
"""

from __future__ import annotations

import argparse
import hashlib
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
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.rollback_recovery_option import (
    STONE_REQUIRED,
    compose_rollback_recovery_option,
)
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_exact_resource_mining_tool,
    bind_fast_reposition_tool,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_copper_furnace_recovery_v1"
DEFAULT_OPTION_SECONDS=20
DEFAULT_GRANT_TTL_SECONDS=300
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
DASHBOARD_BUILD_INFO=Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_copper_furnace_recovery"
OWNER="run_cortex_f5c_repair_copper_furnace"

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


def _service_state() -> dict[str,str]:
    def run(action: str) -> str:
        done=subprocess.run(
            ["systemctl",action,"factorio-ai-evolution.service"],
            capture_output=True,text=True,timeout=10,check=False,
        )
        output=(done.stdout or done.stderr or "").strip()
        return output.splitlines()[0] if output else f"returncode:{done.returncode}"
    return {"active":run("is-active"),"enabled":run("is-enabled")}


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


def _read_build(path: Path) -> dict[str,Any]:
    value=_load(path)
    if not isinstance(value.get("commit"),str):
        raise TypeError(f"build info commit unavailable: {path}")
    return value


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


def _physical_signature(rows: Any) -> list[dict[str,Any]]:
    out=[]
    if not isinstance(rows,list):
        return out
    for row in rows:
        if not isinstance(row,Mapping):
            continue
        name=str(row.get("name") or "")
        if name=="character":
            continue
        pos=row.get("position")
        if not isinstance(pos,Mapping):
            continue
        try:
            x=float(pos["x"]); y=float(pos["y"])
        except (KeyError,TypeError,ValueError):
            continue
        out.append({"name":name,"x":x,"y":y})
    return sorted(out,key=lambda row:(row["name"],row["x"],row["y"]))


def _promoted_spec(
    state: Mapping[str,Any],
) -> tuple[int,Path,tuple[float,float],tuple[float,float]]:
    protocol=state.get("phase5_protocol")
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(protocol,Mapping) or not isinstance(baseline,Mapping):
        raise TypeError("F5 phase-state baseline/protocol unavailable")
    expected=[
        "iron_extraction","coal_self_sufficiency","iron_smelting",
        "steam_power","copper_chain","automation_science",
    ]
    if protocol.get("achieved_capabilities")!=expected:
        raise RuntimeError("copper furnace recovery requires six promoted capabilities")
    if protocol.get("next_capability")!="powered_manufacturing":
        raise RuntimeError("copper furnace recovery only applies at powered_manufacturing")
    seed=baseline.get("seed")
    copper=baseline.get("copper_chain")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5 seed unavailable")
    if not isinstance(copper,Mapping):
        raise TypeError("accepted copper_chain state unavailable")
    raw=copper.get("artifact_path")
    if not isinstance(raw,str) or not raw:
        raise TypeError("accepted copper_chain artifact unavailable")
    artifact=Path(raw)
    payload=_load(artifact)
    if (
        payload.get("status")!="completed"
        or payload.get("capability_promoted")!="copper_chain"
        or payload.get("transaction_committed") is not True
    ):
        raise RuntimeError("selected copper_chain artifact is not promoted")
    positions=(payload.get("option_plan") or {}).get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("accepted copper_chain positions unavailable")
    stone=positions.get("stone")
    furnace=positions.get("copper_furnace")
    if not isinstance(stone,Mapping) or not isinstance(furnace,Mapping):
        raise TypeError("accepted copper recovery positions unavailable")
    return (
        seed,artifact,
        (float(stone["x"]),float(stone["y"])),
        (float(furnace["x"]),float(furnace["y"])),
    )


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_repair_copper_furnace_{seed}_{commit[:12]}.json"
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
        raise RuntimeError("copper furnace recovery requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("copper furnace recovery commit unavailable")
    if artifact.exists():
        raise FileExistsError("copper furnace recovery artifact already exists for commit")
    evolution=dict(service_state_reader())
    if evolution!={"active":"inactive","enabled":"disabled"}:
        raise RuntimeError(f"legacy evolution must remain OFF: {evolution!r}")
    if _intervention_count()!=0:
        raise RuntimeError("copper furnace recovery requires zero F5 interventions")
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError("copper furnace recovery requires no active WorldLease")
    ledger=_ledger_state(ledger_path)
    if not ledger["parent_writable"] or not ledger["file_writable"]:
        raise RuntimeError(f"authority ledger is not writable: {ledger!r}")
    runtime_build=_read_build(RUNTIME_BUILD_INFO)
    dashboard_build=_read_build(DASHBOARD_BUILD_INFO)
    if runtime_build["commit"]!=commit or dashboard_build["commit"]!=commit:
        raise RuntimeError("copper recovery requires runtime/dashboard on current commit")

    state=_load(RUNS_DIR/"cortex_phase_state.json")
    seed,copper_artifact,stone,furnace=_promoted_spec(state)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
    finally:
        observer.close()
    rows=snapshot.get("entities")
    if _entity_at(rows,name="stone-furnace",position=furnace) is not None:
        raise RuntimeError("promoted copper furnace is already live; recovery refused")
    copper_extractor=_entity_at(
        rows,name="burner-mining-drill",position=(-71.0,70.0)
    )
    copper_buffer=_entity_at(
        rows,name="wooden-chest",position=(-70.5,71.5)
    )
    if copper_extractor is None or copper_buffer is None:
        raise RuntimeError("copper recovery refuses broader copper-chain loss")
    if _contents_count(copper_buffer,"automation-science-pack")<10:
        raise RuntimeError("persistent automation-science buffer is not intact")
    return {
        "status":"preflight_pass",
        "mode":"technical_rollback_recovery",
        "promotion_credit":False,
        "base_seed":seed,
        "code_revision":revision,
        "copper_artifact":str(copper_artifact),
        "positions":{
            "stone":{"x":stone[0],"y":stone[1]},
            "copper_furnace":{"x":furnace[0],"y":furnace[1]},
        },
        "stone_required":STONE_REQUIRED,
        "world_entity_count":snapshot.get("entity_count"),
        "world_physical_signature":_physical_signature(rows),
        "evolution":evolution,
        "intervention_count":0,
        "world_lease_state":lease,
        "authority_ledger":ledger,
        "runtime_build_info":runtime_build,
        "dashboard_build_info":dashboard_build,
        "world_reset":False,
        "external_resource_injection":False,
        "grant_issued":False,
        "option_executed_live":False,
    }


def _option_request(*,run_id: str,commit: str) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:copper-furnace-recovery",
        kind=OptionKind.RESTORE_PROMOTED_ENTITY,
        goal="restore exact promoted copper furnace from endogenous stone",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="scripts.run_cortex_f5c_repair_copper_furnace",
            code_revision=commit,run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=DEFAULT_OPTION_SECONDS*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:copper-furnace-recovery-action",
        family=ActionFamily.PLACEMENT,
        intent="restore promoted copper furnace at its accepted position",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="scripts.run_cortex_f5c_repair_copper_furnace",
            code_revision=commit,run_id=run_id,
        ),
        requires=("promoted_copper_chain_evidence","live_endogenous_stone"),
        provides=("restored_promoted_copper_furnace",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    del prepared
    return {
        "promoted_entity_restored":bool(
            getattr(namespace,"cortex_promoted_entity_restored",False)
        ),
        "recovery_stone_harvested":float(
            getattr(namespace,"cortex_recovery_stone_harvested",0) or 0
        ),
    }


def run_recovery(
    *,
    artifact: Path,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=code_revision()
    pf=preflight(artifact=artifact,revision=revision,ledger_path=ledger_path)
    commit=str(revision["commit"])
    seed=int(pf["base_seed"])
    run_id=(
        f"cortex-f5c-copper-furnace-recovery-{seed}-"
        +datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "mode":"technical_rollback_recovery",
        "promotion_credit":False,
        "capability":"copper_chain",
        "base_seed":seed,
        "run_id":run_id,
        "code_revision":revision,
        "preflight":pf,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "world_reset":False,
        "external_resource_injection":False,
        "human_intervention_count":0,
        "automatic_retry":False,
        "grant_issued":False,
        "option_execution_attempts":0,
        "started_at":utc_now(),
    }
    env=None
    with FactorioWorldLease(run_id=run_id,arena=ARENA,owner=OWNER) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        _write(artifact,record)
        try:
            env=attach_live_factorio_environment()
            enforce_minimum_eval_timeout(env,minimum_seconds=300)
            record["fle_transactional_reposition_tool"]=bind_fast_reposition_tool(env)
            record["fle_transactional_resource_mining_tool"]=(
                bind_exact_resource_mining_tool(env)
            )
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,"arena":ARENA,
                    "stage":"copper_furnace_recovery","progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace
            observer=FactorioObserver()
            try:
                attached=observer.snapshot()
            finally:
                observer.close()
            if _physical_signature(attached.get("entities"))!=pf["world_physical_signature"]:
                raise RuntimeError("FLE attachment changed physical WORLD before recovery A2")

            raw=pf["positions"]
            positions={
                name:(float(row["x"]),float(row["y"]))
                for name,row in raw.items()
            }
            furnace=positions["copper_furnace"]
            stone=positions["stone"]

            check=instance.rcon_client.send_command(
                "/c local p=storage.agent_characters[1]; local s=p.surface; "
                f"local f={{x={furnace[0]},y={furnace[1]}}}; "
                f"local q={{x={stone[0]},y={stone[1]}}}; "
                "rcon.print('CAN='..tostring(s.can_place_entity{name='stone-furnace',position=f,force=p.force})); "
                "local n=0; local best=nil; local bestd=nil; "
                "for _,e in pairs(s.find_entities_filtered{position=q,radius=3,name='stone',type='resource'}) do "
                "n=n+(e.amount or 0); "
                "local dx=e.position.x-q.x; local dy=e.position.y-q.y; "
                "local d=dx*dx+dy*dy; "
                "if bestd==nil or d<bestd then best=e; bestd=d end end; "
                "rcon.print('STONE='..n); "
                "if best then rcon.print('STONE_POS='..best.position.x..','..best.position.y) end"
            )
            record["live_recovery_precheck"]=str(check)
            if "CAN=true" not in str(check):
                raise RuntimeError("accepted copper furnace position is not buildable")
            marker="STONE="
            if marker not in str(check):
                raise RuntimeError("live stone resource precheck unavailable")
            raw_stone=str(check).split(marker,1)[1].splitlines()[0].strip()
            if float(raw_stone)<STONE_REQUIRED:
                raise RuntimeError("accepted endogenous stone resource is exhausted")
            position_marker="STONE_POS="
            if position_marker not in str(check):
                raise RuntimeError("no live stone resource within accepted recovery radius")
            raw_position=(
                str(check).split(position_marker,1)[1].splitlines()[0].strip()
            )
            try:
                stone_x,stone_y=(float(value) for value in raw_position.split(",",1))
            except (TypeError,ValueError) as exc:
                raise RuntimeError("invalid live stone recovery position") from exc
            stone_anchor=stone
            if (
                (stone_x-stone_anchor[0])**2
                +(stone_y-stone_anchor[1])**2
                > 9.0001
            ):
                raise RuntimeError("resolved stone escaped accepted recovery radius")
            resolved_stone=(stone_x,stone_y)
            positions["stone"]=resolved_stone
            record["stone_anchor"]={"x":stone_anchor[0],"y":stone_anchor[1]}
            record["stone_harvest_position"]={
                "x":resolved_stone[0],"y":resolved_stone[1]
            }

            option=_option_request(run_id=run_id,commit=commit)
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_rollback_recovery_option(
                option,action_request=action,positions=positions
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"copper furnace recovery Option refused: {refusal}")
            plan=composed.plan
            checkpoint=GameState.from_instance(instance)
            executor.game_state=checkpoint
            record["rollback_checkpoint_sha256"]=hashlib.sha256(
                checkpoint.to_raw().encode()
            ).hexdigest()
            before=_measure(namespace,plan.prepared)

            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(
                ledger=ledger,lease_attestor=lease.active_attestation
            )
            scope,grant=bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason="F5-C technical rollback recovery of promoted copper furnace",
                ttl_seconds=DEFAULT_GRANT_TTL_SECONDS,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "copper furnace recovery A2 validation refused: "
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
            record["option_execution_attempts"]=1
            _write(artifact,record)

            execution=bridge.execute_a2(
                plan,grant=grant,scope=scope,executor=executor,
                measure=lambda prepared:_measure(namespace,prepared),
                tick_source=env,use_checkpoint_for_action=False,
            )
            after=_measure(namespace,plan.prepared)
            result=execution.result
            accepted=(
                execution.executed and result is not None
                and result.status.value=="accepted" and result.changed_world is True
            )
            observer=FactorioObserver()
            try:
                final_world=observer.snapshot()
            finally:
                observer.close()
            restored=(
                _entity_at(
                    final_world.get("entities"),
                    name="stone-furnace",position=furnace,
                )
                is not None
            )
            gate={
                "promoted_entity_restored":(
                    after["promoted_entity_restored"] is True and restored
                ),
                "endogenous_stone_consumed":(
                    after["recovery_stone_harvested"]>=STONE_REQUIRED
                ),
                "promotion_credit":False,
                "external_resource_injection":False,
            }
            completed=accepted and all(
                bool(gate[name])
                for name in ("promoted_entity_restored","endogenous_stone_consumed")
            )
            record.update({
                "status":"completed" if completed else "rejected",
                "finished_at":utc_now(),
                "option_execution":execution.to_dict(),
                "measurement_final":after,
                "recovery_gate":gate,
                "rollback_integrity":executor.rollback_integrity_snapshot(),
                "technical_recovery_completed":completed,
                "capability_promoted":None,
                "promotion_credit":False,
                "transaction_committed":completed,
                "world_entity_count_final":final_world.get("entity_count"),
            })
            _write(artifact,record)
            return record
        except Exception as exc:  # noqa: BLE001
            record.update({
                "status":"failed",
                "finished_at":utc_now(),
                "failure":{"type":type(exc).__name__,"message":str(exc)},
                "rollback_integrity":(
                    None if env is None
                    else (
                        executor.rollback_integrity_snapshot()
                        if "executor" in locals()
                        else None
                    )
                ),
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
        raise RuntimeError("copper furnace recovery code revision unavailable")
    state=_load(RUNS_DIR/"cortex_phase_state.json")
    seed,_,_,_=_promoted_spec(state)
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(seed,commit).resolve()
    )
    if not args.execute:
        payload=preflight(artifact=artifact,revision=revision)
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_recovery(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
