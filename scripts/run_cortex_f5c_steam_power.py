#!/usr/bin/env python3
"""F5-C deterministic steam-power capability runner."""

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

from fle.env.game_types import Prototype, Resource

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
from factorio_ai_lab.cortex.spatial_validation import validate_observed_route
from factorio_ai_lab.cortex.steam_power_option import (
    INITIAL_COAL_DRAW,
    IRON_TRIGGER_ORE_DRAW,
    compose_steam_power_option,
)
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
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

SCHEMA_VERSION="cortex_f5c_steam_power_v1"
DEFAULT_LEDGER=RUNS_DIR/"authority"/"cortex_option_grants.sqlite3"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
DASHBOARD_BUILD_INFO=Path(
    "/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json"
)
RUNTIME_BUILD_INFO=Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
ARENA="cortex_f5c_steam_power"
OWNER="run_cortex_f5c_steam_power"
DEFAULT_OPTION_SECONDS=360
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
            x=float(pos["x"])
            y=float(pos["y"])
        except (KeyError,TypeError,ValueError):
            continue
        if abs(x-position[0])<=tolerance and abs(y-position[1])<=tolerance:
            return row
    return None


def _contents_count(row: Mapping[str,Any],item: str) -> float:
    total=0.0
    for key in ("contents","craft_input","craft_output","fuel"):
        raw=row.get(key)
        if not isinstance(raw,list):
            continue
        for stack in raw:
            if not isinstance(stack,Mapping) or stack.get("name")!=item:
                continue
            value=stack.get("count")
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                total+=float(value)
    return total


def _selected_positions(
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
    return (
        seed,
        iron_extractor,
        iron_buffer,
        coal_extractor,
        coal_buffer,
        iron_furnace,
    )


def artifact_for(seed: int,commit: str) -> Path:
    return (
        RUNS_DIR/"audits"/
        f"cortex_f5c_continuation_{seed}_steam_power_{commit[:12]}.json"
    )


def _canonical_character(snapshot: Mapping[str,Any]) -> tuple[float,float]:
    rows=snapshot.get("entities")
    if not isinstance(rows,list):
        raise TypeError("canonical WORLD entities unavailable")
    for row in rows:
        if not isinstance(row,Mapping) or row.get("name")!="character":
            continue
        pos=row.get("position")
        if not isinstance(pos,Mapping):
            continue
        return float(pos["x"]),float(pos["y"])
    raise RuntimeError("canonical WORLD character unavailable")


def _nearest_resource(
    overview: Mapping[str,Any],
    *,
    name: str,
    origin: tuple[float,float],
) -> tuple[float,float]:
    points=overview.get("points")
    rows=points if isinstance(points,list) else []
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
        raise RuntimeError("F5-C steam power requires clean committed source")
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C steam power commit unavailable")
    if artifact.exists():
        raise FileExistsError("F5-C steam power artifact already exists for commit")

    state=dict(phase_state_reader())
    protocol=state.get("phase5_protocol")
    bridge=state.get("phase5_authority_bridge")
    if not isinstance(protocol,Mapping) or not isinstance(bridge,Mapping):
        raise TypeError("F5 protocol/authority state unavailable")
    achieved=protocol.get("achieved_capabilities")
    expected=["iron_extraction","coal_self_sufficiency","iron_smelting"]
    if (
        state.get("phase")!="F5"
        or state.get("phase5_checkpoint")!="F5-C"
        or achieved!=expected
        or bridge.get("validated") is not True
        or bridge.get("ambient_authority")!="A0"
        or bridge.get("max_bounded_grant")!="A2"
    ):
        raise RuntimeError(
            "steam power requires F5-C with iron+coal+smelting promoted and A0/A2 boundary"
        )

    dashboard_build=_load(DASHBOARD_BUILD_INFO)
    runtime_build=_load(RUNTIME_BUILD_INFO)
    for label,build in (
        ("dashboard",dashboard_build),
        ("runtime",runtime_build),
    ):
        if build.get("commit")!=commit or build.get("dirty") is not False:
            raise RuntimeError(
                f"steam power requires deployed {label} at exact HEAD"
            )

    evolution=_assert_evolution_off(service_state_reader)
    lease=world_lease_state()
    if lease.get("status")=="active":
        raise RuntimeError(f"active WorldLease blocks steam power: {lease}")
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
        iron_furnace,
    )=_selected_positions(state)

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
            raise RuntimeError(
                f"promoted {name} at {pos!r} absent from live WORLD"
            )
        found[(name,pos)]=row

    iron_stock=_contents_count(found[("wooden-chest",iron_buffer)],"iron-ore")
    coal_stock=_contents_count(found[("wooden-chest",coal_buffer)],"coal")
    if iron_stock<IRON_TRIGGER_ORE_DRAW:
        raise RuntimeError(
            f"endogenous iron stock below {IRON_TRIGGER_ORE_DRAW}: {iron_stock}"
        )
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
        "persistent_copper_chain":False,
        "code_revision":revision,
        "dashboard_build_info":dashboard_build,
        "runtime_build_info":runtime_build,
        "phase_state":{
            "phase":"F5",
            "phase5_checkpoint":"F5-C",
            "phase5_next_checkpoint":"F5-C",
            "achieved_capabilities":expected,
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
        "iron_stock":iron_stock,
        "coal_stock":coal_stock,
        "canonical_character_position":{
            "x":_canonical_character(snapshot)[0],
            "y":_canonical_character(snapshot)[1],
        },
        "world_entity_count":snapshot.get("entity_count"),
    }


