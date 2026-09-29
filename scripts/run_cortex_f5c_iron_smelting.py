#!/usr/bin/env python3
"""F5-C deterministic iron-smelting capability runner."""

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

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.iron_smelting_option import (
    COAL_DRAW,
    IRON_ORE_DRAW,
    STONE_QUANTITY,
    compose_iron_smelting_option,
)
from factorio_ai_lab.cortex.live_canary import (
    resource_survey_from_overview,
    world_rows,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.cortex.spatial_validation import validate_observed_route
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.instrumentation.runtime import runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_fast_reposition_tool,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.planning.placement import plan_placement
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_iron_smelting_v1"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
BUILD_INFO=Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_iron_smelting"
OWNER="run_cortex_f5c_iron_smelting"
DEFAULT_OPTION_SECONDS=120
DEFAULT_GRANT_TTL_SECONDS=300
STONE_FALLBACK_CANDIDATE_LIMIT=8

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


def _positions(
    state: Mapping[str,Any],
) -> tuple[
    int,
    tuple[float,float],
    tuple[float,float],
    tuple[float,float],
    tuple[float,float],
    Path,
]:
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C baseline state missing")
    seed=baseline.get("seed")
    if not isinstance(seed,int) or isinstance(seed,bool):
        raise TypeError("F5-C base seed missing")
    iron_raw=baseline.get("artifact_path")
    coal_state=baseline.get("coal_self_sufficiency")
    if not isinstance(iron_raw,str) or not isinstance(coal_state,Mapping):
        raise TypeError("F5-C promoted artifacts missing")
    coal_raw=coal_state.get("artifact_path")
    if not isinstance(coal_raw,str):
        raise TypeError("F5-C promoted coal artifact missing")
    iron=_load(Path(iron_raw))
    coal=_load(Path(coal_raw))
    iron_plan=iron.get("option_plan")
    iron_prepared=iron_plan.get("prepared") if isinstance(iron_plan,Mapping) else None
    iron_pf=iron_prepared.get("preflight") if isinstance(iron_prepared,Mapping) else None
    iron_target=iron_pf.get("target_position") if isinstance(iron_pf,Mapping) else None
    coal_plan=coal.get("option_plan")
    coal_prepared=coal_plan.get("prepared") if isinstance(coal_plan,Mapping) else None
    coal_pf=coal_prepared.get("preflight") if isinstance(coal_prepared,Mapping) else None
    coal_target=coal_pf.get("target_position") if isinstance(coal_pf,Mapping) else None
    if not isinstance(iron_target,Mapping) or not isinstance(coal_target,Mapping):
        raise TypeError("promoted iron/coal positions unavailable")
    iron_extractor=(float(iron_target["x"]),float(iron_target["y"]))
    iron_buffer=(iron_extractor[0]+0.5,iron_extractor[1]+1.5)
    coal_extractor=(float(coal_target["x"]),float(coal_target["y"]))
    coal_buffer=(coal_extractor[0]+0.5,coal_extractor[1]+1.5)
    return (
        seed,
        iron_extractor,
        iron_buffer,
        coal_extractor,
        coal_buffer,
        Path(coal_raw),
    )


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_continuation_{seed}_iron_smelting_{commit[:12]}.json"
    )


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
        raise RuntimeError("F5-C iron smelting requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C iron smelting commit unavailable")
    if artifact.exists():
        raise FileExistsError("F5-C iron smelting artifact already exists for commit")

    state=dict(phase_state_reader())
    protocol=state.get("phase5_protocol")
    bridge=state.get("phase5_authority_bridge")
    if not isinstance(protocol,Mapping) or not isinstance(bridge,Mapping):
        raise TypeError("F5 protocol/authority state unavailable")
    achieved=protocol.get("achieved_capabilities")
    if (
        state.get("phase")!="F5"
        or state.get("phase5_checkpoint")!="F5-C"
        or achieved!=["iron_extraction","coal_self_sufficiency"]
        or bridge.get("validated") is not True
        or bridge.get("ambient_authority")!="A0"
        or bridge.get("max_bounded_grant")!="A2"
    ):
        raise RuntimeError(
            "iron smelting requires F5-C with iron+coal promoted and A0/A2 boundary"
        )
    build=_load(BUILD_INFO)
    if build.get("commit")!=commit or build.get("dirty") is not False:
        raise RuntimeError("iron smelting requires deployed dashboard/runtime at exact HEAD")
    evolution=_assert_evolution_off(service_state_reader)
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError(f"active WorldLease blocks iron smelting: {lease}")
    if _intervention_count()!=0:
        raise RuntimeError("F5 intervention ledger is not empty")
    ledger=_ledger_state(ledger_path)
    if not ledger["parent_writable"] or not ledger["file_writable"]:
        raise RuntimeError(f"authority ledger is not writable: {ledger}")

    (
        seed,
        iron_extractor,
        iron_buffer,
        coal_extractor,
        coal_buffer,
        coal_artifact,
    )=_positions(state)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
    finally:
        observer.close()
    if snapshot.get("connected") is not True:
        raise RuntimeError("canonical WORLD snapshot unavailable")
    rows=snapshot.get("entities")
    iron_drill=_entity_at(rows,name="burner-mining-drill",position=iron_extractor)
    iron_chest=_entity_at(rows,name="wooden-chest",position=iron_buffer)
    coal_drill=_entity_at(rows,name="burner-mining-drill",position=coal_extractor)
    coal_chest=_entity_at(rows,name="wooden-chest",position=coal_buffer)
    if any(row is None for row in (iron_drill,iron_chest,coal_drill,coal_chest)):
        raise RuntimeError("promoted iron/coal entities are absent from live WORLD")
    assert iron_chest is not None
    assert coal_chest is not None
    iron_stock=_contents_count(iron_chest,"iron-ore")
    coal_stock=_contents_count(coal_chest,"coal")
    if iron_stock<IRON_ORE_DRAW:
        raise RuntimeError(
            f"endogenous iron stock below {IRON_ORE_DRAW}: {iron_stock}"
        )
    if coal_stock<COAL_DRAW:
        raise RuntimeError(
            f"endogenous coal stock below {COAL_DRAW}: {coal_stock}"
        )
    return {
        "status":"preflight_pass",
        "world_mutation":False,
        "grant_issued":False,
        "option_executed_live":False,
        "world_reset":False,
        "external_resource_injection":False,
        "code_revision":revision,
        "build_info":build,
        "phase_state":{
            "phase":"F5",
            "phase5_checkpoint":"F5-C",
            "phase5_next_checkpoint":"F5-C",
            "achieved_capabilities":["iron_extraction","coal_self_sufficiency"],
        },
        "evolution":evolution,
        "world_lease_state":lease,
        "authority_ledger":ledger,
        "intervention_count":0,
        "base_seed":seed,
        "coal_artifact":str(coal_artifact),
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
        "iron_stock":iron_stock,
        "coal_stock":coal_stock,
        "world_entity_count":snapshot.get("entity_count"),
    }


