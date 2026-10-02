#!/usr/bin/env python3
"""Replay the six already-promoted F5-C contracts after infrastructure world loss.

This is technical state restoration, not scientific promotion.  The runner:
- reads only already accepted/promoted artifacts;
- rebuilds their PreparedStructuralAction contracts under current provenance;
- issues one fresh bounded A2 grant per replayed Option;
- executes the same v4->v9 structural compilers and measurement probes;
- saves the world after every accepted step;
- never edits phase-state or grants new capability credit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fle.commons.models.game_state import GameState

SCRIPT_DIR=Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0,str(SCRIPT_DIR))

import run_cortex_f5c_automation_science as automation_runner
import run_cortex_f5c_coal_self_sufficiency as coal_runner
import run_cortex_f5c_copper_chain as copper_runner
import run_cortex_f5c_deterministic_baseline as iron_runner
import run_cortex_f5c_iron_smelting as smelting_runner
import run_cortex_f5c_steam_power as steam_runner

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.structural_execute import (
    execution_guard_conditions,
    prepared_postconditions,
)
from factorio_ai_lab.cortex.structural_prepare import (
    PreparedStructuralAction,
    StructuralOperation,
)
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_fast_reposition_tool,
    enforce_minimum_eval_timeout,
    enforce_pathfinding_retry_floor,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_promoted_baseline_replay_v1"
ARENA="cortex_f5c_promoted_baseline_replay"
OWNER="run_cortex_f5c_replay_promoted_baseline"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
DASHBOARD_BUILD_INFO=Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")
GRANT_TTL_SECONDS=600
FLE_INIT_SCRIPT_ORDER=(
    "lualib_util",
    "utils",
    "alerts",
    "connection_points",
    "recipe_fluid_connection_mappings",
    "serialize",
    "serialize_direction_fix",
)

CAPABILITY_ORDER=(
    "iron_extraction",
    "coal_self_sufficiency",
    "iron_smelting",
    "steam_power",
    "copper_chain",
    "automation_science",
)
OPTION_KIND={
    "iron_extraction":OptionKind.ESTABLISH_RESOURCE_EXTRACTION,
    "coal_self_sufficiency":OptionKind.ESTABLISH_COAL_SELF_SUFFICIENCY,
    "iron_smelting":OptionKind.ESTABLISH_IRON_SMELTING,
    "steam_power":OptionKind.ESTABLISH_STEAM_POWER,
    "copper_chain":OptionKind.ESTABLISH_COPPER_CHAIN,
    "automation_science":OptionKind.ESTABLISH_AUTOMATION_SCIENCE,
}
MEASURE: dict[str,Callable[[Any,Any],dict[str,Any]]]={
    "iron_extraction":iron_runner._namespace_measure,
    "coal_self_sufficiency":coal_runner._namespace_measure,
    "iron_smelting":smelting_runner._measure,
    "steam_power":steam_runner._measure,
    "copper_chain":copper_runner._measure,
    "automation_science":automation_runner._measure,
}


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
    import subprocess

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


def _accepted_artifacts(state: Mapping[str,Any]) -> dict[str,Path]:
    baseline=state.get("phase5_deterministic_baseline")
    protocol=state.get("phase5_protocol")
    if not isinstance(baseline,Mapping) or not isinstance(protocol,Mapping):
        raise TypeError("F5 baseline/protocol unavailable")
    expected=list(CAPABILITY_ORDER)
    if protocol.get("achieved_capabilities")!=expected:
        raise RuntimeError(
            "technical replay requires exactly the six historically promoted "
            f"capabilities, got {protocol.get('achieved_capabilities')!r}"
        )
    if protocol.get("next_capability")!="powered_manufacturing":
        raise RuntimeError("technical replay only applies before powered_manufacturing")
    seed=baseline.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5 baseline seed unavailable")

    raw_paths={
        "iron_extraction":baseline.get("artifact_path"),
        "coal_self_sufficiency":(
            baseline.get("coal_self_sufficiency") or {}
        ).get("artifact_path"),
        "iron_smelting":(
            baseline.get("iron_smelting") or {}
        ).get("artifact_path"),
        "steam_power":(
            baseline.get("steam_power") or {}
        ).get("artifact_path"),
        "copper_chain":(
            baseline.get("copper_chain") or {}
        ).get("artifact_path"),
        "automation_science":(
            baseline.get("automation_science") or {}
        ).get("artifact_path"),
    }
    paths: dict[str,Path]={}
    for capability in CAPABILITY_ORDER:
        raw=raw_paths[capability]
        if not isinstance(raw,str) or not raw:
            raise TypeError(f"accepted artifact path missing for {capability}")
        path=Path(raw)
        payload=_load(path)
        promoted=payload.get("capability_promoted")
        gate=payload.get("capability_gate")
        survival=payload.get("survival_gate")
        validated=(
            payload.get("transaction_committed") is True
            and isinstance(gate,Mapping)
            and bool(gate)
            and all(value is True for value in gate.values())
            and isinstance(survival,Mapping)
            and survival.get("passed") is True
            and not (survival.get("regressed") or [])
        )
        if (
            payload.get("status")!="completed"
            or promoted!=capability
            or validated is not True
        ):
            raise RuntimeError(
                f"artifact for {capability} is not accepted/promoted: {path}"
            )
        plan=payload.get("option_plan")
        if not isinstance(plan,Mapping) or not isinstance(plan.get("prepared"),Mapping):
            raise TypeError(f"accepted artifact lacks frozen plan for {capability}")
        paths[capability]=path
    return paths


def _baseline_seed(state: Mapping[str,Any]) -> int:
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5 baseline unavailable")
    seed=baseline.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5 baseline seed unavailable")
    return seed


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


def _contents_count(row: Mapping[str,Any] | None,item: str) -> float:
    if row is None:
        return 0.0
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


def _promoted_positions(artifacts: Mapping[str,Path]) -> dict[str,tuple[float,float]]:
    automation=_load(artifacts["automation_science"])
    positions=(automation.get("option_plan") or {}).get("positions")
    if not isinstance(positions,Mapping):
        raise TypeError("automation-science accepted positions unavailable")
    out: dict[str,tuple[float,float]]={}
    for key in (
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","boiler","steam_engine",
        "copper_extractor","copper_buffer","copper_furnace",
    ):
        raw=positions.get(key)
        if not isinstance(raw,Mapping):
            raise TypeError(f"accepted promoted position unavailable: {key}")
        out[key]=(float(raw["x"]),float(raw["y"]))

    coal=_load(artifacts["coal_self_sufficiency"])
    coal_plan=coal.get("option_plan") or {}
    prepared=coal_plan.get("prepared") or {}
    operations=prepared.get("operations") or []
    coal_target=None
    for op in operations:
        if isinstance(op,Mapping) and op.get("op")=="establish_coal_self_sufficiency":
            params=op.get("parameters")
            if isinstance(params,Mapping):
                coal_target=params.get("target_position")
            break
    if not isinstance(coal_target,Mapping):
        raise TypeError("coal target unavailable")
    target=(float(coal_target["x"]),float(coal_target["y"]))
    out["coal_quarantine"]=(target[0]+0.5,target[1]-1.5)
    return out


def _detect_replay_prefix(
    snapshot: Mapping[str,Any],
    positions: Mapping[str,tuple[float,float]],
) -> int:
    rows=snapshot.get("entities")
    requirements=(
        (
            ("burner-mining-drill","iron_extractor"),
            ("wooden-chest","iron_buffer"),
        ),
        (
            ("burner-mining-drill","coal_extractor"),
            ("wooden-chest","coal_buffer"),
            ("wooden-chest","coal_quarantine"),
        ),
        (("stone-furnace","iron_furnace"),),
        (
            ("boiler","boiler"),
            ("steam-engine","steam_engine"),
        ),
        (
            ("burner-mining-drill","copper_extractor"),
            ("wooden-chest","copper_buffer"),
            ("stone-furnace","copper_furnace"),
        ),
    )
    completeness=[
        all(
            _entity_at(rows,name=name,position=positions[key]) is not None
            for name,key in group
        )
        for group in requirements
    ]
    seen_gap=False
    prefix=0
    for index,complete in enumerate(completeness,start=1):
        if not complete:
            seen_gap=True
            continue
        if seen_gap:
            raise RuntimeError(
                "technical replay found a non-contiguous physical capability prefix"
            )
        prefix=index

    copper_buffer=_entity_at(
        rows,
        name="wooden-chest",
        position=positions["copper_buffer"],
    )
    science=_contents_count(copper_buffer,"automation-science-pack")
    if science>=10:
        if prefix!=5:
            raise RuntimeError(
                "automation science buffer exists without complete promoted prefix"
            )
        prefix=6
    return prefix


def _factory_fingerprint(
    snapshot: Mapping[str,Any],
) -> tuple[tuple[str,float,float],...]:
    rows=snapshot.get("entities")
    if not isinstance(rows,list):
        return ()
    result=[]
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
            result.append((
                name,
                round(float(pos["x"]),3),
                round(float(pos["y"]),3),
            ))
        except (KeyError,TypeError,ValueError):
            continue
    return tuple(sorted(result))


def _reference_fingerprint(seed: int) -> tuple[Path,tuple[tuple[str,float,float],...]]:
    best_path=None
    best: tuple[tuple[str,float,float],...]=()
    for path in sorted(
        (RUNS_DIR/"audits").glob(
            f"cortex_f5c_continuation_{seed}_powered_manufacturing_*.json"
        )
    ):
        payload=_load(path)
        raw=(payload.get("preflight") or {}).get("world_factory_fingerprint")
        if not isinstance(raw,list):
            continue
        candidate=tuple(sorted(
            (str(row[0]),round(float(row[1]),3),round(float(row[2]),3))
            for row in raw
            if isinstance(row,(list,tuple)) and len(row)==3
        ))
        if len(candidate)>len(best):
            best_path=path
            best=candidate
    if best_path is None or not best:
        raise RuntimeError("pre-powered-manufacturing reference fingerprint unavailable")
    return best_path,best


def _latest_prior_replay_progress(seed: int,current_artifact: Path) -> int:
    best=0
    pattern=f"cortex_f5c_promoted_baseline_replay_{seed}_*.json"
    for path in sorted((RUNS_DIR/"audits").glob(pattern)):
        if path.resolve()==current_artifact.resolve():
            continue
        try:
            payload=_load(path)
        except (OSError,TypeError,ValueError,json.JSONDecodeError):
            continue
        steps=payload.get("steps")
        if not isinstance(steps,list):
            continue
        prefix=0
        for row in steps:
            if not isinstance(row,Mapping) or row.get("status")!="accepted":
                break
            prefix+=1
        best=max(best,prefix)
    return best


def _current_build(path: Path) -> dict[str,Any]:
    payload=_load(path)
    if not isinstance(payload.get("commit"),str):
        raise TypeError(f"build commit unavailable: {path}")
    return payload


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_promoted_baseline_replay_{seed}_{commit[:12]}.json"
    )


@dataclass(frozen=True)
class ReplayOptionPlan:
    request: OptionRequest
    action_request: ActionRequest
    prepared: PreparedStructuralAction
    termination_conditions: tuple[Any,...]
    capability: str
    source_artifact: str

    def to_dict(self) -> dict[str,Any]:
        return {
            "request":self.request.to_dict(),
            "action_request":self.action_request.to_dict(),
            "prepared":self.prepared.to_dict(),
            "termination_conditions":[
                condition.to_dict() for condition in self.termination_conditions
            ],
            "technical_replay":True,
            "promotion_credit":False,
            "capability":self.capability,
            "source_artifact":self.source_artifact,
            "world_mutation":False,
            "execute_authorized":False,
        }


def _plan_from_artifact(
    *,
    capability: str,
    artifact: Path,
    commit: str,
    run_id: str,
) -> ReplayOptionPlan:
    payload=_load(artifact)
    frozen=payload["option_plan"]
    raw_prepared=frozen["prepared"]
    original_request=frozen["request"]
    original_action=frozen["action_request"]

    option_id=f"{run_id}:{capability}:replay"
    action_id=f"{option_id}:action"
    provenance=ActionProvenance(
        requested_by="f5-c-technical-replay",
        source_component="scripts.run_cortex_f5c_replay_promoted_baseline",
        code_revision=commit,
        run_id=run_id,
    )
    action_provenance=ActionProvenance(
        requested_by="f5-c-technical-replay",
        source_component="scripts.run_cortex_f5c_replay_promoted_baseline",
        code_revision=commit,
        run_id=run_id,
        parent_action_id=option_id,
    )

    operations=tuple(
        StructuralOperation(
            op=str(row["op"]),
            parameters=dict(row.get("parameters") or {}),
        )
        for row in raw_prepared["operations"]
    )
    replay_preflight=dict(raw_prepared.get("preflight") or {})
    replay_preflight.update({
        "technical_replay":True,
        "promotion_credit":False,
        "source_promoted_artifact":str(artifact),
        "source_promoted_commit":(
            (payload.get("code_revision") or {}).get("commit")
            or (original_request.get("provenance") or {}).get("code_revision")
        ),
    })
    prepared=PreparedStructuralAction(
        action_id=action_id,
        family=ActionFamily(str(raw_prepared["family"])),
        intent=str(raw_prepared["intent"]),
        binding=str(raw_prepared["binding"]),
        purpose=str(raw_prepared["purpose"]),
        contract_version=str(raw_prepared["contract_version"]),
        operations=operations,
        measurement_keys=tuple(str(x) for x in raw_prepared["measurement_keys"]),
        preflight=replay_preflight,
    )
    ticks=int(original_request["budget"]["requested_ticks"])
    request=OptionRequest(
        option_id=option_id,
        kind=OPTION_KIND[capability],
        goal=f"technically replay accepted {capability} after infrastructure world loss",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=ticks),
        authority=ActionAuthority.SHADOW,
    )
    action=ActionRequest(
        action_id=action_id,
        family=ActionFamily(str(original_action["family"])),
        intent=str(original_action["intent"]),
        provenance=action_provenance,
        arguments=dict(original_action.get("arguments") or {}),
        targets=tuple(str(x) for x in original_action.get("targets") or ()),
        requires=tuple(str(x) for x in original_action.get("requires") or ()),
        provides=tuple(str(x) for x in original_action.get("provides") or ()),
    )
    termination=(
        prepared_postconditions(prepared)
        + execution_guard_conditions(prepared)
    )
    return ReplayOptionPlan(
        request=request,
        action_request=action,
        prepared=prepared,
        termination_conditions=termination,
        capability=capability,
        source_artifact=str(artifact),
    )


def preflight(
    *,
    artifact: Path,
    revision: Mapping[str,Any] | None=None,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=dict(code_revision() if revision is None else revision)
    if revision.get("dirty") is not False:
        raise RuntimeError("technical replay requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("technical replay commit unavailable")
    if artifact.exists():
        raise FileExistsError("technical replay artifact already exists for commit")

    runtime=_current_build(RUNTIME_BUILD_INFO)
    dashboard=_current_build(DASHBOARD_BUILD_INFO)
    if runtime.get("commit")!=commit or dashboard.get("commit")!=commit:
        raise RuntimeError("technical replay requires runtime/dashboard at exact HEAD")

    evolution=_service_state()
    if evolution!={"active":"inactive","enabled":"disabled"}:
        raise RuntimeError(f"legacy evolution must remain OFF: {evolution!r}")
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError(f"active WorldLease blocks technical replay: {lease}")
    if _intervention_count()!=0:
        raise RuntimeError("F5 intervention ledger must remain empty")
    ledger=_ledger_state(ledger_path)
    if not ledger["file_writable"] or not ledger["parent_writable"]:
        raise RuntimeError(f"authority ledger unavailable: {ledger!r}")

    state=_load(RUNS_DIR/"cortex_phase_state.json")
    artifacts=_accepted_artifacts(state)
    seed=_baseline_seed(state)
    positions=_promoted_positions(artifacts)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
        resources=observer.resource_overview(max_age_s=0.0)
    finally:
        observer.close()
    if snapshot.get("connected") is not True:
        raise RuntimeError("technical replay canonical WORLD unavailable")
    prefix=_detect_replay_prefix(snapshot,positions)
    prior_progress=_latest_prior_replay_progress(seed,artifact)
    if prefix>0 and prior_progress<prefix:
        raise RuntimeError(
            f"live replay prefix {prefix} lacks prior accepted replay evidence "
            f"(prior={prior_progress})"
        )
    if prior_progress>prefix:
        raise RuntimeError(
            f"previous replay reached {prior_progress} but live WORLD only shows {prefix}"
        )
    if prefix==0 and int(snapshot.get("entity_count") or 0)!=0:
        raise RuntimeError(
            "technical replay refuses a non-empty WORLD without an accepted replay prefix"
        )
    totals=resources.get("totals")
    if not isinstance(totals,Mapping):
        raise TypeError("resource overview totals unavailable")
    for resource in ("iron-ore","coal","stone","copper-ore"):
        row=totals.get(resource)
        if not isinstance(row,Mapping) or float(row.get("amount") or 0)<=0:
            raise RuntimeError(f"technical replay WORLD lacks {resource}")

    reference_path,reference=_reference_fingerprint(seed)
    return {
        "status":"preflight_pass",
        "technical_replay":True,
        "promotion_credit":False,
        "external_resource_injection":False,
        "phase_state_mutation":False,
        "world_reset":False,
        "base_seed":seed,
        "code_revision":revision,
        "runtime_build_info":runtime,
        "dashboard_build_info":dashboard,
        "evolution":evolution,
        "world_lease_state":lease,
        "authority_ledger":ledger,
        "intervention_count":0,
        "accepted_artifacts":{k:str(v) for k,v in artifacts.items()},
        "replay_prefix_before":prefix,
        "prior_replay_progress":prior_progress,
        "world_entity_count":snapshot.get("entity_count"),
        "reference_fingerprint_artifact":str(reference_path),
        "reference_factory_fingerprint":[list(row) for row in reference],
        "positions":{
            key:{"x":value[0],"y":value[1]}
            for key,value in positions.items()
        },
    }


def _quiesce_fle_storage_for_save(instance: Any) -> dict[str,Any]:
    command=r"""/sc local seen={}
