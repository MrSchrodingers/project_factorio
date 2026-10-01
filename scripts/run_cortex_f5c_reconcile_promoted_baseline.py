#!/usr/bin/env python3
"""Bounded technical reconciliation of the promoted F5-C physical baseline."""

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
    SCIENCE_BUFFER_MIN,
    compose_rollback_recovery_option,
)
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_exact_craft_tool,
    bind_exact_item_deposit_tool,
    bind_exact_item_inspect_tool,
    bind_exact_item_transfer_tool,
    bind_exact_place_tool,
    bind_exact_resource_mining_tool,
    bind_fast_reposition_tool,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_promoted_baseline_recovery_v1"
DEFAULT_OPTION_SECONDS=240
DEFAULT_GRANT_TTL_SECONDS=300
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
DASHBOARD_BUILD_INFO=Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_promoted_baseline_recovery"
OWNER="run_cortex_f5c_reconcile_promoted_baseline"

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


def _physical_fingerprint(rows: Any) -> tuple[tuple[str,float,float],...]:
    out=[]
    if not isinstance(rows,list):
        return ()
    for row in rows:
        if not isinstance(row,Mapping):
            continue
        name=row.get("name")
        if not isinstance(name,str) or name=="character":
            continue
        pos=row.get("position")
        if not isinstance(pos,Mapping):
            continue
        try:
            x=round(float(pos["x"]),3); y=round(float(pos["y"]),3)
        except (KeyError,TypeError,ValueError):
            continue
        out.append((name,x,y))
    return tuple(sorted(out))


def _position_from(row: Mapping[str,Any],name: str) -> tuple[float,float]:
    value=row.get(name)
    if not isinstance(value,Mapping):
        raise TypeError(f"accepted position unavailable: {name}")
    return float(value["x"]),float(value["y"])


def _promoted_spec(
    state: Mapping[str,Any],
) -> tuple[int,dict[str,tuple[float,float]],dict[str,str]]:
    protocol=state.get("phase5_protocol")
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(protocol,Mapping) or not isinstance(baseline,Mapping):
        raise TypeError("F5 phase-state baseline/protocol unavailable")
    expected=[
        "iron_extraction","coal_self_sufficiency","iron_smelting",
        "steam_power","copper_chain","automation_science",
    ]
    if protocol.get("achieved_capabilities")!=expected:
        raise RuntimeError("baseline recovery requires exactly six promoted capabilities")
    if protocol.get("next_capability")!="powered_manufacturing":
        raise RuntimeError("baseline recovery only applies at powered_manufacturing")
    seed=baseline.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5 seed unavailable")

    coal_row=baseline.get("coal_self_sufficiency")
    copper_row=baseline.get("copper_chain")
    auto_row=baseline.get("automation_science")
    if not all(isinstance(row,Mapping) for row in (coal_row,copper_row,auto_row)):
        raise TypeError("promoted recovery artifacts unavailable")
    coal_artifact=Path(str(coal_row["artifact_path"]))
    copper_artifact=Path(str(copper_row["artifact_path"]))
    auto_artifact=Path(str(auto_row["artifact_path"]))
    coal=_load(coal_artifact)
    copper=_load(copper_artifact)
    auto=_load(auto_artifact)
    for payload,name in ((coal,"coal_self_sufficiency"),(copper,"copper_chain"),(auto,"automation_science")):
        if payload.get("status")!="completed" or payload.get("capability_promoted")!=name:
            raise RuntimeError(f"accepted {name} artifact is not promoted")

    coal_plan=coal.get("option_plan") or {}
    prepared=coal_plan.get("prepared") or {}
    operations=prepared.get("operations") or []
    params=None
    for op in operations:
        if isinstance(op,Mapping) and op.get("op")=="establish_coal_self_sufficiency":
            params=op.get("parameters")
            break
    if not isinstance(params,Mapping):
        raise TypeError("accepted coal parameters unavailable")
    target=params.get("target_position")
    if not isinstance(target,Mapping):
        raise TypeError("accepted coal target unavailable")
    coal_extractor=(float(target["x"]),float(target["y"]))
    coal_buffer=(coal_extractor[0]+0.5,coal_extractor[1]+1.5)
    coal_quarantine=(coal_extractor[0]+0.5,coal_extractor[1]-1.5)

    boot=coal_plan.get("bootstrap_resources")
    if not isinstance(boot,list):
        raise TypeError("accepted coal bootstrap resources unavailable")
    resources={}
    for row in boot:
        if not isinstance(row,Mapping):
            continue
        resource=row.get("resource")
        pos=row.get("position")
        if isinstance(resource,str) and isinstance(pos,Mapping):
            resources[resource]=(float(pos["x"]),float(pos["y"]))
    for resource in ("stone","coal","wood","iron-ore"):
        if resource not in resources:
            raise TypeError(f"accepted bootstrap resource unavailable: {resource}")

    copper_positions=(copper.get("option_plan") or {}).get("positions")
    auto_positions=(auto.get("option_plan") or {}).get("positions")
    if not isinstance(copper_positions,Mapping) or not isinstance(auto_positions,Mapping):
        raise TypeError("accepted promoted positions unavailable")

    positions={
        "stone":resources["stone"],
        "wood":resources["wood"],
        "coal_resource":resources["coal"],
        "iron_resource":resources["iron-ore"],
        "coal_extractor":coal_extractor,
        "coal_buffer":coal_buffer,
        "coal_quarantine":coal_quarantine,
        "iron_extractor":_position_from(auto_positions,"iron_extractor"),
        "iron_buffer":_position_from(auto_positions,"iron_buffer"),
        "iron_furnace":_position_from(auto_positions,"iron_furnace"),
        "boiler":_position_from(auto_positions,"boiler"),
        "copper_extractor":_position_from(auto_positions,"copper_extractor"),
        "copper_buffer":_position_from(auto_positions,"copper_buffer"),
        "copper_furnace":_position_from(copper_positions,"copper_furnace"),
    }
    artifacts={
        "coal":str(coal_artifact),
        "copper":str(copper_artifact),
        "automation_science":str(auto_artifact),
    }
    return seed,positions,artifacts


