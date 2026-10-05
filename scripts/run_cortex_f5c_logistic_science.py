#!/usr/bin/env python3
"""F5-C logistic-science capability runner."""

from __future__ import annotations

import argparse
import json
import os
import re
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
from factorio_ai_lab.cortex.logistic_science_option import (
    COPPER_MINER_REFUEL,
    COPPER_PLATE_TARGET,
    DEFAULT_OPTION_SECONDS,
    ELECTRIC_COAL_MIN,
    IRON_MINER_REFUEL,
    IRON_PLATE_TARGET,
    compose_logistic_science_option,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_safe_score_tool,
    bind_tick_accurate_sleep_tool,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_logistic_science_v1"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
DASHBOARD_BUILD_INFO=Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_logistic_science"
OWNER="run_cortex_f5c_logistic_science"
DEFAULT_RUN_OPTION_SECONDS=DEFAULT_OPTION_SECONDS
DEFAULT_GRANT_TTL_SECONDS=2700
SERVER_SETTINGS=Path("/srv/factorio-ai-lab/.fle-local/config/server-settings.json")
MIN_BURNER_ORE_PER_COAL=6

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


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_continuation_{seed}_logistic_science_{commit[:12]}.json"
    )


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


def _factory_fingerprint(snapshot: Mapping[str,Any]) -> tuple[tuple[str,float,float],...]:
    rows=snapshot.get("entities")
    if not isinstance(rows,list):
        return ()
    fingerprint: list[tuple[str,float,float]]=[]
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
            x=round(float(pos["x"]),3)
            y=round(float(pos["y"]),3)
        except (KeyError,TypeError,ValueError):
            continue
        fingerprint.append((name,x,y))
    return tuple(sorted(fingerprint))


def _promoted_positions(
    state: Mapping[str,Any],
) -> tuple[int,dict[str,tuple[float,float]],Path]:
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C baseline state missing")
    seed=baseline.get("seed")
    electric=baseline.get("electric_mining")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5-C base seed missing")
    if not isinstance(electric,Mapping):
        raise TypeError("promoted electric_mining state missing")
    artifact_raw=electric.get("artifact_path")
    if not isinstance(artifact_raw,str) or not artifact_raw:
        raise TypeError("promoted electric_mining artifact path missing")
    artifact=Path(artifact_raw)
    payload=_load(artifact)
    if (
        payload.get("status")!="completed"
        or payload.get("capability_promoted")!="electric_mining"
        or payload.get("transaction_committed") is not True
    ):
        raise RuntimeError("selected electric_mining artifact is not promoted")
    preflight=payload.get("preflight")
    rows=preflight.get("positions") if isinstance(preflight,Mapping) else None
    if not isinstance(rows,Mapping):
        raise TypeError("promoted electric-mining positions unavailable")

    names=(
        "iron_extractor","iron_buffer","iron_furnace",
        "coal_extractor","coal_buffer","boiler","steam_engine","power_pole",
        "copper_extractor","copper_buffer","copper_furnace",
        "lab","assembler","electric_pole","electric_drill","electric_buffer",
    )
    positions: dict[str,tuple[float,float]]={}
    for name in names:
        row=rows.get(name)
        if not isinstance(row,Mapping):
            raise TypeError(f"promoted position unavailable: {name}")
        positions[name]=(float(row["x"]),float(row["y"]))
    return seed,positions,artifact

def _research_probe() -> dict[str,Any]:
    client=_open_control_rcon()
    try:
        command=(
            "/sc local f=game.forces.player; local out={}; "
            "local t=f.technologies['logistic-science-pack']; "
            "local r=f.recipes['logistic-science-pack']; "
            "out.researched=t and t.researched or false; "
            "out.tech_enabled=t and t.enabled or false; "
            "out.recipe_enabled=r and r.enabled or false; "
            "out.research_unit_count=t and t.research_unit_count or nil; "
            "out.research_unit_energy=t and t.research_unit_energy or nil; "
            "rcon.print(helpers.table_to_json(out))"
        )
        raw=client.send_command(command)
        parsed=json.loads("" if raw is None else str(raw))
    finally:
        client.close()
    if not isinstance(parsed,dict):
        raise TypeError("logistic-science research probe returned no object")
    if parsed.get("researched") is True:
        raise RuntimeError("Logistic Science Pack technology is already researched")
    if parsed.get("tech_enabled") is not True:
        raise RuntimeError(f"Logistic Science Pack technology unavailable: {parsed!r}")
    if int(parsed.get("research_unit_count") or 0)!=75:
        raise RuntimeError(f"unexpected logistic-science research count: {parsed!r}")
    return parsed