local removed=0
local function scrub(value)
  if type(value)~="table" or seen[value] then return end
  seen[value]=true
  local drop={}
  for key,item in pairs(value) do
    if type(key)=="function" or type(item)=="function" then
      drop[#drop+1]=key
    elseif type(item)=="table" then
      scrub(item)
    end
  end
  for _,key in ipairs(drop) do
    value[key]=nil
    removed=removed+1
  end
end
scrub(storage)
storage.__lua_script_checksums={}
rcon.print(helpers.table_to_json({ok=true,removed_functions=removed}))
"""
    response=instance.rcon_client.send_command(command)
    payload=json.loads("" if response is None else str(response))
    if payload.get("ok") is not True:
        raise RuntimeError(f"FLE storage quiesce failed: {payload!r}")
    removed=payload.get("removed_functions")
    if not isinstance(removed,int) or isinstance(removed,bool) or removed<=0:
        raise RuntimeError(
            "FLE storage quiesce removed no Lua functions; refusing unsafe save"
        )
    return {
        "status":"quiesced",
        "removed_functions":removed,
        "checksums_cleared":True,
    }


def _rehydrate_fle_runtime(instance: Any) -> dict[str,Any]:
    manager=instance.lua_script_manager
    previous_cache=bool(manager.cache_scripts)
    manager.cache_scripts=False
    try:
        manager.tool_scripts=manager.get_tools_to_load()
        manager.lib_scripts=manager.get_libs_to_load()
        manager.load_init_into_game("initialise")
        for name in FLE_INIT_SCRIPT_ORDER:
            manager.load_init_into_game(name)
        manager.setup_tools(instance)
    finally:
        manager.cache_scripts=previous_cache
    return {
        "status":"rehydrated",
        "cache_scripts_restored":previous_cache,
        "init_scripts":list(FLE_INIT_SCRIPT_ORDER),
    }


def _save_world(instance: Any,name: str) -> dict[str,Any]:
    quiesce=_quiesce_fle_storage_for_save(instance)
    response=None
    save_error=None
    try:
        response=instance.rcon_client.send_command(f"/server-save {name}")
        response_text="" if response is None else str(response).strip()
        if "Saving the map" not in response_text:
            raise RuntimeError(
                f"Factorio save command failed for {name!r}: {response_text!r}"
            )
    except Exception as exc:  # noqa: BLE001
        save_error=exc
        response_text="" if response is None else str(response).strip()

    try:
        rehydrate=_rehydrate_fle_runtime(instance)
    except Exception as rehydrate_exc:
        if save_error is not None:
            raise RuntimeError(
                f"Factorio save failed for {name!r} and FLE rehydration also failed: "
                f"save={type(save_error).__name__}: {save_error}; "
                f"rehydrate={type(rehydrate_exc).__name__}: {rehydrate_exc}"
            ) from rehydrate_exc
        raise RuntimeError(
            f"Factorio save for {name!r} did not leave a rehydratable live runtime: "
            f"{type(rehydrate_exc).__name__}: {rehydrate_exc}"
        ) from rehydrate_exc

    if save_error is not None:
        raise save_error

    return {
        "response":response_text,
        "storage_quiesce":quiesce,
        "runtime_rehydrate":rehydrate,
    }


def run_replay(
    *,
    artifact: Path,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=code_revision()
    pf=preflight(artifact=artifact,revision=revision,ledger_path=ledger_path)
    commit=str(revision["commit"])
    seed=int(pf["base_seed"])
    run_id=(
        f"cortex-f5c-promoted-baseline-replay-{seed}-"
        +datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    source_artifacts={
        key:Path(value)
        for key,value in pf["accepted_artifacts"].items()
    }
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "run_id":run_id,
        "base_seed":seed,
        "code_revision":revision,
        "preflight":pf,
        "technical_replay":True,
        "promotion_credit":False,
        "phase_state_mutation":False,
        "external_resource_injection":False,
        "world_reset":False,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "steps":[],
        "started_at":utc_now(),
    }
    env=None
    with FactorioWorldLease(run_id=run_id,arena=ARENA,owner=OWNER) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        _write(artifact,record)
        try:
            env=attach_live_factorio_environment()
            enforce_minimum_eval_timeout(env,minimum_seconds=900)
            enforce_pathfinding_retry_floor(env,minimum_attempts=40)
            record["fle_transactional_reposition_tool"]=bind_fast_reposition_tool(env)
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"promoted_baseline_replay",
                    "progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace
            executor.game_state=GameState.from_instance(instance)

            observer=FactorioObserver()
            try:
                attached=observer.snapshot()
            finally:
                observer.close()
            positions={
                key:(float(value["x"]),float(value["y"]))
                for key,value in pf["positions"].items()
            }
            prefix=_detect_replay_prefix(attached,positions)
            if prefix!=int(pf["replay_prefix_before"]):
                raise RuntimeError("live FLE attachment changed replay prefix")

            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(
                ledger=ledger,
                lease_attestor=lease.active_attestation,
            )

            for index,capability in enumerate(CAPABILITY_ORDER,start=1):
                if index<=prefix:
                    record["steps"].append({
                        "index":index,
                        "capability":capability,
                        "status":"resumed_existing",
                        "promotion_credit":False,
                    })
                    continue

                plan=_plan_from_artifact(
                    capability=capability,
                    artifact=source_artifacts[capability],
                    commit=commit,
                    run_id=run_id,
                )
                measure=MEASURE[capability]
                before=measure(namespace,plan.prepared)
                step_record={
                    "index":index,
                    "capability":capability,
                    "status":"grant_pending",
                    "source_artifact":str(source_artifacts[capability]),
                    "option_plan":plan.to_dict(),
                    "measurement_before":before,
                    "promotion_credit":False,
                }
                record["steps"].append(step_record)
                _write(artifact,record)

                scope,grant=bridge.issue_a2_grant(
                    plan,
                    experiment_id=run_id,
                    reason=(
                        "F5-C technical replay after verified infrastructure "
                        f"world loss: {capability}; no promotion credit"
                    ),
                    ttl_seconds=GRANT_TTL_SECONDS,
                )
                validation=bridge.validate_a2(plan,grant=grant,scope=scope)
                step_record.update({
                    "scope":scope.to_dict(),
                    "grant":grant.to_dict(),
                    "grant_validation":validation.to_dict(),
                })
                if not validation.allowed:
                    raise RuntimeError(
                        f"technical replay A2 validation refused for {capability}: "
                        +json.dumps(validation.to_dict(),sort_keys=True)
                    )
                step_record["status"]="executing"
                _write(artifact,record)

                execution=bridge.execute_a2(
                    plan,
                    grant=grant,
                    scope=scope,
                    executor=executor,
                    measure=lambda prepared,m=measure: m(namespace,prepared),
                    tick_source=env,
                    use_checkpoint_for_action=False,
                )
                after=measure(namespace,plan.prepared)
                result=execution.result
                accepted=(
                    execution.executed
                    and result is not None
                    and result.status.value=="accepted"
                    and result.changed_world is True
                )
                step_record.update({
                    "status":"accepted" if accepted else "rejected",
                    "option_execution":execution.to_dict(),
                    "measurement_after":after,
                    "rollback_integrity":executor.rollback_integrity_snapshot(),
                })
                if not accepted:
                    record.update({
                        "status":"rejected",
                        "finished_at":utc_now(),
                        "failed_capability":capability,
                        "technical_replay_completed":False,
                    })
                    _write(artifact,record)
                    return record

                save_name=f"cortex-f5c-replay-{index:02d}-{capability}"
                step_record["save_name"]=save_name
                save_result=_save_world(instance,save_name)
                step_record["save_response"]=save_result["response"]
                step_record["save_storage_quiesce"]=save_result["storage_quiesce"]
                step_record["save_runtime_rehydrate"]=save_result["runtime_rehydrate"]

                observer=FactorioObserver()
                try:
                    current=observer.snapshot()
                finally:
                    observer.close()
                observed_prefix=_detect_replay_prefix(current,positions)
                step_record["replay_prefix_after"]=observed_prefix
                step_record["world_entity_count_after"]=current.get("entity_count")
                if observed_prefix<index:
                    raise RuntimeError(
                        f"accepted replay step {capability} did not persist physical prefix "
                        f"{index}; observed {observed_prefix}"
                    )
                _write(artifact,record)

            observer=FactorioObserver()
            try:
                final=observer.snapshot()
            finally:
                observer.close()
            final_fp=_factory_fingerprint(final)
            reference=tuple(
                (str(row[0]),float(row[1]),float(row[2]))
                for row in pf["reference_factory_fingerprint"]
            )
            missing=tuple(sorted(set(reference)-set(final_fp)))
            extra=tuple(sorted(set(final_fp)-set(reference)))
            copper_buffer=_entity_at(
                final.get("entities"),
                name="wooden-chest",
                position=positions["copper_buffer"],
            )
            science=_contents_count(copper_buffer,"automation-science-pack")
            completed=not missing and not extra and science>=10
            record.update({
                "status":"completed" if completed else "rejected",
                "finished_at":utc_now(),
                "technical_replay_completed":completed,
                "promotion_credit":False,
                "phase_state_mutation":False,
                "final_factory_fingerprint":[list(row) for row in final_fp],
                "missing_vs_promoted_reference":[list(row) for row in missing],
                "extra_vs_promoted_reference":[list(row) for row in extra],
                "science_buffer_count":science,
                "world_entity_count_final":final.get("entity_count"),
            })
            if completed:
                record["final_save_name"]="cortex-f5c-promoted-baseline"
                final_save=_save_world(instance,record["final_save_name"])
                record["final_save_response"]=final_save["response"]
                record["final_save_storage_quiesce"]=final_save["storage_quiesce"]
                record["final_save_runtime_rehydrate"]=final_save["runtime_rehydrate"]
            _write(artifact,record)
            return record
        except Exception as exc:  # noqa: BLE001
            record.update({
                "status":"failed",
                "finished_at":utc_now(),
                "technical_replay_completed":False,
                "failure":{"type":type(exc).__name__,"message":str(exc)},
                "rollback_integrity":(
                    executor.rollback_integrity_snapshot()
                    if "executor" in locals()
                    else None
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
        raise RuntimeError("technical replay code revision unavailable")
    state=_load(RUNS_DIR/"cortex_phase_state.json")
    seed=_baseline_seed(state)
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(seed,commit).resolve()
    )
    if not args.execute:
        payload=preflight(artifact=artifact,revision=revision)
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_replay(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
