#!/usr/bin/env python3
"""F5-C deterministic persistent copper-chain capability runner."""

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
from fle.env.game_types import Prototype

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.copper_chain_option import (
    INITIAL_COAL_DRAW,
    compose_copper_chain_option,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.live_canary import (
    resource_survey_from_overview,
    world_rows,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.spatial_validation import validate_observed_route
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.instrumentation.runtime import runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_fast_reposition_tool,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.planning.placement import ResourceSurvey, plan_placement
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_copper_chain_v1"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
DASHBOARD_BUILD_INFO=Path(
    "/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json"
)
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_copper_chain"
OWNER="run_cortex_f5c_copper_chain"
DEFAULT_OPTION_SECONDS=220
DEFAULT_GRANT_TTL_SECONDS=600

ServiceStateReader=Callable[[],dict[str,str]]
PhaseStateReader=Callable[[],dict[str,Any]]


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


def _unique_position(rows: Any,name: str) -> tuple[float,float]:
    matches=[]
    if isinstance(rows,list):
        for row in rows:
            if not isinstance(row,Mapping) or row.get("name")!=name:
                continue
            pos=row.get("position")
            if not isinstance(pos,Mapping):
                continue
            matches.append((float(pos["x"]),float(pos["y"])))
    if len(matches)!=1:
        raise RuntimeError(
            f"copper-chain preflight requires exactly one live {name}, got {matches!r}"
        )
    return matches[0]


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
) -> tuple[
    int,
    tuple[float,float],
    tuple[float,float],
    tuple[float,float],
    tuple[float,float],
    tuple[float,float],
]:
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C baseline state missing")
    seed=baseline.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5-C base seed missing")
    iron_raw=baseline.get("artifact_path")
    coal_state=baseline.get("coal_self_sufficiency")
    smelting_state=baseline.get("iron_smelting")
    if (
        not isinstance(iron_raw,str)
        or not isinstance(coal_state,Mapping)
        or not isinstance(smelting_state,Mapping)
    ):
        raise TypeError("promoted iron/coal/smelting artifacts missing")
    coal_raw=coal_state.get("artifact_path")
    smelting_raw=smelting_state.get("artifact_path")
    if not isinstance(coal_raw,str) or not isinstance(smelting_raw,str):
        raise TypeError("promoted continuation artifacts missing")

    iron=_load(Path(iron_raw))
    coal=_load(Path(coal_raw))
    smelting=_load(Path(smelting_raw))
    iron_plan=iron.get("option_plan")
    iron_prepared=iron_plan.get("prepared") if isinstance(iron_plan,Mapping) else None
    iron_pf=iron_prepared.get("preflight") if isinstance(iron_prepared,Mapping) else None
    iron_target=iron_pf.get("target_position") if isinstance(iron_pf,Mapping) else None
    coal_plan=coal.get("option_plan")
    coal_prepared=coal_plan.get("prepared") if isinstance(coal_plan,Mapping) else None
    coal_pf=coal_prepared.get("preflight") if isinstance(coal_prepared,Mapping) else None
    coal_target=coal_pf.get("target_position") if isinstance(coal_pf,Mapping) else None
    smelting_final=smelting.get("measurement_final")
    furnace=(
        smelting_final.get("furnace_position")
        if isinstance(smelting_final,Mapping)
        else None
    )
    if (
        not isinstance(iron_target,Mapping)
        or not isinstance(coal_target,Mapping)
        or not isinstance(furnace,Mapping)
    ):
        raise TypeError("promoted physical positions unavailable")
    iron_extractor=(float(iron_target["x"]),float(iron_target["y"]))
    iron_buffer=(iron_extractor[0]+0.5,iron_extractor[1]+1.5)
    coal_extractor=(float(coal_target["x"]),float(coal_target["y"]))
    coal_buffer=(coal_extractor[0]+0.5,coal_extractor[1]+1.5)
    iron_furnace=(float(furnace["x"]),float(furnace["y"]))
    return seed,iron_extractor,iron_buffer,coal_extractor,coal_buffer,iron_furnace


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_continuation_{seed}_copper_chain_{commit[:12]}.json"
    )