def preflight(
    *,
    artifact: Path,
    revision: Mapping[str,Any] | None=None,
    service_state_reader: ServiceStateReader=_service_state,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=dict(code_revision() if revision is None else revision)
    if revision.get("dirty") is not False:
        raise RuntimeError("logistic science requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("logistic science commit is unavailable")
    if artifact.exists():
        raise FileExistsError("logistic-science artifact already exists for this commit")
    evolution=_assert_evolution_off(service_state_reader)
    if _intervention_count()!=0:
        raise RuntimeError("logistic science requires zero F5 interventions")
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError("logistic science requires no active WorldLease")
    ledger=_ledger_state(ledger_path)
    if not ledger["parent_writable"] or not ledger["file_writable"]:
        raise RuntimeError(f"authority ledger is not writable: {ledger!r}")

    runtime_build=_read_build_commit(RUNTIME_BUILD_INFO)
    dashboard_build=_read_build_commit(DASHBOARD_BUILD_INFO)
    if runtime_build["commit"]!=commit or dashboard_build["commit"]!=commit:
        raise RuntimeError(
            "logistic science requires runtime/dashboard on current commit"
        )

    state=phase_state()
    protocol=state.get("phase5_protocol")
    if not isinstance(protocol,Mapping):
        raise TypeError("phase5 protocol state missing")
    achieved=protocol.get("achieved_capabilities")
    expected=[
        "iron_extraction","coal_self_sufficiency","iron_smelting",
        "steam_power","copper_chain","automation_science",
        "powered_manufacturing","electric_mining",
    ]
    if achieved!=expected:
        raise RuntimeError(
            f"logistic science requires promoted prefix {expected!r}, got {achieved!r}"
        )
    if protocol.get("next_capability")!="logistic_science":
        raise RuntimeError("logistic science is not the current F5-C frontier")

    seed,positions,electric_artifact=_promoted_positions(state)

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
        "power_pole":"small-electric-pole",
        "copper_extractor":"burner-mining-drill",
        "copper_buffer":"wooden-chest",
        "copper_furnace":"stone-furnace",
        "lab":"lab",
        "assembler":"assembling-machine-1",
        "electric_pole":"small-electric-pole",
        "electric_drill":"electric-mining-drill",
        "electric_buffer":"wooden-chest",
    }
    observed: dict[str,Mapping[str,Any]]={}
    for key,name in required_entities.items():
        row=_entity_at(rows,name=name,position=positions[key])
        if row is None:
            raise RuntimeError(
                f"promoted entity absent from WORLD: {key} at {positions[key]!r}"
            )
        observed[key]=row

    electric_coal_stock=_contents_count(observed["electric_buffer"],"coal")
    if electric_coal_stock<ELECTRIC_COAL_MIN:
        raise RuntimeError(
            f"logistic science requires {ELECTRIC_COAL_MIN} electric-mined coal, "
            f"observed {electric_coal_stock}"
        )

    iron_ore_buffer=_contents_count(observed["iron_buffer"],"iron-ore")
    copper_ore_buffer=_contents_count(observed["copper_buffer"],"copper-ore")
    ore_budget={
        "minimum_ore_per_refuel_coal":MIN_BURNER_ORE_PER_COAL,
        "iron_buffer_before":iron_ore_buffer,
        "iron_refuel_coal":IRON_MINER_REFUEL,
        "iron_guaranteed_available":(
            iron_ore_buffer+IRON_MINER_REFUEL*MIN_BURNER_ORE_PER_COAL
        ),
        "iron_target":IRON_PLATE_TARGET,
        "copper_buffer_before":copper_ore_buffer,
        "copper_refuel_coal":COPPER_MINER_REFUEL,
        "copper_guaranteed_available":(
            copper_ore_buffer+COPPER_MINER_REFUEL*MIN_BURNER_ORE_PER_COAL
        ),
        "copper_target":COPPER_PLATE_TARGET,
    }
    if ore_budget["iron_guaranteed_available"]<IRON_PLATE_TARGET:
        raise RuntimeError(
            f"logistic-science iron ore budget insufficient: {ore_budget!r}"
        )
    if ore_budget["copper_guaranteed_available"]<COPPER_PLATE_TARGET:
        raise RuntimeError(
            f"logistic-science copper ore budget insufficient: {ore_budget!r}"
        )

    promoted_network_id=observed["power_pole"].get("network_id")
    if (
        not isinstance(promoted_network_id,int)
        or isinstance(promoted_network_id,bool)
        or promoted_network_id<=0
    ):
        raise RuntimeError("promoted power pole has no live electrical network")
    for key in ("lab","assembler","electric_pole","electric_drill"):
        if observed[key].get("network_id")!=promoted_network_id:
            raise RuntimeError(
                f"promoted {key} is not on electrical network {promoted_network_id}"
            )
    research=_research_probe()
    return {
        "status":"preflight_pass",
        "base_seed":seed,
        "code_revision":revision,
        "electric_mining_artifact":str(electric_artifact),
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
        "research_probe":research,
        "electric_coal_stock":electric_coal_stock,
        "ore_budget":ore_budget,
        "promoted_network_id":promoted_network_id,
        "world_entity_count":snapshot.get("entity_count"),
        "world_factory_fingerprint":_factory_fingerprint(snapshot),
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
        "native_research_queue":True,
        "direct_research_completion_mutation":False,
        "natural_resource_transaction":"none_required",
    }

def _option_request(*,run_id: str,commit: str,seconds: int) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:logistic-science",
        kind=OptionKind.ESTABLISH_LOGISTIC_SCIENCE,
        goal=(
            "research Logistic Science Pack through endogenous automation science "
            "and prove sustainable green-science output while all eight promoted "
            "capabilities survive"
        ),
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_logistic_science",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=seconds*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:logistic-science-action",
        family=ActionFamily.CRAFT,
        intent="research and produce sustainable endogenous logistic science",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_logistic_science",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=(
            "iron_extraction","coal_self_sufficiency","iron_smelting",
            "steam_power","copper_chain","automation_science",
            "powered_manufacturing","electric_mining",
        ),
        provides=("logistic_science",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    del prepared
    boolean_names=(
        "logistic_science_output_positive",
        "inputs_endogenous",
        "all_promoted_capabilities_alive",
        "sustainability_soak_passed",
        "research_completed",
        "automation_science_survives",
        "powered_manufacturing_survives",
        "electric_mining_survives",
        "iron_extraction_survives",
        "coal_self_sufficiency_survives",
        "iron_smelting_survives",
        "steam_power_survives",
        "copper_chain_survives",
    )
    numeric_names=(
        "research_remaining_count",
        "science_batch_ready",
        "iron_plate_ready",
        "copper_plate_ready",
        "electric_coal_before",
        "electric_coal_after_accumulation",
        "logistic_belts_ready",
        "logistic_inserters_ready",
        "logistic_first_growth",
        "logistic_second_output",
        "logistic_assembler_energy",
        "logistic_assembler_electrical_id",
        "science_replenished",
        "science_buffer_after",
        "gear_output",
        "circuit_output",
        "iron_survival_growth",
        "coal_survival_growth",
        "smelting_survival_growth",
        "copper_survival_growth",
        "copper_smelting_growth",
        "electric_survival_growth",
        "steam_survival_amount",
        "steam_survival_energy",
        "electric_drill_survival_energy",
        "electric_drill_survival_electrical_id",
        "electric_pole_survival_electrical_id",
    )
    values={
        name:bool(getattr(namespace,f"cortex_{name}",False))
        for name in boolean_names
    }
    for name in numeric_names:
        raw=getattr(namespace,f"cortex_{name}",0)
        values[name]=float(raw or 0)
    return values

def _configured_autosave_interval() -> int:
    payload=_load(SERVER_SETTINGS)
    value=payload.get("autosave_interval")
    if not isinstance(value,int) or isinstance(value,bool) or value<0:
        raise RuntimeError(
            f"configured autosave_interval unavailable in {SERVER_SETTINGS}"
        )
    return value


def _open_control_rcon() -> Any:
    from factorio_rcon import RCONClient

    address=os.getenv("FACTORIO_SERVER_ADDRESS") or "127.0.0.1"
    raw_port=os.getenv("FACTORIO_SERVER_PORT")
    port=27000 if raw_port is None else int(raw_port)
    return RCONClient(address,port,"factorio")


def _autosave_interval(client: Any) -> int:
    response=client.send_command("/config get autosave-interval")
    text="" if response is None else str(response).strip()
    if "disabled" in text.lower():
        return 0
    matches=re.findall(r"\b(\d+)\b",text)
    if not matches:
        raise RuntimeError(f"unable to parse autosave interval: {text!r}")
    return int(matches[-1])


def _set_autosave_interval(client: Any,minutes: int) -> dict[str,Any]:
    if not isinstance(minutes,int) or isinstance(minutes,bool) or minutes<0:
        raise ValueError("autosave interval must be a non-negative integer")
    response=client.send_command(f"/config set autosave-interval {minutes}")
    text="" if response is None else str(response).strip()
    lowered=text.lower()
    if any(token in lowered for token in ("unknown command","error","invalid")):
        raise RuntimeError(f"failed to set autosave interval: {text!r}")
    observed=_autosave_interval(client)
    if observed!=minutes:
        raise RuntimeError(
            f"autosave interval mismatch: requested={minutes} observed={observed}"
        )
    return {
        "requested_minutes":minutes,
        "observed_minutes":observed,
        "response":text,
    }


def _ensure_factorio_unpaused(instance: Any) -> dict[str,Any]:
    response=instance.rcon_client.send_command(
        "/sc game.tick_paused = false; "
        "rcon.print(game.tick_paused and 'true' or 'false')"
    )
    text="" if response is None else str(response).strip().lower()
    if text!="false":
        raise RuntimeError(
            f"failed to unpause Factorio for powered manufacturing: {text!r}"
        )
    game_control=getattr(instance,"game_control",None)
    if game_control is not None and hasattr(game_control,"_is_paused"):
        game_control._is_paused=False
    return {"status":"unpaused","verified":True}


def _pause_factorio_for_quiesce(instance: Any) -> dict[str,Any]:
    response=instance.rcon_client.send_command(
        "/sc game.tick_paused = true; "
        "rcon.print(game.tick_paused and 'true' or 'false')"
    )
    text="" if response is None else str(response).strip().lower()
    if text!="true":
        raise RuntimeError(f"failed to pause Factorio before quiesce: {text!r}")
    game_control=getattr(instance,"game_control",None)
    if game_control is not None and hasattr(game_control,"_is_paused"):
        game_control._is_paused=True
    return {"status":"paused","verified":True}


def _quiesce_fle_storage(instance: Any) -> dict[str,Any]:
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
        raise RuntimeError("FLE storage quiesce removed no Lua functions")
    return {
        "status":"quiesced",
        "removed_functions":removed,
        "checksums_cleared":True,
    }


def _server_save(client: Any,name: str) -> str:
    response=client.send_command(f"/server-save {name}")
    text="" if response is None else str(response).strip()
    if "Saving the map" not in text:
        raise RuntimeError(f"Factorio save command failed for {name!r}: {text!r}")
    return text


def run_logistic_science(
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
        f"cortex-f5c-logistic-science-{seed}-"
        +datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "planning_stage":"starting",
        "capability":"logistic_science",
        "base_seed":seed,
        "run_id":run_id,
        "code_revision":revision,
        "preflight":pf,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "world_reset":False,
        "external_resource_injection":False,
        "native_research_queue":True,
        "direct_research_completion_mutation":False,
        "human_intervention_count":0,
        "grant_issued":False,
        "option_execution_attempts":0,
        "automatic_retry":False,
        "natural_resource_transaction":"none_required",
        "started_at":utc_now(),
    }
    env=None
    instance=None
    executor=None
    checkpoint=None
    control=None
    autosave_original=None
    with FactorioWorldLease(run_id=run_id,arena=ARENA,owner=OWNER) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        _write(artifact,record)
        try:
            control=_open_control_rcon()
            autosave_observed=_autosave_interval(control)
            autosave_original=_configured_autosave_interval()
            record["autosave_guard"]={
                "observed_before_minutes":autosave_observed,
                "configured_restore_minutes":autosave_original,
                "suspend":_set_autosave_interval(control,0),
                "status":"suspended",
            }
            _write(artifact,record)

            env=attach_live_factorio_environment()
            instance=env.unwrapped.instance
            record["fle_attach_unpause"]=_ensure_factorio_unpaused(instance)
            record["fle_eval_timeout_s"]=enforce_minimum_eval_timeout(
                env,minimum_seconds=max(2700,option_seconds+300)
            )
            record["fle_safe_score_tool"]=bind_safe_score_tool(env)
            record["fle_tick_accurate_sleep_tool"]=bind_tick_accurate_sleep_tool(env)
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"logistic_science",
                    "progress":"F5-C",
                },
            )
            namespace=instance.namespace
            observer=FactorioObserver()
            try:
                after_attach=observer.snapshot()
            finally:
                observer.close()
            if _factory_fingerprint(after_attach)!=pf["world_factory_fingerprint"]:
                raise RuntimeError(
                    "live FLE attachment changed factory entities before "
                    "logistic-science A2"
                )

            raw_positions=pf["positions"]
            if not isinstance(raw_positions,Mapping):
                raise TypeError("logistic science preflight positions missing")
            positions={}
            for name,row in raw_positions.items():
                if not isinstance(row,Mapping):
                    raise TypeError(f"logistic-science position invalid: {name}")
                positions[str(name)]=(float(row["x"]),float(row["y"]))
            option=_option_request(
                run_id=run_id,commit=commit,seconds=option_seconds
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_logistic_science_option(
                option,
                action_request=action,
                positions=positions,
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"logistic science Option refused: {refusal}")
            plan=composed.plan
            checkpoint=GameState.from_instance(instance)
            executor.game_state=checkpoint
            record["rollback_checkpoint"]=True
            before=_measure(namespace,plan.prepared)

            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(
                ledger=ledger,lease_attestor=lease.active_attestation
            )
            scope,grant=bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason=(
                    "F5-C native Logistic Science Pack research and sustainable "
                    "green-science production preserving eight incumbents"
                ),
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "logistic science A2 validation refused: "
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
                    "logistic-science WorldLease scope changed before execute"
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
                "logistic_science_output_positive":(
                    after["logistic_science_output_positive"] is True
                    and after["logistic_first_growth"]>0
                ),
                "inputs_endogenous":(
                    after["inputs_endogenous"] is True
                    and after["logistic_belts_ready"]>=2
                    and after["logistic_inserters_ready"]>=2
                ),
                "all_promoted_capabilities_alive":(
                    after["all_promoted_capabilities_alive"] is True
                ),
                "sustainability_soak_passed":(
                    after["sustainability_soak_passed"] is True
                    and after["logistic_second_output"]>0
                    and after["logistic_assembler_energy"]>0
                    and after["logistic_assembler_electrical_id"]>0
                ),
            }
            continuity_gate={
                "logistic_science_research_completed":(
                    after["research_completed"] is True
                    and after["research_remaining_count"]==0
                ),
                "native_research_queue":True,
                "direct_research_completion_mutation":False,
                "science_batch_ready":after["science_batch_ready"],
                "iron_plate_ready":after["iron_plate_ready"],
                "copper_plate_ready":after["copper_plate_ready"],
                "electric_coal_after_accumulation":(
                    after["electric_coal_after_accumulation"]
                ),
                "logistic_first_growth":after["logistic_first_growth"],
                "logistic_second_output":after["logistic_second_output"],
            }
            survival_gate={
                "previously_promoted":[
                    "iron_extraction","coal_self_sufficiency","iron_smelting",
                    "steam_power","copper_chain","automation_science",
                    "powered_manufacturing","electric_mining",
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
                "automation_science_survives":(
                    after["automation_science_survives"] is True
                    and after["science_replenished"]>0
                    and after["science_buffer_after"]>0
                ),
                "powered_manufacturing_survives":(
                    after["powered_manufacturing_survives"] is True
                    and after["gear_output"]>0
                    and after["circuit_output"]>0
                ),
                "electric_mining_survives":(
                    after["electric_mining_survives"] is True
                    and after["electric_survival_growth"]>0
                    and after["electric_drill_survival_energy"]>0
                    and after["electric_drill_survival_electrical_id"]>0
                    and after["electric_pole_survival_electrical_id"]>0
                    and after["electric_drill_survival_electrical_id"]
                        ==after["electric_pole_survival_electrical_id"]
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
                    ("automation_science","automation_science_survives"),
                    ("powered_manufacturing","powered_manufacturing_survives"),
                    ("electric_mining","electric_mining_survives"),
                )
                if not survival_gate[key]
            ]
            survival_gate["passed"]=not survival_gate["regressed"]
            promoted=(
                accepted
                and all(capability_gate.values())
                and continuity_gate["logistic_science_research_completed"] is True
                and continuity_gate["science_batch_ready"]>=77
                and survival_gate["passed"] is True
            )
            outer_gate_rollback=None
            if accepted and not promoted:
                outer_gate_rollback=executor.restore_checkpoint(checkpoint)
                after=_measure(namespace,plan.prepared)
            record.update({
                "status":"completed" if promoted else "rejected",
                "finished_at":utc_now(),
                "option_execution":execution.to_dict(),
                "measurement_final":after,
                "capability_gate":capability_gate,
                "continuity_gate":continuity_gate,
                "survival_gate":survival_gate,
                "capability_promoted":"logistic_science" if promoted else None,
                "transaction_committed":promoted,
                "intervention_snapshot":executor.intervention_snapshot(),
                "outer_gate_rollback":outer_gate_rollback,
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
                        "capability":"logistic_science",
                        "survival_required":survival_gate["previously_promoted"],
                    },
                    "authority_level":"A2",
                    "execution_trace":execution.to_dict(),
                    "postconditions":after,
                    "continuity_gate":continuity_gate,
                    "capability_delta":{
                        "promoted":["logistic_science"] if promoted else [],
                        "regressed":survival_gate["regressed"],
                    },
                    "resource_cost":{
                        "external_resource_injection":False,
                        "world_reset":False,
                        "natural_resource_harvested":False,
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
                "transaction_committed":False,
            })
            _write(artifact,record)
            return record
        finally:
            cleanup: dict[str,Any]={}
            if env is not None and instance is not None:
                try:
                    cleanup["pause_before_quiesce"]=_pause_factorio_for_quiesce(
                        instance
                    )
                    cleanup["storage_quiesce"]=_quiesce_fle_storage(instance)
                    save_name=(
                        "cortex-f5c-logistic-science"
                        if record.get("status")=="completed"
                        else "cortex-f5c-logistic-science-rollback"
                    )
                    cleanup["save_name"]=save_name
                    cleanup["save_response"]=_server_save(
                        instance.rcon_client,
                        save_name,
                    )
                except Exception as exc:  # noqa: BLE001
                    cleanup["persistence_error"]={
                        "type":type(exc).__name__,
                        "message":str(exc),
                    }
                try:
                    env.close()
                except Exception as exc:  # noqa: BLE001
                    cleanup["env_close_error"]={
                        "type":type(exc).__name__,
                        "message":str(exc),
                    }
            if autosave_original is not None:
                restore_client=control
                restore_error=None
                try:
                    if restore_client is None:
                        restore_client=_open_control_rcon()
                    cleanup["autosave_restore"]=_set_autosave_interval(
                        restore_client,
                        autosave_original,
                    )
                    if "autosave_guard" in record:
                        record["autosave_guard"]["status"]="restored"
                except Exception as exc:  # noqa: BLE001
                    restore_error=exc
                    if restore_client is control:
                        try:
                            restore_client=_open_control_rcon()
                            cleanup["autosave_restore"]=_set_autosave_interval(
                                restore_client,
                                autosave_original,
                            )
                            if "autosave_guard" in record:
                                record["autosave_guard"]["status"]="restored"
                            restore_error=None
                        except Exception as retry_exc:  # noqa: BLE001
                            restore_error=retry_exc
                    if restore_error is not None:
                        cleanup["autosave_restore_error"]={
                            "type":type(restore_error).__name__,
                            "message":str(restore_error),
                        }
                seen=set()
                for client in (restore_client,control):
                    if client is None or id(client) in seen:
                        continue
                    seen.add(id(client))
                    close=getattr(client,"close",None)
                    if callable(close):
                        try:
                            close()
                        except OSError:
                            pass
            if cleanup:
                record["runtime_cleanup"]=cleanup
                _write(artifact,record)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--artifact",type=Path,default=None)
    args=parser.parse_args()
    revision=code_revision()
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("logistic-science code revision unavailable")
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
    result=run_logistic_science(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