def _option_request(*,run_id: str,commit: str,seconds: int) -> OptionRequest:
    return OptionRequest(
        option_id=f"{run_id}:steam-power",
        kind=OptionKind.ESTABLISH_STEAM_POWER,
        goal=(
            "establish native-trigger steam power and a supplied electric consumer "
            "while preserving iron, coal and smelting"
        ),
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_steam_power",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=seconds*int(TICKS_PER_SECOND)),
        authority=ActionAuthority.SHADOW,
    )


def _action_request(*,run_id: str,commit: str) -> ActionRequest:
    return ActionRequest(
        action_id=f"{run_id}:steam-power-action",
        family=ActionFamily.PLACEMENT,
        intent="establish endogenous steam power with native technology triggers",
        provenance=ActionProvenance(
            requested_by="f5-c-deterministic-baseline",
            source_component="scripts.run_cortex_f5c_steam_power",
            code_revision=commit,
            run_id=run_id,
        ),
        requires=(
            "iron_extraction",
            "coal_self_sufficiency",
            "iron_smelting",
        ),
        provides=("steam_power",),
    )


def _measure(namespace: Any,prepared: Any) -> dict[str,Any]:
    del prepared
    numeric=(
        "water_amount",
        "steam_amount",
        "steam_engine_energy",
        "electric_consumer_energy",
        "power_tap_count",
        "consumer_electrical_id",
        "trigger_iron_plates",
        "trigger_copper_plates",
        "infrastructure_iron_after_trigger",
        "water_pipe_required",
        "steam_pipe_required",
        "pipe_required_total",
        "pipe_available_before",
        "pipe_to_craft",
        "pipe_iron_shortfall",
        "pipe_topup_ore",
        "pipe_topup_coal",
        "pipe_topup_plates",
        "pipe_topup_window",
        "pipe_inventory_ready",
        "iron_survival_growth",
        "coal_survival_growth",
        "smelting_survival_growth",
    )
    values={
        "water_source_valid":bool(
            getattr(namespace,"cortex_water_source_valid",False)
        ),
        "endogenous_fuel_reachable":bool(
            getattr(namespace,"cortex_endogenous_fuel_reachable",False)
        ),
        "steam_generated":bool(
            getattr(namespace,"cortex_steam_generated",False)
        ),
        "electrical_production_positive":bool(
            getattr(namespace,"cortex_electrical_production_positive",False)
        ),
        "electric_consumer_supplied":bool(
            getattr(namespace,"cortex_electric_consumer_supplied",False)
        ),
        "steam_engine_exists":bool(
            getattr(namespace,"cortex_steam_engine_exists",False)
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
    }
    for name in numeric:
        values[name]=float(getattr(namespace,f"cortex_{name}",0) or 0)
    return values


def _checkpoint_digest(checkpoint: Any) -> str:
    return hashlib.sha256(checkpoint.to_raw().encode()).hexdigest()


def run_steam_power(
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
    def pos(key: str) -> tuple[float,float]:
        raw=pf[key]
        return float(raw["x"]),float(raw["y"])

    iron_extractor=pos("incumbent_iron_extractor_position")
    iron_buffer=pos("incumbent_iron_buffer_position")
    coal_extractor=pos("incumbent_coal_extractor_position")
    coal_buffer=pos("incumbent_coal_buffer_position")
    iron_furnace=pos("incumbent_iron_furnace_position")
    canonical_character=pos("canonical_character_position")

    run_id="cortex-f5c-steam-power-"+datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record: dict[str,Any]={
        "schema_version":SCHEMA_VERSION,
        "status":"starting",
        "capability":"steam_power",
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
        "persistent_copper_chain":False,
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
                env,minimum_seconds=600
            )
            record["fle_transactional_reposition_tool"]=bind_fast_reposition_tool(env)
            executor=TransactionalFLEExecutor(
                env,
                runtime_context=lambda:{
                    "run_id":run_id,
                    "arena":ARENA,
                    "stage":"steam_power",
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
            for name,position in (
                ("burner-mining-drill",iron_extractor),
                ("wooden-chest",iron_buffer),
                ("burner-mining-drill",coal_extractor),
                ("wooden-chest",coal_buffer),
                ("stone-furnace",iron_furnace),
            ):
                if _entity_at(rows,name=name,position=position) is None:
                    raise RuntimeError(
                        "live FLE attachment changed promoted WORLD before steam-power A2"
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
                raise RuntimeError("steam power resource overview is empty")
            physical_rows=world_rows(namespace,resources=False)
            footprints=runtime_entity_footprints(instance)

            stone=_nearest_resource(
                overview,name="stone",origin=canonical_character
            )
            copper=_nearest_resource(
                overview,name="copper-ore",origin=stone
            )
            water_entity=namespace.nearest(Resource.Water)
            wood_entity=namespace.nearest(Prototype.Wood)
            water=(float(water_entity.x),float(water_entity.y))
            wood=(float(wood_entity.x),float(wood_entity.y))

            record["planning_stage"]="route_validation"
            _write(artifact,record)
            routes={
                "stone":_observed_route_validation(
                    start=canonical_character,
                    finish=stone,
                    world_entities=physical_rows,
                    footprints=footprints,
                ),
                "copper":_observed_route_validation(
                    start=stone,
                    finish=copper,
                    world_entities=physical_rows,
                    footprints=footprints,
                ),
                "wood":_observed_route_validation(
                    start=copper,
                    finish=wood,
                    world_entities=physical_rows,
                    footprints=footprints,
                ),
                "water":_observed_route_validation(
                    start=wood,
                    finish=water,
                    world_entities=physical_rows,
                    footprints=footprints,
                ),
            }
            record["route_validations"]=routes
            record["bootstrap_targets"]={
                "stone":{"x":stone[0],"y":stone[1]},
                "copper":{"x":copper[0],"y":copper[1]},
                "wood":{"x":wood[0],"y":wood[1]},
                "water":{"x":water[0],"y":water[1]},
            }
            record["planning_stage"]="routes_validated"
            _write(artifact,record)

            option=_option_request(
                run_id=run_id,commit=commit,seconds=option_seconds
            )
            action=_action_request(run_id=run_id,commit=commit)
            composed=compose_steam_power_option(
                option,
                action_request=action,
                incumbent_iron_extractor_position=iron_extractor,
                incumbent_iron_buffer_position=iron_buffer,
                incumbent_iron_furnace_position=iron_furnace,
                incumbent_coal_extractor_position=coal_extractor,
                incumbent_coal_buffer_position=coal_buffer,
                stone_position=stone,
                copper_position=copper,
                wood_position=wood,
                water_position=water,
                route_validations=routes,
            )
            if not composed.ready or composed.plan is None:
                refusal=None if composed.refusal is None else composed.refusal.to_dict()
                raise RuntimeError(f"steam power Option refused: {refusal}")
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
                reason=(
                    "F5-C deterministic steam_power preserving "
                    "iron+coal+smelting"
                ),
                ttl_seconds=grant_ttl_seconds,
            )
            validation=bridge.validate_a2(plan,grant=grant,scope=scope)
            if not validation.allowed:
                raise RuntimeError(
                    "steam power A2 validation refused: "
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
                raise RuntimeError("steam power WorldLease scope changed before execute")
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
                "water_source_valid":after["water_source_valid"] is True,
                "endogenous_fuel_reachable":(
                    after["endogenous_fuel_reachable"] is True
                ),
                "steam_generated":(
                    after["steam_generated"] is True
                    and after["steam_amount"]>0
                ),
                "electrical_production_positive":(
                    after["electrical_production_positive"] is True
                    and after["steam_engine_energy"]>0
                ),
                "electric_consumer_supplied":(
                    after["electric_consumer_supplied"] is True
                    and after["electric_consumer_energy"]>0
                ),
            }
            survival_gate={
                "previously_promoted":[
                    "iron_extraction",
                    "coal_self_sufficiency",
                    "iron_smelting",
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
            }
            survival_gate["regressed"]=[
                name
                for name,key in (
                    ("iron_extraction","iron_extraction_survives"),
                    ("coal_self_sufficiency","coal_self_sufficiency_survives"),
                    ("iron_smelting","iron_smelting_survives"),
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
                "capability_promoted":"steam_power" if promoted else None,
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
                        "promoted":["steam_power"] if promoted else [],
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
    args=parser.parse_args()

    revision=code_revision()
    commit=revision.get("commit")
    if not isinstance(commit,str) or not commit:
        raise RuntimeError("F5-C steam power code revision unavailable")
    state=phase_state()
    seed,*_=_selected_positions(state)
    artifact=(
        args.artifact.resolve()
        if args.artifact is not None
        else artifact_for(seed,commit).resolve()
    )
    if not args.execute:
        payload=preflight(artifact=artifact,revision=revision)
        print(json.dumps(payload,indent=2,sort_keys=True,default=str))
        return 0
    result=run_steam_power(artifact=artifact)
    print(json.dumps(result,indent=2,sort_keys=True,default=str))
    return 0 if result.get("status")=="completed" else 2


if __name__=="__main__":
    raise SystemExit(main())