def _canonical_character(snapshot: Mapping[str,Any]) -> tuple[float,float]:
    rows=snapshot.get("entities")
    if not isinstance(rows,list):
        raise TypeError("canonical WORLD entities unavailable")
    for row in rows:
        if not isinstance(row,Mapping) or row.get("name")!="character":
            continue
        pos=row.get("position")
        if isinstance(pos,Mapping):
            return float(pos["x"]),float(pos["y"])
    raise RuntimeError("canonical WORLD character unavailable")


def _nearest_resource(
    overview: Mapping[str,Any],
    *,
    name: str,
    origin: tuple[float,float],
) -> tuple[float,float]:
    raw=overview.get("points")
    rows=raw if isinstance(raw,list) else []
    candidates=[]
    for row in rows:
        if not isinstance(row,Mapping) or row.get("name")!=name:
            continue
        try:
            candidates.append((float(row["x"]),float(row["y"])))
        except (KeyError,TypeError,ValueError):
            continue
    if not candidates:
        raise RuntimeError(f"resource overview has no {name}")
    return min(
        set(candidates),
        key=lambda pos:(
            (pos[0]-origin[0])**2+(pos[1]-origin[1])**2,
            pos[0],pos[1],
        ),
    )


def _observed_route_validation(
    *,
    start: tuple[float,float],
    finish: tuple[float,float],
    world_entities: list[dict[str,Any]],
    footprints: Mapping[str,tuple[int,int]],
) -> dict[str,Any]:
    center_x=(start[0]+finish[0])/2.0
    center_y=(start[1]+finish[1])/2.0
    radius=min(
        96.0,
        max(
            12.0,
            max(abs(finish[0]-start[0]),abs(finish[1]-start[1]))/2.0+12.0,
        ),
    )
    observer=FactorioObserver()
    try:
        snapshot=observer.map_snapshot(
            max_age_s=0.0,
            center_x=center_x,
            center_y=center_y,
            radius=radius,
        )
    finally:
        observer.close()
    result=validate_observed_route(
        start=start,
        goal=finish,
        map_snapshot=snapshot,
        world_entities=world_entities,
        footprints=footprints,
    )
    if result is None:
        raise RuntimeError(f"no observed-A* route from {start!r} to {finish!r}")
    return result.to_dict()


def _copper_target(
    *,
    resources: ResourceSurvey,
    world_entities: list[dict[str,Any]],
    footprints: Mapping[str,tuple[int,int]],
):
    anchors=sorted(
        point
        for point,name in resources.tiles.items()
        if name=="copper-ore" and isinstance(point,GridPoint)
    )
    for point in anchors:
        candidate=plan_placement(
            entity="burner-mining-drill",
            anchor=(float(point.x),float(point.y)),
            world=world_entities,
            footprints=footprints,
            direction=8,
            reach=0,
            resources=resources,
        )
        if (
            candidate.builds
            and candidate.position is not None
            and candidate.resource_tiles is not None
            and candidate.resource_tiles>0
            and "copper-ore" in candidate.resource_names
        ):
            return candidate
    raise RuntimeError("no buildable surveyed copper extraction target")


def _copper_furnace_target(
    *,
    copper: tuple[float,float],
    resources: ResourceSurvey,
    world_entities: list[dict[str,Any]],
    footprints: Mapping[str,tuple[int,int]],
) -> tuple[float,float]:
    for dx,dy in ((8.0,0.0),(8.0,6.0),(0.0,8.0),(-8.0,6.0)):
        candidate=plan_placement(
            entity="stone-furnace",
            anchor=(copper[0]+dx,copper[1]+dy),
            world=world_entities,
            footprints=footprints,
            reach=3,
            resources=resources,
        )
        if candidate.builds and candidate.position is not None:
            pos=(float(candidate.position[0]),float(candidate.position[1]))
            if (pos[0]-copper[0])**2+(pos[1]-copper[1])**2>=25.0:
                return pos
    raise RuntimeError("no buildable copper furnace target")