def _observed_route_validation(
    *,
    start: tuple[float,float],
    finish: tuple[float,float],
    world_entities: list[dict[str,Any]],
    footprints: Mapping[str,tuple[int,int]],
) -> dict[str,Any]:
    center_x=(float(start[0])+float(finish[0]))/2.0
    center_y=(float(start[1])+float(finish[1]))/2.0
    radius=min(
        96.0,
        max(
            12.0,
            max(
                abs(float(finish[0])-float(start[0])),
                abs(float(finish[1])-float(start[1])),
            )/2.0+12.0,
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
    validation=validate_observed_route(
        start=start,
        goal=finish,
        map_snapshot=snapshot,
        world_entities=world_entities,
        footprints=footprints,
    )
    if validation is None:
        raise RuntimeError(
            f"observed weighted A* found no route from {start!r} to {finish!r}"
        )
    return validation.to_dict()


def _stone_route(
    namespace: Any,
    overview: Mapping[str,Any],
    *,
    world_entities: list[dict[str,Any]],
    footprints: Mapping[str,tuple[int,int]],
    preferred: tuple[float,float] | None=None,
) -> tuple[dict[str,Any],tuple[float,float]]:
    player=getattr(namespace,"player_location",None)
    if player is None:
        raise RuntimeError("iron smelting requires player location")
    start=(float(player.x),float(player.y))
    points=overview.get("points")
    raw=points if isinstance(points,list) else []
    candidates=[]
    for point in raw:
        if not isinstance(point,Mapping) or point.get("name")!="stone":
            continue
        try:
            candidates.append((float(point["x"]),float(point["y"])))
        except (KeyError,TypeError,ValueError):
            continue
    ordered=sorted(
        set(candidates),
        key=lambda pos:(
            (pos[0]-start[0])**2+(pos[1]-start[1])**2,
            pos[0],pos[1],
        ),
    )
    candidates=[]
    if preferred is not None and preferred in ordered:
        candidates.append(preferred)
    candidates.extend(
        pos
        for pos in ordered[:STONE_FALLBACK_CANDIDATE_LIMIT]
        if pos not in candidates
    )
    errors=[]
    for candidate in candidates:
        try:
            validation=_observed_route_validation(
                start=start,
                finish=candidate,
                world_entities=world_entities,
                footprints=footprints,
            )
        except Exception as exc:  # noqa: BLE001 - candidate evidence
            errors.append(f"{candidate!r}:{type(exc).__name__}:{exc}")
            continue
        return ({
            "resource":"stone",
            "quantity":STONE_QUANTITY,
            "position":{"x":candidate[0],"y":candidate[1]},
            "validated_path_waypoints":int(validation["path_waypoints"]),
            "route_validation":validation,
        },candidate)
    raise RuntimeError(
        "no observed-A* stone target for iron smelting: "
        +"; ".join(errors[-5:])
    )


def _preferred_stone_from_coal_artifact(
    coal_artifact: Path,
) -> tuple[float,float] | None:
    payload=_load(coal_artifact)
    route=payload.get("bootstrap_route")
    if not isinstance(route,list):
        return None
    for row in route:
        if not isinstance(row,Mapping) or row.get("resource")!="stone":
            continue
        position=row.get("position")
        if not isinstance(position,Mapping):
            continue
        try:
            return float(position["x"]),float(position["y"])
        except (KeyError,TypeError,ValueError):
            return None
    return None


def _option_request(*,run_id: str,commit: str,seconds: int) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:iron-smelting",
        kind=OptionKind.ESTABLISH_IRON_SMELTING,
        goal="establish persistent endogenous iron smelting while preserving iron and coal",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_iron_smelting",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=seconds*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:iron-smelting-action",
        family=ActionFamily.PLACEMENT,
        intent="establish persistent iron smelting from endogenous iron and coal",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_iron_smelting",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=("iron_extraction","coal_self_sufficiency"),
        provides=("iron_smelting",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    return {
        "iron_ore_input_live":bool(
            getattr(namespace,"cortex_iron_ore_input_live",False)
        ),
        "furnace_operational":bool(
            getattr(namespace,"cortex_furnace_operational",False)
        ),
        "iron_plate_output_positive":bool(
            getattr(namespace,"cortex_iron_plate_output_positive",False)
        ),
        "iron_plate_count":float(
            getattr(namespace,"cortex_iron_plate_count",0) or 0
        ),
        "furnace_status_observed":str(
            getattr(namespace,"cortex_furnace_status_observed","")
        ),
        "iron_extraction_survives":bool(
            getattr(namespace,"cortex_iron_extraction_survives",False)
        ),
        "coal_self_sufficiency_survives":bool(
            getattr(namespace,"cortex_coal_self_sufficiency_survives",False)
        ),
        "iron_survival_growth":float(
            getattr(namespace,"cortex_iron_survival_growth",0) or 0
        ),
        "coal_survival_growth":float(
            getattr(namespace,"cortex_coal_survival_growth",0) or 0
        ),
        "furnace_position":prepared.preflight.get("furnace_position"),
    }


def _checkpoint_digest(checkpoint: Any) -> str:
    return hashlib.sha256(checkpoint.to_raw().encode()).hexdigest()


def run_iron_smelting(
    *,
    artifact: Path,
    option_seconds: int=DEFAULT_OPTION_SECONDS,
    grant_ttl_seconds: int=DEFAULT_GRANT_TTL_SECONDS,
    ledger_path: Path=DEFAULT_LEDGER,
    service_state_reader: ServiceStateReader=_service_state,
    phase_state_reader: PhaseStateReader=phase_state,
) -> dict[str,Any]:
    revision=code_revision()
    pf=preflight(
        artifact=artifact,
        revision=revision,
        service_state_reader=service_state_reader,
        phase_state_reader=phase_state_reader,
        ledger_path=ledger_path,
    )
    commit=str(revision["commit"])
    seed=int(pf["base_seed"])
    iron_extractor=(
        float(pf["incumbent_iron_extractor_position"]["x"]),
        float(pf["incumbent_iron_extractor_position"]["y"]),
    )
    iron_buffer=(
        float(pf["incumbent_iron_buffer_position"]["x"]),
        float(pf["incumbent_iron_buffer_position"]["y"]),
    )
    coal_extractor=(
        float(pf["incumbent_coal_extractor_position"]["x"]),
        float(pf["incumbent_coal_extractor_position"]["y"]),
    )
    coal_buffer=(
        float(pf["incumbent_coal_buffer_position"]["x"]),
        float(pf["incumbent_coal_buffer_position"]["y"]),
    )
    run_id="cortex-f5c-iron-smelting-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "capability":"iron_smelting",
        "base_seed":seed,
        "run_id":run_id,
        "code_revision":revision,
        "preflight":pf,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "automatic_retry":False,
        "world_reset":False,
        "external_resource_injection":False,
        "human_intervention_count":0,
        "option_execution_attempts":0,
        "grant_issued":False,
        "started_at":utc_now(),
        "planning_stage":"starting",
    }
    _write(artifact,record)
    env=None
    with FactorioWorldLease(run_id=run_id,arena=ARENA,owner=OWNER) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        try:
            from fle.commons.models.game_state import GameState

            env=attach_live_factorio_environment()
            record["fle_eval_timeout_s"]=enforce_minimum_eval_timeout(
                env,minimum_seconds=300
            )
            record["fle_transactional_reposition_tool"]=bind_fast_reposition_tool(env)
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"iron_smelting",
                    "progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace

            observer=FactorioObserver()
            try:
                attached=observer.snapshot()
                overview=observer.resource_overview(max_age_s=0.0)
            finally:
                observer.close()
            rows=attached.get("entities")
            for name,pos in (
                ("burner-mining-drill",iron_extractor),
                ("wooden-chest",iron_buffer),
                ("burner-mining-drill",coal_extractor),
                ("wooden-chest",coal_buffer),
            ):
                if _entity_at(rows,name=name,position=pos) is None:
                    raise RuntimeError(
                        "live FLE attachment changed promoted WORLD before iron-smelting A2"
                    )
            if attached.get("entity_count")!=pf["world_entity_count"]:
                raise RuntimeError("live FLE attachment changed entity count before A2")
            checkpoint=GameState.from_instance(instance)
            executor.game_state=checkpoint
            record["rollback_checkpoint"]={
                "sha256":_checkpoint_digest(checkpoint),
                "source":"GameState.from_instance",
                "world_reset":False,
            }
            record["planning_stage"]="live_attachment_verified"
            _write(artifact,record)

            resources=resource_survey_from_overview(overview)
            if not resources.tiles:
                raise RuntimeError("iron smelting resource overview is empty")
            record["planning_stage"]="resource_overview_ready"
            record["resource_point_count"]=len(overview.get("points") or [])
            _write(artifact,record)

            physical_rows=world_rows(namespace,resources=False)
            footprints=runtime_entity_footprints(instance)
            coal_artifact=Path(str(pf["coal_artifact"]))
            preferred_stone=_preferred_stone_from_coal_artifact(coal_artifact)
            record["preferred_stone_position"]=(
                None
                if preferred_stone is None
                else {"x":preferred_stone[0],"y":preferred_stone[1]}
            )
            record["planning_stage"]="stone_route_validation"
            _write(artifact,record)
            stone_route,stone_end=_stone_route(
                namespace,
                overview,
                world_entities=physical_rows,
                footprints=footprints,
                preferred=preferred_stone,
            )
            record["stone_route"]=stone_route
            record["spatial_validator"]=stone_route.get("route_validation",{}).get(
                "validator"
            )
            record["planning_stage"]="stone_route_validated"
            _write(artifact,record)
            placement=plan_placement(
                entity="stone-furnace",
                anchor=(iron_buffer[0]+4.0,iron_buffer[1]),
                world=physical_rows,
                footprints=footprints,
                reach=8,
                resources=resources,
            )
            if not placement.builds or placement.position is None:
                raise RuntimeError(
                    "no deterministic furnace placement: "+json.dumps(
                        placement.to_dict(),sort_keys=True
                    )
                )
            furnace_position=(
                float(placement.position[0]),
                float(placement.position[1]),
            )
            furnace_route=_observed_route_validation(
                start=stone_end,
                finish=furnace_position,
                world_entities=physical_rows,
                footprints=footprints,
            )
            record["furnace_route_validation"]=furnace_route
            record["furnace_target_validated_path_waypoints"]=int(
                furnace_route["path_waypoints"]
            )
            record["furnace_position"]={
                "x":furnace_position[0],
                "y":furnace_position[1],
            }
            record["planning_stage"]="furnace_route_validated"
            _write(artifact,record)

            option=_option_request(
                run_id=run_id,commit=commit,seconds=option_seconds
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_iron_smelting_option(
                option,
                action_request=action,
                furnace_placement=placement,
                stone_route=stone_route,
                incumbent_iron_extractor_position=iron_extractor,
                incumbent_iron_buffer_position=iron_buffer,
                incumbent_coal_extractor_position=coal_extractor,
                incumbent_coal_buffer_position=coal_buffer,
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"iron smelting Option refused: {refusal}")
            plan=composed.plan
            before=_measure(namespace,plan.prepared)

            ledger=PersistentOptionGrantLedger(ledger_path)
            bridge=F5BoundedAuthorityBridge(
                ledger=ledger,
                lease_attestor=lease.active_attestation,
            )
            scope,grant=bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason="F5-C deterministic iron_smelting preserving iron+coal",
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "iron smelting A2 validation refused: "
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
                raise RuntimeError("iron smelting WorldLease scope changed before execute")
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
                "iron_ore_input_live":after["iron_ore_input_live"] is True,
                "furnace_operational":after["furnace_operational"] is True,
                "iron_plate_output_positive":(
                    after["iron_plate_output_positive"] is True
                    and after["iron_plate_count"]>0
                ),
            }
            survival_gate={
                "previously_promoted":[
                    "iron_extraction","coal_self_sufficiency"
                ],
                "iron_extraction_survives":(
                    after["iron_extraction_survives"] is True
                    and after["iron_survival_growth"]>0
                ),
                "coal_self_sufficiency_survives":(
                    after["coal_self_sufficiency_survives"] is True
                    and after["coal_survival_growth"]>0
                ),
            }
            survival_gate["regressed"]=[
                name
                for name,key in (
                    ("iron_extraction","iron_extraction_survives"),
                    ("coal_self_sufficiency","coal_self_sufficiency_survives"),
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
                "capability_promoted":"iron_smelting" if promoted else None,
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
                    "authority_level":"A2",
                    "execution_trace":execution.to_dict(),
                    "postconditions":after,
                    "capability_delta":{
                        "promoted":["iron_smelting"] if promoted else [],
                        "regressed":survival_gate["regressed"],
                    },
                    "rollback":not promoted,
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
    parser.add_argument("--ledger",type=Path,default=DEFAULT_LEDGER)
    parser.add_argument("--option-seconds",type=int,default=DEFAULT_OPTION_SECONDS)
    parser.add_argument(
        "--grant-ttl-seconds",type=int,default=DEFAULT_GRANT_TTL_SECONDS
    )
    args=parser.parse_args()
    revision=code_revision()
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C iron smelting code revision unavailable")
    state=phase_state()
    seed,_,_,_,_,_=_positions(state)
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(seed,commit).resolve()
    )
    if not args.execute:
        payload=preflight(
            artifact=artifact,
            revision=revision,
            ledger_path=args.ledger,
        )
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_iron_smelting(
        artifact=artifact,
        option_seconds=args.option_seconds,
        grant_ttl_seconds=args.grant_ttl_seconds,
        ledger_path=args.ledger,
    )
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
