#!/usr/bin/env python3
"""F5-C continuation: coal self-sufficiency on the surviving iron WORLD.

Without --execute this is read-only preflight. With --execute it acquires one
WorldLease, captures the exact current FLE GameState as rollback checkpoint,
issues exactly one A2 grant and attempts exactly one coal self-sufficiency
Option. It never resets into a new seed.
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

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
)
from factorio_ai_lab.cortex.coal_self_sufficiency_option import (
    BOOTSTRAP_RESOURCES,
    compose_coal_self_sufficiency_option,
)
from factorio_ai_lab.cortex.f5_authority import F5BoundedAuthorityBridge
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.live_canary import (
    resource_survey_from_overview,
    world_rows,
)
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.instrumentation.runtime import runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_fast_reposition_tool,
    enforce_minimum_eval_timeout,
    enforce_pathfinding_retry_floor,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.runtime import WORLD_LEASE_STATE, FactorioWorldLease

SCHEMA_VERSION="cortex_f5c_coal_self_sufficiency_v1"
DEFAULT_OPTION_SECONDS=120
DEFAULT_GRANT_TTL_SECONDS=300
ARENA="cortex_f5c_coal_self_sufficiency"
OWNER="run_cortex_f5c_coal_self_sufficiency"
def artifact_for_revision(base_seed: int,commit: str) -> Path:
    if not isinstance(base_seed,int) or isinstance(base_seed,bool) or base_seed<=0:
        raise ValueError("coal continuation artifact requires positive base seed")
    normalized=str(commit).strip().lower()
    if len(normalized)<12 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ValueError("coal continuation artifact requires a hexadecimal git commit")
    return (
        RUNS_DIR/"audits"/
        (
            f"cortex_f5c_continuation_{base_seed}_"
            f"coal_self_sufficiency_{normalized[:12]}.json"
        )
    )


DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
BUILD_INFO=Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")

ServiceStateReader=Callable[[],Mapping[str,str]]
PhaseStateReader=Callable[[],Mapping[str,Any]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_object(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _atomic_json(path: Path,payload: Mapping[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(
        json.dumps(dict(payload),indent=2,sort_keys=True,default=str)+"\n",
        encoding="utf-8",
    )
    temp.replace(path)


def phase_state() -> dict[str,Any]:
    path=RUNS_DIR/"cortex_phase_state.json"
    return _load_object(path) if path.exists() else {}


def evolution_service_state() -> dict[str,str]:
    def run(action: str) -> str:
        done=subprocess.run(
            ["systemctl",action,"factorio-ai-evolution"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output=(done.stdout or done.stderr or "").strip()
        return output.splitlines()[0] if output else f"returncode:{done.returncode}"
    return {"active":run("is-active"),"enabled":run("is-enabled")}


def _assert_evolution_off(reader: ServiceStateReader) -> dict[str,str]:
    state=dict(reader())
    if state.get("active")!="inactive" or state.get("enabled")!="disabled":
        raise RuntimeError(f"F5-C requires evolution OFF: {state}")
    return state


def _intervention_count() -> int:
    payload=_load_object(INTERVENTION_LEDGER)
    rows=payload.get("interventions")
    if not isinstance(rows,list):
        raise TypeError("F5 intervention ledger interventions must be a list")
    return len(rows)


def _assert_ledger_writable(path: Path) -> dict[str,Any]:
    parent=path.parent
    if not parent.exists() or not parent.is_dir():
        raise RuntimeError(f"authority ledger directory unavailable: {parent}")
    parent_writable=os.access(parent,os.W_OK|os.X_OK)
    file_exists=path.exists()
    file_writable=(not file_exists) or os.access(path,os.R_OK|os.W_OK)
    if not parent_writable or not file_writable:
        raise RuntimeError(
            "authority ledger is not writable: "
            f"parent={parent_writable} file={file_writable}"
        )
    return {
        "path":str(path),
        "parent_writable":parent_writable,
        "file_exists":file_exists,
        "file_writable":file_writable,
    }


def _positions_from_iron_artifact(
    state: Mapping[str,Any],
) -> tuple[tuple[float,float],tuple[float,float],Path,int]:
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C deterministic baseline state must be an object")
    raw_seed=baseline.get("seed")
    if (
        not isinstance(raw_seed,int)
        or isinstance(raw_seed,bool)
        or raw_seed<=0
        or baseline.get("iron_extraction_validated") is not True
    ):
        raise RuntimeError("promoted iron base seed is unavailable")
    raw_path=baseline.get("artifact_path")
    if not isinstance(raw_path,str) or not raw_path:
        raise RuntimeError("promoted iron artifact path missing")
    path=Path(raw_path)
    payload=_load_object(path)
    if (
        payload.get("status")!="completed"
        or payload.get("capability_promoted")!="iron_extraction"
        or payload.get("seed")!=raw_seed
    ):
        raise RuntimeError("selected iron artifact is not the promoted base episode")
    plan=payload.get("option_plan")
    prepared=plan.get("prepared") if isinstance(plan,Mapping) else None
    preflight=prepared.get("preflight") if isinstance(prepared,Mapping) else None
    target=preflight.get("target_position") if isinstance(preflight,Mapping) else None
    if not isinstance(target,Mapping):
        raise TypeError("promoted iron target position must be an object")
    x=float(target["x"])
    y=float(target["y"])
    return (x,y),(x+0.5,y+1.5),path,raw_seed


def _entity_at(
    rows: object,
    *,
    name: str,
    position: tuple[float,float],
    tolerance: float=0.15,
) -> dict[str,Any] | None:
    if not isinstance(rows,list):
        return None
    for row in rows:
        if not isinstance(row,dict) or str(row.get("name") or "")!=name:
            continue
        pos=row.get("position")
        if not isinstance(pos,dict):
            continue
        try:
            dx=abs(float(pos.get("x"))-position[0])
            dy=abs(float(pos.get("y"))-position[1])
        except (TypeError,ValueError):
            continue
        if dx<=tolerance and dy<=tolerance:
            return row
    return None


def _validated_path_waypoints(
    namespace: Any,
    *,
    start: tuple[float,float],
    finish: tuple[float,float],
) -> int:
    from fle.env.entities import Position

    move_to=namespace.move_to
    handle=move_to.request_path(
        start=Position(x=float(start[0]),y=float(start[1])),
        finish=Position(x=float(finish[0]),y=float(finish[1])),
        allow_paths_through_own_entities=True,
        resolution=-1,
    )
    path=move_to.get_path(handle,max_attempts=40)
    if not path:
        raise RuntimeError(
            f"empty validated path from {start!r} to {finish!r}"
        )
    return len(path)


def _plan_bootstrap_route(
    namespace: Any,
    overview: Mapping[str,Any],
) -> tuple[tuple[dict[str,Any],...],tuple[float,float]]:
    from fle.env.game_types import Resource

    raw_points=overview.get("points")
    points=raw_points if isinstance(raw_points,list) else []
    player=getattr(namespace,"player_location",None)
    if player is None:
        raise RuntimeError("F5-C coal bootstrap route requires player location")
    start=(float(player.x),float(player.y))
    route: list[dict[str,Any]]=[]

    for template in BOOTSTRAP_RESOURCES:
        resource=str(template["resource"])
        candidates: list[tuple[float,float]]=[]
        if resource=="wood":
            wood=namespace.nearest(Resource.Wood)
            candidates=[(float(wood.x),float(wood.y))]
        else:
            for point in points:
                if not isinstance(point,Mapping) or point.get("name")!=resource:
                    continue
                try:
                    candidates.append((float(point["x"]),float(point["y"])))
                except (KeyError,TypeError,ValueError):
                    continue
            candidates=sorted(
                set(candidates),
                key=lambda pos:(
                    (pos[0]-start[0])**2+(pos[1]-start[1])**2,
                    pos[0],
                    pos[1],
                ),
            )[:128]
        chosen: tuple[float,float] | None=None
        chosen_waypoints: int | None=None
        errors: list[str]=[]
        for candidate in candidates:
            try:
                waypoints=_validated_path_waypoints(
                    namespace,
                    start=start,
                    finish=candidate,
                )
            except Exception as exc:  # noqa: BLE001 - path probe evidence
                errors.append(f"{candidate!r}:{type(exc).__name__}:{exc}")
                continue
            chosen=candidate
            chosen_waypoints=waypoints
            break
        if chosen is None or chosen_waypoints is None:
            raise RuntimeError(
                f"no path-validated bootstrap target for {resource}: "
                +"; ".join(errors[-5:])
            )
        row=dict(template)
        row["position"]={"x":chosen[0],"y":chosen[1]}
        row["validated_path_waypoints"]=chosen_waypoints
        route.append(row)
        start=chosen
    return tuple(route),start


IRON_TRANSITION_ENTRY_STATUSES=frozenset({"working","no_fuel"})


def _iron_transition_entry(iron_row: Mapping[str,Any]) -> dict[str,Any]:
    status=str(iron_row.get("status") or "")
    if status not in IRON_TRANSITION_ENTRY_STATUSES:
        raise RuntimeError(
            "promoted iron extractor has unsupported transition state: "
            f"{status!r}"
        )
    raw_coal_fuel=iron_row.get("coal_fuel",0)
    raw_fuel_remaining=iron_row.get("fuel_remaining",0)
    coal_fuel=(
        float(raw_coal_fuel)
        if isinstance(raw_coal_fuel,(int,float))
        and not isinstance(raw_coal_fuel,bool)
        else 0.0
    )
    fuel_remaining=(
        float(raw_fuel_remaining)
        if isinstance(raw_fuel_remaining,(int,float))
        and not isinstance(raw_fuel_remaining,bool)
        else 0.0
    )
    return {
        "status":status,
        "coal_fuel":coal_fuel,
        "fuel_remaining":fuel_remaining,
        "requires_endogenous_refuel":status=="no_fuel",
    }


def preflight_coal(
    *,
    artifact: Path,
    revision: Mapping[str,Any],
    ledger_path: Path=DEFAULT_LEDGER,
    service_state_reader: ServiceStateReader=evolution_service_state,
    phase_state_reader: PhaseStateReader=phase_state,
) -> dict[str,Any]:
    if artifact.exists():
        raise FileExistsError(
            "canonical coal continuation artifact already exists; inspect it"
        )
    if revision.get("dirty") is not False:
        raise RuntimeError("F5-C coal requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C coal code revision is unavailable")

    state=dict(phase_state_reader())
    protocol=state.get("phase5_protocol")
    bridge=state.get("phase5_authority_bridge")
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(protocol,Mapping):
        protocol={}
    if not isinstance(bridge,Mapping):
        bridge={}
    if not isinstance(baseline,Mapping):
        baseline={}
    achieved=protocol.get("achieved_capabilities")
    if not isinstance(achieved,list):
        achieved=[]
    if (
        state.get("phase")!="F5"
        or state.get("phase5_checkpoint")!="F5-C"
        or state.get("phase5_next_checkpoint")!="F5-C"
        or baseline.get("iron_extraction_validated") is not True
        or achieved!=["iron_extraction"]
        or bridge.get("validated") is not True
        or bridge.get("ambient_authority")!="A0"
        or bridge.get("max_bounded_grant")!="A2"
        or bridge.get("max_executions")!=1
        or bridge.get("continuous_authority") is not False
    ):
        raise RuntimeError(
            "coal continuation requires F5-C with only iron_extraction promoted "
            "and validated F5-B A0/A2 boundary"
        )

    build=_load_object(BUILD_INFO)
    if build.get("commit")!=commit or build.get("dirty") is not False:
        raise RuntimeError(
            "coal continuation requires deployed dashboard/runtime at exact HEAD"
        )

    evolution=_assert_evolution_off(service_state_reader)
    lease=(
        _load_object(WORLD_LEASE_STATE)
        if WORLD_LEASE_STATE.exists()
        else {"status":"absent"}
    )
    if lease.get("status")=="active":
        raise RuntimeError(f"active WorldLease blocks coal continuation: {lease}")
    if _intervention_count()!=0:
        raise RuntimeError("F5 intervention ledger is not empty")
    ledger_state=_assert_ledger_writable(ledger_path)

    (
        iron_extractor,
        iron_buffer,
        iron_artifact,
        base_seed,
    )=_positions_from_iron_artifact(state)
    observer=FactorioObserver()
    try:
        snapshot=observer.snapshot()
    finally:
        observer.close()
    if snapshot.get("connected") is not True:
        raise RuntimeError("canonical WORLD snapshot unavailable")
    rows=snapshot.get("entities")
    iron_row=_entity_at(
        rows,
        name="burner-mining-drill",
        position=iron_extractor,
    )
    buffer_row=_entity_at(rows,name="wooden-chest",position=iron_buffer)
    if iron_row is None or buffer_row is None:
        raise RuntimeError("promoted iron entities are absent from live WORLD")
    iron_entry=_iron_transition_entry(iron_row)

    return {
        "status":"preflight_pass",
        "world_mutation":False,
        "grant_issued":False,
        "option_executed_live":False,
        "world_reset":False,
        "code_revision":dict(revision),
        "build_info":build,
        "phase_state":{
            "phase":"F5",
            "phase5_checkpoint":"F5-C",
            "phase5_next_checkpoint":"F5-C",
            "achieved_capabilities":["iron_extraction"],
        },
        "evolution":evolution,
        "world_lease_state":lease,
        "authority_ledger":ledger_state,
        "intervention_count":0,
        "base_seed":base_seed,
        "iron_artifact":str(iron_artifact),
        "incumbent_iron_extractor_position":{
            "x":iron_extractor[0],"y":iron_extractor[1],
        },
        "incumbent_iron_buffer_position":{
            "x":iron_buffer[0],"y":iron_buffer[1],
        },
        "incumbent_iron_entry_status":iron_entry["status"],
        "incumbent_iron_entry_coal_fuel":iron_entry["coal_fuel"],
        "incumbent_iron_entry_fuel_remaining":iron_entry["fuel_remaining"],
        "incumbent_iron_transition_requires_endogenous_refuel":(
            iron_entry["requires_endogenous_refuel"]
        ),
        "world_entity_count":snapshot.get("entity_count"),
    }


def _selected_base_seed(
    reader: PhaseStateReader=phase_state,
) -> int:
    state=dict(reader())
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(baseline,Mapping):
        raise TypeError("F5-C deterministic baseline state must be an object")
    seed=baseline.get("seed")
    if (
        not isinstance(seed,int)
        or isinstance(seed,bool)
        or seed<=0
        or baseline.get("iron_extraction_validated") is not True
    ):
        raise RuntimeError("F5-C promoted iron base seed is unavailable")
    return seed


def _option_request(
    *,
    run_id: str,
    commit: str,
    option_seconds: int,
) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:coal-self-sufficiency",
        kind=OptionKind.ESTABLISH_COAL_SELF_SUFFICIENCY,
        goal=(
            "establish endogenous coal while preserving promoted iron extraction"
        ),
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_coal_self_sufficiency",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(
            requested_ticks=option_seconds*int(TICKS_PER_SECOND)
        ),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:coal-self-sufficiency-action",
        family=ActionFamily.PLACEMENT,
        intent="establish endogenous coal and preserve incumbent iron",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_coal_self_sufficiency",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=("iron_extraction","observed_world_resources"),
        provides=("coal_self_sufficiency",),
    )


def _namespace_measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    placement=prepared.preflight.get("placement")
    resource_tiles=(
        placement.get("resource_tiles")
        if isinstance(placement,dict)
        else None
    )
    return {
        "coal_resource_patch_valid":(
            isinstance(resource_tiles,int)
            and not isinstance(resource_tiles,bool)
            and resource_tiles>0
        ),
        "coal_mined":bool(getattr(namespace,"cortex_coal_mined",False)),
        "endogenous_coal_reaches_fuel_consumer":bool(
            getattr(
                namespace,
                "cortex_endogenous_coal_reaches_fuel_consumer",
                False,
            )
        ),
        "external_bootstrap_fuel_retired":bool(
            getattr(namespace,"cortex_external_bootstrap_fuel_retired",False)
        ),
        "coal_extractor_exists":bool(
            getattr(namespace,"cortex_coal_extractor_exists",False)
        ),
        "coal_endogenous_growth":float(
            getattr(namespace,"cortex_coal_endogenous_growth",0) or 0
        ),
        "incumbent_iron_survives":bool(
            getattr(namespace,"cortex_incumbent_iron_survives",False)
        ),
        "incumbent_iron_buffer_growth":float(
            getattr(namespace,"cortex_incumbent_iron_buffer_growth",0) or 0
        ),
        "bootstrap_total":float(
            getattr(namespace,"cortex_bootstrap_total",0) or 0
        ),
        "bootstrap_quarantine":float(
            getattr(namespace,"cortex_bootstrap_quarantine_count",0) or 0
        ),
        "seed_fuel_remaining":float(
            getattr(namespace,"cortex_seed_fuel_remaining",0) or 0
        ),
        "seed_phase_count":float(
            getattr(namespace,"cortex_seed_phase_count",0) or 0
        ),
        "incumbent_iron_bootstrap_removed":float(
            getattr(
                namespace,
                "cortex_incumbent_iron_bootstrap_removed",
                0,
            )
            or 0
        ),
        "incumbent_iron_bootstrap_remaining":float(
            getattr(
                namespace,
                "cortex_incumbent_iron_bootstrap_inventory_remaining",
                0,
            )
            or 0
        ),
        "endogenous_transfer":float(
            getattr(namespace,"cortex_endogenous_transfer",0) or 0
        ),
        "coal_self_refuel":float(
            getattr(namespace,"cortex_coal_self_refuel",0) or 0
        ),
        "iron_endogenous_refuel":float(
            getattr(namespace,"cortex_iron_endogenous_refuel",0) or 0
        ),
    }


def _checkpoint_digest(game_state: Any) -> str:
    raw=game_state.to_raw()
    if not isinstance(raw,str):
        raw=str(raw)
    return hashlib.sha256(raw.encode()).hexdigest()


def run_coal(
    *,
    artifact: Path | None=None,
    ledger_path: Path=DEFAULT_LEDGER,
    option_seconds: int=DEFAULT_OPTION_SECONDS,
    grant_ttl_seconds: int=DEFAULT_GRANT_TTL_SECONDS,
    revision: Mapping[str,Any] | None=None,
    service_state_reader: ServiceStateReader=evolution_service_state,
    phase_state_reader: PhaseStateReader=phase_state,
) -> dict[str,Any]:
    revision=dict(code_revision() if revision is None else revision)
    commit_value=revision.get("commit")
    if not isinstance(commit_value,str) or not commit_value:
        raise RuntimeError("F5-C coal code revision is unavailable")
    selected_seed=_selected_base_seed(phase_state_reader)
    artifact=artifact or artifact_for_revision(selected_seed,commit_value)
    preflight=preflight_coal(
        artifact=artifact,
        revision=revision,
        ledger_path=ledger_path,
        service_state_reader=service_state_reader,
        phase_state_reader=phase_state_reader,
    )
    commit=str(revision["commit"])
    iron_position=preflight["incumbent_iron_extractor_position"]
    buffer_position=preflight["incumbent_iron_buffer_position"]
    iron_extractor=(float(iron_position["x"]),float(iron_position["y"]))
    iron_buffer=(float(buffer_position["x"]),float(buffer_position["y"]))
    run_id="cortex-f5c-coal-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "capability":"coal_self_sufficiency",
        "base_seed":preflight["base_seed"],
        "run_id":run_id,
        "code_revision":revision,
        "preflight":preflight,
        "ambient_authority":"A0",
        "bounded_authority":"A2",
        "continuous_authority":False,
        "automatic_retry":False,
        "option_execution_attempts":0,
        "world_reset":False,
        "external_resource_injection":False,
        "human_intervention_count":0,
        "started_at":utc_now(),
    }

    env=None
    with FactorioWorldLease(
        run_id=run_id,
        arena=ARENA,
        owner=OWNER,
    ) as lease:
        record["world_lease"]=dict(lease.active_attestation())
        record["evolution_after_lease"]=_assert_evolution_off(
            service_state_reader
        )
        try:
            from fle.commons.models.game_state import GameState

            env=attach_live_factorio_environment()
            record["live_environment"]={
                "mode":"attach_existing_world",
                "task_setup_called":False,
                "world_reset":False,
            }
            record["fle_eval_timeout_s"]=enforce_minimum_eval_timeout(
                env,
                minimum_seconds=300,
            )
            record["fle_path_retry_floor"]=enforce_pathfinding_retry_floor(
                env,
                minimum_attempts=40,
            )
            record["fle_transactional_reposition_tool"]=bind_fast_reposition_tool(
                env
            )
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"coal_self_sufficiency",
                    "run_status":record.get("status"),
                    "progress":"F5-C",
                },
            )
            instance=env.unwrapped.instance
            namespace=instance.namespace

            observer_after_attach=FactorioObserver()
            try:
                attached_snapshot=observer_after_attach.snapshot()
            finally:
                observer_after_attach.close()
            attached_rows=attached_snapshot.get("entities")
            if (
                _entity_at(
                    attached_rows,
                    name="burner-mining-drill",
                    position=iron_extractor,
                ) is None
                or _entity_at(
                    attached_rows,
                    name="wooden-chest",
                    position=iron_buffer,
                ) is None
            ):
                raise RuntimeError(
                    "live FLE attachment changed the promoted iron WORLD before A2"
                )
            record["world_after_live_attachment"]={
                "entity_count":attached_snapshot.get("entity_count"),
                "incumbent_iron_present":True,
                "world_reset":False,
            }

            checkpoint=GameState.from_instance(instance)
            executor.game_state=checkpoint
            record["rollback_checkpoint"]={
                "source":"fle.commons.models.game_state.GameState.from_instance",
                "sha256":_checkpoint_digest(checkpoint),
                "captured_at":utc_now(),
                "world_reset":False,
            }
            record["status"]="rollback_checkpoint_captured"
            _atomic_json(artifact,record)

            observer=FactorioObserver()
            try:
                overview=observer.resource_overview(max_age_s=0.0)
            finally:
                observer.close()
            resources=resource_survey_from_overview(overview)
            if not resources.tiles:
                raise RuntimeError("coal continuation resource overview is empty")
            record["resource_observation"]={
                "source":"FactorioObserver.resource_overview",
                "connected":overview.get("connected"),
                "center":overview.get("center"),
                "radius":overview.get("radius"),
                "point_count":len(overview.get("points") or []),
                "totals":overview.get("totals"),
            }

            bootstrap_route,bootstrap_route_end=_plan_bootstrap_route(
                namespace,
                overview,
            )
            record["bootstrap_route"]=[dict(row) for row in bootstrap_route]
            record["bootstrap_path_waypoints_total"]=sum(
                int(row["validated_path_waypoints"])
                for row in bootstrap_route
            )

            physical_rows=world_rows(namespace,resources=False)
            footprints=runtime_entity_footprints(instance)
            option=_option_request(
                run_id=run_id,
                commit=commit,
                option_seconds=option_seconds,
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_coal_self_sufficiency_option(
                option,
                action_request=action,
                world_entities=physical_rows,
                resources=resources,
                incumbent_iron_extractor_position=iron_extractor,
                incumbent_iron_buffer_position=iron_buffer,
                bootstrap_resources=bootstrap_route,
                footprints=footprints,
            )
            if not composed.ready or composed.plan is None:
                refusal=(
                    None if composed.refusal is None
                    else composed.refusal.to_dict()
                )
                raise RuntimeError(f"coal Option composition refused: {refusal}")
            plan=composed.plan
            target=plan.prepared.preflight.get("target_position")
            if not isinstance(target,Mapping):
                raise TypeError("coal Option target_position is unavailable")
            target_position=(float(target["x"]),float(target["y"]))
            record["coal_target_validated_path_waypoints"]=_validated_path_waypoints(
                namespace,
                start=bootstrap_route_end,
                finish=target_position,
            )
            record["coal_target_position"]={
                "x":target_position[0],
                "y":target_position[1],
            }
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
                    "F5-C deterministic continuation: exactly one coal "
                    "self-sufficiency Option preserving iron_extraction"
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
                    "coal A2 validation refused: "
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
            _atomic_json(artifact,record)

            record["evolution_before_execute"]=_assert_evolution_off(
                service_state_reader
            )
            if lease.active_attestation()["scope_id"]!=scope.world_lease_id:
                raise RuntimeError("coal WorldLease scope changed before execution")
            record["option_execution_attempts"]=1
            _atomic_json(artifact,record)

            execution=bridge.execute_a2(
                plan,
                grant=grant,
                scope=scope,
                executor=executor,
                measure=lambda prepared:_namespace_measure(namespace,prepared),
                tick_source=env,
                use_checkpoint_for_action=False,
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
                "coal_mined":after["coal_mined"] is True,
                "endogenous_coal_reaches_fuel_consumer":(
                    after["endogenous_coal_reaches_fuel_consumer"] is True
                ),
                "external_bootstrap_fuel_retired":(
                    after["external_bootstrap_fuel_retired"] is True
                ),
            }
            survival_gate={
                "previously_promoted":["iron_extraction"],
                "iron_extraction_survives":(
                    after["incumbent_iron_survives"] is True
                    and after["incumbent_iron_buffer_growth"]>0
                ),
            }
            survival_gate["regressed"]=(
                []
                if survival_gate["iron_extraction_survives"]
                else ["iron_extraction"]
            )
            survival_gate["passed"]=not survival_gate["regressed"]
            interventions=executor.intervention_snapshot()
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
                    "coal_self_sufficiency" if promoted else None
                ),
                "transaction_committed":promoted,
                "intervention_snapshot":interventions,
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
                        "promoted":(
                            ["coal_self_sufficiency"] if promoted else []
                        ),
                        "regressed":survival_gate["regressed"],
                    },
                    "rollback":not promoted,
                    "next_state":after,
                },
            })
            _atomic_json(artifact,record)
            return record
        except Exception as exc:  # noqa: BLE001 - persist opaque FLE failure
            record.update({
                "status":"failed",
                "finished_at":utc_now(),
                "failure":{
                    "type":type(exc).__name__,
                    "message":str(exc),
                },
            })
            _atomic_json(artifact,record)
            return record
        finally:
            if env is not None:
                env.close()

    return record


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--artifact",type=Path,default=None)
    parser.add_argument("--ledger",type=Path,default=DEFAULT_LEDGER)
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
    args=parser.parse_args()
    revision=code_revision()
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C coal code revision is unavailable")
    selected_seed=_selected_base_seed()
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for_revision(selected_seed,commit).resolve()
    )
    if not args.execute:
        payload=preflight_coal(
            artifact=artifact,
            revision=revision,
            ledger_path=args.ledger,
        )
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_coal(
        artifact=artifact,
        ledger_path=args.ledger,
        option_seconds=args.option_seconds,
        grant_ttl_seconds=args.grant_ttl_seconds,
        revision=revision,
    )
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