def preflight(
    *,
    artifact: Path,
    revision: Mapping[str,Any] | None=None,
    service_state_reader: ServiceStateReader=_service_state,
    phase_state_reader: PhaseStateReader=phase_state,
    ledger_path: Path=DEFAULT_LEDGER,
) -> dict[str,Any]:
    revision=dict(code_revision() if revision is None else revision)
    if revision.get("dirty") is not False:
        raise RuntimeError("F5-C copper chain requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C copper chain commit unavailable")
    if artifact.exists():
        raise FileExistsError("F5-C copper chain artifact already exists for commit")

    state=dict(phase_state_reader())
    protocol=state.get("phase5_protocol")
    bridge=state.get("phase5_authority_bridge")
    if not isinstance(protocol,Mapping) or not isinstance(bridge,Mapping):
        raise TypeError("F5 protocol/authority state unavailable")
    expected=[
        "iron_extraction","coal_self_sufficiency","iron_smelting","steam_power"
    ]
    if (
        state.get("phase")!="F5"
        or state.get("phase5_checkpoint")!="F5-C"
        or protocol.get("achieved_capabilities")!=expected
        or protocol.get("next_capability")!="copper_chain"
        or (
            not isinstance(protocol.get("capability_order_amendment"),Mapping)
            or protocol["capability_order_amendment"].get("validated") is not True
        )
        or bridge.get("validated") is not True
        or bridge.get("ambient_authority")!="A0"
        or bridge.get("max_bounded_grant")!="A2"
    ):
        raise RuntimeError(
            "copper chain requires amended F5-C frontier with four promoted capabilities"
        )

    dashboard_build=_load(DASHBOARD_BUILD_INFO)
    runtime_build=_load(RUNTIME_BUILD_INFO)
    for label,build in (("dashboard",dashboard_build),("runtime",runtime_build)):
        if build.get("commit")!=commit or build.get("dirty") is not False:
            raise RuntimeError(f"copper chain requires deployed {label} at exact HEAD")

    evolution=_assert_evolution_off(service_state_reader)
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError(f"active WorldLease blocks copper chain: {lease}")
    if _intervention_count()!=0:
        raise RuntimeError("F5 intervention ledger is not empty")
    ledger=_ledger_state(ledger_path)
    if not ledger["parent_writable"] or not ledger["file_writable"]:
        raise RuntimeError(f"authority ledger is not writable: {ledger}")

    (
        seed,iron_extractor,iron_buffer,coal_extractor,coal_buffer,iron_furnace
    )=_promoted_positions(state)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
        overview=observer.resource_overview(max_age_s=0.0)
    finally:
        observer.close()
    if snapshot.get("connected") is not True:
        raise RuntimeError("canonical WORLD snapshot unavailable")
    rows=snapshot.get("entities")
    expected_entities=(
        ("burner-mining-drill",iron_extractor),
        ("wooden-chest",iron_buffer),
        ("burner-mining-drill",coal_extractor),
        ("wooden-chest",coal_buffer),
        ("stone-furnace",iron_furnace),
    )
    found={}
    for name,pos in expected_entities:
        row=_entity_at(rows,name=name,position=pos)
        if row is None:
            raise RuntimeError(f"promoted {name} at {pos!r} absent from live WORLD")
        found[(name,pos)]=row

    boiler=_unique_position(rows,"boiler")
    steam_engine=_unique_position(rows,"steam-engine")
    coal_stock=_contents_count(found[("wooden-chest",coal_buffer)],"coal")
    if coal_stock<INITIAL_COAL_DRAW:
        raise RuntimeError(
            f"endogenous coal stock below {INITIAL_COAL_DRAW}: {coal_stock}"
        )
    totals=overview.get("totals")
    if not isinstance(totals,Mapping):
        raise TypeError("canonical resource overview totals unavailable")
    for resource in ("stone","copper-ore"):
        row=totals.get(resource)
        if not isinstance(row,Mapping) or float(row.get("amount") or 0)<=0:
            raise RuntimeError(f"canonical WORLD lacks {resource}")

    return {
        "status":"preflight_pass",
        "world_mutation":False,
        "grant_issued":False,
        "option_executed_live":False,
        "world_reset":False,
        "external_resource_injection":False,
        "persistent_copper_chain":True,
        "code_revision":revision,
        "dashboard_build_info":dashboard_build,
        "runtime_build_info":runtime_build,
        "phase_state":{
            "phase":"F5",
            "phase5_checkpoint":"F5-C",
            "phase5_next_checkpoint":"F5-C",
            "achieved_capabilities":expected,
            "next_capability":"copper_chain",
        },
        "evolution":evolution,
        "world_lease_state":lease,
        "authority_ledger":ledger,
        "intervention_count":0,
        "base_seed":seed,
        "incumbent_iron_extractor_position":{
            "x":iron_extractor[0],"y":iron_extractor[1],
        },
        "incumbent_iron_buffer_position":{
            "x":iron_buffer[0],"y":iron_buffer[1],
        },
        "incumbent_coal_extractor_position":{
            "x":coal_extractor[0],"y":coal_extractor[1],
        },
        "incumbent_coal_buffer_position":{
            "x":coal_buffer[0],"y":coal_buffer[1],
        },
        "incumbent_iron_furnace_position":{
            "x":iron_furnace[0],"y":iron_furnace[1],
        },
        "boiler_position":{"x":boiler[0],"y":boiler[1]},
        "steam_engine_position":{"x":steam_engine[0],"y":steam_engine[1]},
        "coal_stock":coal_stock,
        "canonical_character_position":{
            "x":_canonical_character(snapshot)[0],
            "y":_canonical_character(snapshot)[1],
        },
        "world_entity_count":snapshot.get("entity_count"),
    }