def _reference_fingerprint(seed: int) -> tuple[Path,tuple[tuple[str,float,float],...]]:
    best_path=None
    best=()
    pattern=f"cortex_f5c_continuation_{seed}_powered_manufacturing_*.json"
    for path in sorted((RUNS_DIR/"audits").glob(pattern)):
        payload=_load(path)
        raw=(payload.get("preflight") or {}).get("world_factory_fingerprint")
        if not isinstance(raw,list):
            continue
        current=[]
        for row in raw:
            if not isinstance(row,(list,tuple)) or len(row)!=3:
                continue
            current.append((str(row[0]),round(float(row[1]),3),round(float(row[2]),3)))
        candidate=tuple(sorted(current))
        if len(candidate)>len(best):
            best_path=path
            best=candidate
    if best_path is None or len(best)<30:
        raise RuntimeError("pre-corruption promoted world fingerprint unavailable")
    return best_path,best


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_reconcile_promoted_baseline_{seed}_{commit[:12]}.json"
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
        raise RuntimeError("baseline recovery requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("baseline recovery commit unavailable")
    if artifact.exists():
        raise FileExistsError("baseline recovery artifact already exists for commit")
    evolution=dict(service_state_reader())
    if evolution!={"active":"inactive","enabled":"disabled"}:
        raise RuntimeError(f"legacy evolution must remain OFF: {evolution!r}")
    if _intervention_count()!=0:
        raise RuntimeError("baseline recovery requires zero F5 interventions")
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError("baseline recovery requires no active WorldLease")
    ledger=_ledger_state(ledger_path)
    if not ledger["parent_writable"] or not ledger["file_writable"]:
        raise RuntimeError(f"authority ledger is not writable: {ledger!r}")
    runtime_build=_read_build(RUNTIME_BUILD_INFO)
    dashboard_build=_read_build(DASHBOARD_BUILD_INFO)
    if runtime_build["commit"]!=commit or dashboard_build["commit"]!=commit:
        raise RuntimeError("baseline recovery requires runtime/dashboard on current commit")

    state=_load(RUNS_DIR/"cortex_phase_state.json")
    seed,positions,artifacts=_promoted_spec(state)
    reference_path,reference=_reference_fingerprint(seed)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
    finally:
        observer.close()
    rows=snapshot.get("entities")
    current=_physical_fingerprint(rows)
    missing=tuple(sorted(set(reference)-set(current)))
    extra=tuple(sorted(set(current)-set(reference)))
    component_specs={
        "coal_extractor":(
            "burner-mining-drill",
            round(positions["coal_extractor"][0],3),
            round(positions["coal_extractor"][1],3),
        ),
        "iron_extractor":(
            "burner-mining-drill",
            round(positions["iron_extractor"][0],3),
            round(positions["iron_extractor"][1],3),
        ),
        "coal_buffer":(
            "wooden-chest",
            round(positions["coal_buffer"][0],3),
            round(positions["coal_buffer"][1],3),
        ),
        "coal_quarantine":(
            "wooden-chest",
            round(positions["coal_quarantine"][0],3),
            round(positions["coal_quarantine"][1],3),
        ),
        "iron_buffer":(
            "wooden-chest",
            round(positions["iron_buffer"][0],3),
            round(positions["iron_buffer"][1],3),
        ),
        "iron_furnace":(
            "stone-furnace",
            round(positions["iron_furnace"][0],3),
            round(positions["iron_furnace"][1],3),
        ),
        "copper_furnace":(
            "stone-furnace",
            round(positions["copper_furnace"][0],3),
            round(positions["copper_furnace"][1],3),
        ),
    }
    reverse_specs={value:name for name,value in component_specs.items()}
    if extra:
        raise RuntimeError(f"baseline recovery refuses unexpected extra entities: {extra!r}")
    unknown_missing=tuple(row for row in missing if row not in reverse_specs)
    if unknown_missing:
        raise RuntimeError(
            "baseline recovery refuses missing entities outside recoverable set: "
            f"{unknown_missing!r}"
        )
    missing_components=tuple(sorted(reverse_specs[row] for row in missing))
    if not missing_components:
        raise RuntimeError("promoted baseline already matches reference fingerprint")
    copper_buffer=_entity_at(rows,name="wooden-chest",position=positions["copper_buffer"])
    if copper_buffer is None:
        raise RuntimeError("automation-science buffer missing during baseline recovery")
    if _contents_count(copper_buffer,"automation-science-pack")<SCIENCE_BUFFER_MIN:
        raise RuntimeError("persistent automation-science buffer is not intact")

    return {
        "status":"preflight_pass",
        "mode":"technical_rollback_recovery",
        "promotion_credit":False,
        "base_seed":seed,
        "code_revision":revision,
        "promoted_artifacts":artifacts,
        "reference_fingerprint_artifact":str(reference_path),
        "reference_factory_fingerprint":[list(row) for row in reference],
        "current_factory_fingerprint":[list(row) for row in current],
        "missing_factory_entities":[list(row) for row in missing],
        "missing_components":list(missing_components),
        "positions":{
            name:{"x":pos[0],"y":pos[1]}
            for name,pos in positions.items()
        },
        "world_entity_count":snapshot.get("entity_count"),
        "evolution":evolution,
        "intervention_count":0,
        "world_lease_state":lease,
        "authority_ledger":ledger,
        "runtime_build_info":runtime_build,
        "dashboard_build_info":dashboard_build,
        "world_reset":False,
        "external_resource_injection":False,
        "quarantine_recreated_empty":True,
        "grant_issued":False,
        "option_executed_live":False,
    }


def _option_request(*,run_id: str,commit: str) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:promoted-baseline-recovery",
        kind=OptionKind.RESTORE_PROMOTED_ENTITY,
        goal="reconcile rollback-damaged promoted physical baseline without capability credit",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="scripts.run_cortex_f5c_reconcile_promoted_baseline",
            code_revision=commit,run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=DEFAULT_OPTION_SECONDS*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:promoted-baseline-recovery-action",
        family=ActionFamily.PLACEMENT,
        intent="restore exact promoted baseline from endogenous live-world materials",
        provenance=ActionProvenance(
            requested_by="f5-c-technical-recovery",
            source_component="scripts.run_cortex_f5c_reconcile_promoted_baseline",
            code_revision=commit,run_id=run_id,
        ),
        requires=("six_promoted_capabilities","pre_corruption_world_fingerprint"),
        provides=("restored_promoted_physical_baseline",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    del prepared
    values={
        name:bool(getattr(namespace,f"cortex_{name}",False))
        for name in (
            "promoted_baseline_restored",
            "iron_extraction_restored",
            "iron_smelting_restored",
            "coal_stock_recovered",
            "copper_smelting_restored",
            "science_buffer_intact",
        )
    }
    for name in (
        "recovery_stone_harvested",
        "recovery_coal_harvested",
        "recovery_iron_ore_harvested",
        "recovery_wood_harvested",
        "recovery_iron_plates_ready",
        "recovery_coal_stock_final",
        "recovery_iron_stock_final",
        "recovery_copper_plate_count",
        "recovery_science_buffer",
    ):
        values[name]=float(getattr(namespace,f"cortex_{name}",0) or 0)
    return values


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
        f"cortex-f5c-promoted-baseline-recovery-{seed}-"
        +datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "mode":"technical_rollback_recovery",
        "promotion_credit":False,
        "capability":None,
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
            record["fle_transactional_resource_mining_tool"]=bind_exact_resource_mining_tool(env)
            record["fle_transactional_item_transfer_tool"]=bind_exact_item_transfer_tool(env)
            record["fle_transactional_item_deposit_tool"]=bind_exact_item_deposit_tool(env)
            record["fle_exact_item_inspect_tool"]=bind_exact_item_inspect_tool(env)
            record["fle_transactional_craft_tool"]=bind_exact_craft_tool(env)
            record["fle_transactional_place_tool"]=bind_exact_place_tool(env)
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,"arena":ARENA,
                    "stage":"promoted_baseline_recovery","progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace
            observer=FactorioObserver()
            try:
                attached=observer.snapshot()
            finally:
                observer.close()
            attached_fp=_physical_fingerprint(attached.get("entities"))
            expected_current=tuple(tuple(row) for row in pf["current_factory_fingerprint"])
            if attached_fp!=expected_current:
                raise RuntimeError("FLE attachment changed physical WORLD before recovery A2")

            raw=pf["positions"]
            positions={
                name:(float(row["x"]),float(row["y"]))
                for name,row in raw.items()
            }
            wood_anchor=positions["wood"]
            wood_check=instance.rcon_client.send_command(
                "/c local p=storage.agent_characters[1]; local s=p.surface; "
                f"local q={{x={wood_anchor[0]},y={wood_anchor[1]}}}; "
                "local best=nil; local bestd=nil; "
                "for _,e in pairs(s.find_entities_filtered{position=q,radius=24,type='tree'}) do "
                "if e.valid and e.minable then "
                "local dx=e.position.x-q.x; local dy=e.position.y-q.y; "
                "local d=dx*dx+dy*dy; "
                "if bestd==nil or d<bestd then best=e; bestd=d end end end; "
                "if best then rcon.print('WOOD_POS='..best.position.x..','..best.position.y) end"
            )
            marker="WOOD_POS="
            if marker not in str(wood_check):
                raise RuntimeError("no live wood within accepted bootstrap radius")
            raw_wood=str(wood_check).split(marker,1)[1].splitlines()[0].strip()
            try:
                wood_x,wood_y=(float(value) for value in raw_wood.split(",",1))
            except (TypeError,ValueError) as exc:
                raise RuntimeError("invalid live wood recovery position") from exc
            if (
                (wood_x-wood_anchor[0])**2
                +(wood_y-wood_anchor[1])**2
                > 24.0001**2
            ):
                raise RuntimeError("resolved wood escaped accepted bootstrap radius")
            positions["wood"]=(wood_x,wood_y)
            record["wood_anchor"]={"x":wood_anchor[0],"y":wood_anchor[1]}
            record["wood_harvest_position"]={"x":wood_x,"y":wood_y}
            option=_option_request(run_id=run_id,commit=commit)
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_rollback_recovery_option(
                option,
                action_request=action,
                positions=positions,
                missing_components=tuple(str(value) for value in pf["missing_components"]),
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"promoted baseline recovery Option refused: {refusal}")
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
                reason="F5-C technical reconciliation of rollback-damaged promoted baseline",
                ttl_seconds=DEFAULT_GRANT_TTL_SECONDS,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "promoted baseline recovery A2 validation refused: "
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
            final_fp=_physical_fingerprint(final_world.get("entities"))
            reference=tuple(tuple(row) for row in pf["reference_factory_fingerprint"])
            missing_after=tuple(sorted(set(reference)-set(final_fp)))
            extra_after=tuple(sorted(set(final_fp)-set(reference)))
            gate={
                "promoted_baseline_restored":after["promoted_baseline_restored"] is True,
                "iron_extraction_restored":after["iron_extraction_restored"] is True,
                "iron_smelting_restored":after["iron_smelting_restored"] is True,
                "coal_stock_recovered":after["coal_stock_recovered"] is True,
                "copper_smelting_restored":after["copper_smelting_restored"] is True,
                "science_buffer_intact":after["science_buffer_intact"] is True,
                "baseline_signature_restored":not missing_after and not extra_after,
                "promotion_credit":False,
                "external_resource_injection":False,
                "quarantine_recreated_empty":True,
            }
            completed=accepted and all(
                bool(gate[name])
                for name in (
                    "promoted_baseline_restored",
                    "iron_extraction_restored",
                    "iron_smelting_restored",
                    "coal_stock_recovered",
                    "copper_smelting_restored",
                    "science_buffer_intact",
                    "baseline_signature_restored",
                )
            )
            record.update({
                "status":"completed" if completed else "rejected",
                "finished_at":utc_now(),
                "option_execution":execution.to_dict(),
                "measurement_final":after,
                "recovery_gate":gate,
                "missing_after_recovery":[list(row) for row in missing_after],
                "extra_after_recovery":[list(row) for row in extra_after],
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
        raise RuntimeError("baseline recovery code revision unavailable")
    state=_load(RUNS_DIR/"cortex_phase_state.json")
    seed,_,_=_promoted_spec(state)
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