def _option_request(*,run_id: str,commit: str,seconds: int) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:copper-chain",
        kind=OptionKind.ESTABLISH_COPPER_CHAIN,
        goal=(
            "establish persistent copper extraction and smelting while "
            "preserving iron, coal, smelting and steam power"
        ),
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_copper_chain",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=seconds*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:copper-chain-action",
        family=ActionFamily.PLACEMENT,
        intent="establish endogenous persistent copper extraction and smelting",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_copper_chain",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=(
            "iron_extraction","coal_self_sufficiency","iron_smelting","steam_power",
        ),
        provides=("copper_chain",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    del prepared
    numeric=(
        "copper_ore_count","copper_plate_count",
        "construction_iron_ready","construction_iron_shortfall",
        "construction_topup_ore","construction_topup_plates",
        "iron_survival_growth","coal_survival_growth",
        "smelting_survival_growth","steam_survival_amount",
        "steam_survival_energy",
    )
    result: dict[str,Any]={}
    for name in (
        "copper_extraction_live","copper_smelting_live",
        "copper_plate_output_positive","copper_extractor_exists",
        "copper_furnace_exists","iron_extraction_survives",
        "coal_self_sufficiency_survives","iron_smelting_survives",
        "steam_power_survives",
    ):
        result[name]=bool(getattr(namespace,f"cortex_{name}",False))
    for name in numeric:
        result[name]=float(getattr(namespace,f"cortex_{name}",0) or 0)
    return result


def run_copper_chain(
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
        f"cortex-f5c-copper-chain-{seed}-"
        +datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "planning_stage":"starting",
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
        "persistent_copper_chain":True,
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
            record["fle_transactional_reposition_tool"]=bind_fast_reposition_tool(env)
            record["fle_eval_timeout_s"]=enforce_minimum_eval_timeout(
                env,minimum_seconds=300
            )
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"copper_chain",
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
            if after_attach.get("entity_count")!=pf["world_entity_count"]:
                raise RuntimeError("live FLE attachment changed WORLD before copper A2")

            physical_rows=world_rows(namespace,resources=False)
            footprints=runtime_entity_footprints(instance)
            resources=resource_survey_from_overview(overview)
            copper_plan=_copper_target(
                resources=resources,
                world_entities=physical_rows,
                footprints=footprints,
            )
            if copper_plan.position is None:
                raise RuntimeError("copper target planner returned no position")
            copper=(float(copper_plan.position[0]),float(copper_plan.position[1]))
            furnace=_copper_furnace_target(
                copper=copper,
                resources=resources,
                world_entities=physical_rows,
                footprints=footprints,
            )
            character=(
                float(namespace.player_location.x),
                float(namespace.player_location.y),
            )
            stone=_nearest_resource(overview,name="stone",origin=character)
            wood_entity=namespace.nearest(Prototype.Wood)
            wood=(float(wood_entity.x),float(wood_entity.y))
            routes={
                "stone":_observed_route_validation(
                    start=character,finish=stone,
                    world_entities=physical_rows,footprints=footprints,
                ),
                "wood":_observed_route_validation(
                    start=stone,finish=wood,
                    world_entities=physical_rows,footprints=footprints,
                ),
                "copper":_observed_route_validation(
                    start=wood,finish=copper,
                    world_entities=physical_rows,footprints=footprints,
                ),
                "furnace":_observed_route_validation(
                    start=copper,finish=furnace,
                    world_entities=physical_rows,footprints=footprints,
                ),
            }
            record.update({
                "planning_stage":"targets_validated",
                "bootstrap_targets":{
                    "stone":{"x":stone[0],"y":stone[1]},
                    "wood":{"x":wood[0],"y":wood[1]},
                    "copper_extractor":{"x":copper[0],"y":copper[1]},
                    "copper_furnace":{"x":furnace[0],"y":furnace[1]},
                },
                "route_validations":routes,
            })
            _write(artifact,record)

            def pos(name: str) -> tuple[float,float]:
                row=pf[name]
                if not isinstance(row,Mapping):
                    raise TypeError(f"preflight position missing: {name}")
                return float(row["x"]),float(row["y"])

            positions={
                "iron_extractor":pos("incumbent_iron_extractor_position"),
                "iron_buffer":pos("incumbent_iron_buffer_position"),
                "iron_furnace":pos("incumbent_iron_furnace_position"),
                "coal_extractor":pos("incumbent_coal_extractor_position"),
                "coal_buffer":pos("incumbent_coal_buffer_position"),
                "boiler":pos("boiler_position"),
                "steam_engine":pos("steam_engine_position"),
                "copper_extractor":copper,
                "copper_furnace":furnace,
                "stone":stone,
                "wood":wood,
            }
            option=_option_request(
                run_id=run_id,commit=commit,seconds=option_seconds
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_copper_chain_option(
                option,
                action_request=action,
                positions=positions,
                route_validations=routes,
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"copper chain Option refused: {refusal}")
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
                reason="F5-C deterministic copper_chain preserving four incumbents",
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "copper chain A2 validation refused: "
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
                raise RuntimeError("copper chain WorldLease scope changed before execute")
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
                "copper_extraction_live":(
                    after["copper_extraction_live"] is True
                    and after["copper_ore_count"]>0
                ),
                "copper_smelting_live":(
                    after["copper_smelting_live"] is True
                    and after["copper_plate_count"]>0
                ),
                "copper_plate_output_positive":(
                    after["copper_plate_output_positive"] is True
                    and after["copper_plate_count"]>0
                ),
            }
            survival_gate={
                "previously_promoted":[
                    "iron_extraction","coal_self_sufficiency",
                    "iron_smelting","steam_power",
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
            }
            survival_gate["regressed"]=[
                name
                for name,key in (
                    ("iron_extraction","iron_extraction_survives"),
                    ("coal_self_sufficiency","coal_self_sufficiency_survives"),
                    ("iron_smelting","iron_smelting_survives"),
                    ("steam_power","steam_power_survives"),
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
                "capability_promoted":"copper_chain" if promoted else None,
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
                        "capability":"copper_chain",
                        "survival_required":survival_gate["previously_promoted"],
                    },
                    "authority_level":"A2",
                    "execution_trace":execution.to_dict(),
                    "postconditions":after,
                    "capability_delta":{
                        "promoted":["copper_chain"] if promoted else [],
                        "regressed":survival_gate["regressed"],
                    },
                    "resource_cost":{
                        "external_resource_injection":False,
                        "world_reset":False,
                    },
                    "rollback":not promoted,
                    "reward_components":{
                        "capability_gate":sum(capability_gate.values()),
                        "survival_gate":int(survival_gate["passed"]),
                    },
                    "next_state":after,
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
        raise RuntimeError("copper chain code revision unavailable")
    state=phase_state()
    seed,*_=_promoted_positions(state)
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(commit=commit,seed=seed).resolve()
    )
    if not args.execute:
        payload=preflight(artifact=artifact,revision=revision)
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_copper_chain(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
