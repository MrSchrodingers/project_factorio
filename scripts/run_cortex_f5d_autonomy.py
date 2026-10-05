"""One bounded F5-D autonomous observe-decide-learn episode.

Default mode is A0 shadow. --execute-one may execute at most one currently
supported repair under an externally issued A2 grant. The learned policy ranks
options but cannot grant itself authority.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
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
from factorio_ai_lab.cortex.f5_trajectory import F5TypedTransition, append_transition
from factorio_ai_lab.cortex.f5d_autonomy import (
    AutonomyDecision,
    PersistentUCBPolicy,
    compact_state,
    decide,
    reward_components,
)
from factorio_ai_lab.cortex.f5d_maintenance_option import (
    compose_autonomous_refuel_option,
)
from factorio_ai_lab.cortex.f5d_material_link import (
    MaterialLinkPlan,
    plan_existing_processing_link,
)
from factorio_ai_lab.cortex.f5d_material_link_option import (
    compose_material_link_option,
)
from factorio_ai_lab.cortex.grant_ledger import PersistentOptionGrantLedger
from factorio_ai_lab.cortex.options import OptionBudget, OptionKind, OptionRequest
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    attach_live_factorio_environment,
    bind_safe_score_tool,
    bind_tick_accurate_sleep_tool,
    enforce_minimum_eval_timeout,
)
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog
from factorio_ai_lab.runtime import FactorioWorldLease, world_lease_state

TRAJECTORY_PATH = RUNS_DIR / "cortex_f5d_trajectories.jsonl"
POLICY_PATH = RUNS_DIR / "cortex_f5d_policy.json"
LEDGER_PATH = RUNS_DIR / "authority" / "cortex_option_grants.sqlite3"
PHASE_STATE = RUNS_DIR / "cortex_phase_state.json"
SERVER_SETTINGS = Path("/srv/factorio-ai-lab/.fle-local/config/server-settings.json")
RUNTIME_BUILD_INFO = Path("/srv/factorio-ai-runtime/current/BUILD_INFO.json")
DASHBOARD_BUILD_INFO = Path("/srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json")

ARENA = "cortex_f5d_autonomy"
OWNER = "run_cortex_f5d_autonomy"
SUPPORTED_LIVE_ACTIONS = frozenset(
    {
        "resupply:insert_fuel_from_world_container",
        "rebuild:reroute_producer_logistics",
    }
)
REFUEL_DOSE = 8
REFUEL_SOURCE_RESERVE = 100
OPTION_SECONDS = 60
GRANT_TTL_SECONDS = 180

F5_CAPABILITIES = [
    "iron_extraction",
    "coal_self_sufficiency",
    "iron_smelting",
    "steam_power",
    "copper_chain",
    "automation_science",
    "powered_manufacturing",
    "electric_mining",
    "logistic_science",
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def _service_state(service: str) -> dict[str, str]:
    def read(action: str) -> str:
        done = subprocess.run(
            ["systemctl", action, service],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        raw = (done.stdout or done.stderr or "").strip()
        return raw.splitlines()[0] if raw else f"returncode:{done.returncode}"

    return {"active": read("is-active"), "enabled": read("is-enabled")}


def _assert_evolution_off() -> dict[str, str]:
    state = _service_state("factorio-ai-evolution.service")
    if state != {"active": "inactive", "enabled": "disabled"}:
        raise RuntimeError(f"legacy evolution must remain OFF: {state!r}")
    return state


def _read_build_commit(path: Path) -> str:
    commit = _load(path).get("commit")
    if not isinstance(commit, str) or not commit:
        raise RuntimeError(f"missing build commit in {path}")
    return commit


def _remote_commit(branch: str) -> str:
    done = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return done.stdout.strip()


def _execution_preflight() -> dict[str, Any]:
    revision = code_revision()
    if revision.get("dirty") is not False:
        raise RuntimeError("F5-D execute requires a clean committed checkout")
    commit = revision.get("commit")
    branch = revision.get("branch")
    if not isinstance(commit, str) or not commit:
        raise RuntimeError("F5-D execute requires a git commit")
    if not isinstance(branch, str) or not branch:
        raise RuntimeError("F5-D execute requires a git branch")

    remote = _remote_commit(branch)
    runtime = _read_build_commit(RUNTIME_BUILD_INFO)
    dashboard = _read_build_commit(DASHBOARD_BUILD_INFO)
    if len({commit, remote, runtime, dashboard}) != 1:
        raise RuntimeError(
            "F5-D execute requires HEAD=remote=runtime=dashboard: "
            f"{commit=}, {remote=}, {runtime=}, {dashboard=}"
        )

    phase = _load(PHASE_STATE)
    protocol = phase.get("phase5_protocol")
    if not isinstance(protocol, Mapping):
        raise TypeError("phase5 protocol unavailable")
    if protocol.get("achieved_capabilities") != F5_CAPABILITIES:
        raise RuntimeError("F5-D requires the complete promoted F5-C baseline")
    if protocol.get("next_capability") is not None:
        raise RuntimeError("F5-D requires a closed F5-C capability frontier")
    if protocol.get("authority_level") != "A0":
        raise RuntimeError("F5-D ambient authority must remain A0")
    if protocol.get("continuous_authority") is not False:
        raise RuntimeError("F5-D continuous authority is forbidden")
    if world_lease_state().get("status") == "active":
        raise RuntimeError("F5-D requires no pre-existing active WorldLease")

    return {
        "revision": revision,
        "remote_commit": remote,
        "runtime_commit": runtime,
        "dashboard_commit": dashboard,
        "evolution": _assert_evolution_off(),
        "phase5_checkpoint": phase.get("phase5_checkpoint"),
        "phase5_complete": True,
        "continuous_authority": False,
    }


def _snapshot() -> dict[str, Any]:
    observer = FactorioObserver()
    try:
        return observer.snapshot()
    finally:
        observer.close()


def _game_knowledge() -> dict[str, Any]:
    observer = FactorioObserver()
    try:
        return observer.game_knowledge()
    finally:
        observer.close()


def _factory_fingerprint(snapshot: Mapping[str, Any]) -> list[list[Any]]:
    rows = snapshot.get("entities")
    result: list[list[Any]] = []
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return result
    for row in rows:
        if not isinstance(row, Mapping) or row.get("name") == "character":
            continue
        pos = row.get("position")
        if not isinstance(pos, Mapping):
            continue
        result.append(
            [
                str(row.get("name") or ""),
                round(float(pos.get("x") or 0), 3),
                round(float(pos.get("y") or 0), 3),
            ]
        )
    return sorted(result)


def _coal_count(entity: Mapping[str, Any]) -> int:
    rows = entity.get("contents")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return 0
    return sum(
        int(row.get("count") or 0)
        for row in rows
        if isinstance(row, Mapping) and row.get("name") == "coal"
    )


def _freeze_refuel_inputs(
    snapshot: Mapping[str, Any],
    target_ids: Sequence[str] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = snapshot.get("entities")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise TypeError("WORLD entity list unavailable")
    entities = [row for row in rows if isinstance(row, Mapping)]
    by_id = {
        f"u{int(row['unit_number'])}": row
        for row in entities
        if isinstance(row.get("unit_number"), int)
        and not isinstance(row.get("unit_number"), bool)
    }

    targets: list[dict[str, Any]] = []
    for identifier in target_ids or ():
        row = by_id.get(str(identifier))
        if row is None or row.get("status") != "no_fuel":
            continue
        if row.get("name") not in {
            "burner-mining-drill",
            "stone-furnace",
            "boiler",
        }:
            continue
        pos = row.get("position")
        if not isinstance(pos, Mapping):
            continue
        targets.append(
            {
                "node_id": str(identifier),
                "entity_name": str(row["name"]),
                "x": float(pos["x"]),
                "y": float(pos["y"]),
            }
        )
    if not targets:
        raise RuntimeError("selected refuel action has no currently starved targets")

    required = REFUEL_DOSE * len(targets) + REFUEL_SOURCE_RESERVE
    sources: list[tuple[int, float, float, Mapping[str, Any]]] = []
    for row in entities:
        if row.get("name") != "wooden-chest":
            continue
        coal = _coal_count(row)
        if coal < required:
            continue
        pos = row.get("position")
        if not isinstance(pos, Mapping):
            continue
        sources.append((coal, float(pos["y"]), float(pos["x"]), row))
    if not sources:
        raise RuntimeError(
            f"no endogenous coal chest holds required reserve {required}"
        )
    coal, _, _, row = max(
        sources,
        key=lambda value: (value[0], -value[1], -value[2]),
    )
    pos = row["position"]
    source = {
        "entity_name": "wooden-chest",
        "x": float(pos["x"]),
        "y": float(pos["y"]),
        "coal_before": coal,
        "selection": "richest_endogenous_coal_buffer",
    }
    return source, targets


def _option_requests(
    *,
    run_id: str,
    commit: str,
    selected: Any,
) -> tuple[OptionRequest, ActionRequest]:
    provenance = ActionProvenance(
        requested_by="f5d-autonomous-policy",
        source_component="scripts.run_cortex_f5d_autonomy",
        code_revision=commit,
        run_id=run_id,
        policy_version="cortex_f5d_online_policy_v1",
    )
    option = OptionRequest(
        option_id=f"{run_id}:maintenance",
        kind=OptionKind.AUTONOMOUS_MAINTENANCE,
        goal=(
            "repair the highest-ranked currently executable measured deficit "
            "using endogenous factory resources"
        ),
        provenance=provenance,
        budget=OptionBudget(
            requested_ticks=OPTION_SECONDS * int(TICKS_PER_SECOND)
        ),
        authority=ActionAuthority.SHADOW,
    )
    action = ActionRequest(
        action_id=f"{run_id}:maintenance-action",
        family=ActionFamily(selected.action.tool),
        intent=selected.action.intent,
        provenance=provenance,
        arguments=dict(selected.action.arguments),
        targets=tuple(selected.action.targets or ()),
        requires=tuple(selected.action.requires),
        provides=tuple(selected.action.provides),
    )
    return option, action


def _measure(namespace: Any, prepared: Any) -> dict[str, Any]:
    if prepared.binding == "cortex.f5d.autonomous_material_link":
        return {
            "autonomous_material_link_succeeded": bool(
                getattr(
                    namespace,
                    "cortex_autonomous_material_link_succeeded",
                    False,
                )
            ),
            "material_link_output_after": float(
                getattr(namespace, "cortex_material_link_output_after", 0) or 0
            ),
            "material_link_source_after": float(
                getattr(namespace, "cortex_material_link_source_after", 0) or 0
            ),
            "material_link_source_preflow": float(
                getattr(namespace, "cortex_link_source_preflow", 0) or 0
            ),
            "material_link_target_preflow": float(
                getattr(namespace, "cortex_link_target_preflow", 0) or 0
            ),
            "material_link_iron_drawn": float(
                getattr(namespace, "cortex_link_iron_drawn", 0) or 0
            ),
            "material_link_copper_drawn": float(
                getattr(namespace, "cortex_link_copper_drawn", 0) or 0
            ),
        }
    return {
        "autonomous_refuel_succeeded": bool(
            getattr(namespace, "cortex_autonomous_refuel_succeeded", False)
        ),
        "refuel_drawn": float(
            getattr(namespace, "cortex_refuel_drawn", 0) or 0
        ),
        "refuel_inserted": float(
            getattr(namespace, "cortex_refuel_inserted", 0) or 0
        ),
        "refuel_source_before": float(
            getattr(namespace, "cortex_refuel_source_before", 0) or 0
        ),
        "refuel_source_after": float(
            getattr(namespace, "cortex_refuel_source_after", 0) or 0
        ),
    }


def _manual_logistics_actions(intervention: Mapping[str, Any]) -> int:
    total=0
    for key in ("committed_repair","committed_infrastructure","committed"):
        row=intervention.get(key)
        if not isinstance(row,Mapping):
            continue
        value=row.get("manual_logistics_calls")
        if isinstance(value,(int,float)) and not isinstance(value,bool):
            total+=int(value)
    return total


def _resolve_live_candidate(
    snapshot: Mapping[str, Any],
    decision: AutonomyDecision,
    knowledge: Mapping[str, Any],
) -> tuple[Any | None, dict[str, Any] | None, list[dict[str, Any]]]:
    catalog=RuntimeFactorioCatalog(knowledge)
    feasible: list[tuple[Any,dict[str,Any]]]=[]
    rows: list[dict[str,Any]]=[]
    for candidate in decision.candidates:
        key=candidate.action.key
        if key not in SUPPORTED_LIVE_ACTIONS:
            continue
        context: dict[str,Any] | None=None
        refusal: str | None=None
        try:
            if key=="resupply:insert_fuel_from_world_container":
                source,targets=_freeze_refuel_inputs(
                    snapshot,
                    candidate.action.targets,
                )
                context={"kind":"refuel","source":source,"targets":targets}
            elif key=="rebuild:reroute_producer_logistics":
                link=plan_existing_processing_link(
                    snapshot,
                    action=candidate.action,
                    catalog=catalog,
                )
                if link is None:
                    refusal="no_endogenous_existing_processor_link"
                else:
                    context={"kind":"material_link","link":link}
            else:
                refusal="unsupported_live_action"
        except (KeyError,RuntimeError,TypeError,ValueError) as exc:
            refusal=f"{type(exc).__name__}:{exc}"

        rows.append({
            "action_key":key,
            "symptom":candidate.symptom,
            "feasible":context is not None,
            "refusal":refusal,
            "plan":(
                None
                if context is None
                else (
                    context["link"].to_dict()
                    if isinstance(context.get("link"),MaterialLinkPlan)
                    else {
                        "source":context.get("source"),
                        "targets":context.get("targets"),
                    }
                )
            ),
        })
        if context is not None:
            feasible.append((candidate,context))

    if not feasible:
        return None,None,rows
    selected,context=max(
        feasible,
        key=lambda row:(row[0].score,row[0].severity,row[0].action.key),
    )
    return selected,context,rows


def _configured_autosave_interval() -> int:
    value = _load(SERVER_SETTINGS).get("autosave_interval")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError("configured autosave interval unavailable")
    return value


def _open_control_rcon() -> Any:
    from factorio_rcon import RCONClient

    address = os.getenv("FACTORIO_SERVER_ADDRESS") or "127.0.0.1"
    port = int(os.getenv("FACTORIO_SERVER_PORT") or "27000")
    return RCONClient(address, port, "factorio")


def _autosave_interval(client: Any) -> int:
    response = client.send_command("/config get autosave-interval")
    text = "" if response is None else str(response).strip()
    if "disabled" in text.lower():
        return 0
    matches = re.findall(r"\b(\d+)\b", text)
    if not matches:
        raise RuntimeError(f"unable to parse autosave interval: {text!r}")
    return int(matches[-1])


def _set_autosave_interval(client: Any, minutes: int) -> dict[str, Any]:
    response = client.send_command(f"/config set autosave-interval {minutes}")
    text = "" if response is None else str(response).strip()
    observed = _autosave_interval(client)
    if observed != minutes:
        raise RuntimeError(
            f"autosave interval mismatch requested={minutes} observed={observed}"
        )
    return {
        "requested_minutes": minutes,
        "observed_minutes": observed,
        "response": text,
    }


def _ensure_unpaused(instance: Any) -> dict[str, Any]:
    response = instance.rcon_client.send_command(
        "/sc game.tick_paused=false; "
        "rcon.print(game.tick_paused and 'true' or 'false')"
    )
    text = "" if response is None else str(response).strip().lower()
    if text != "false":
        raise RuntimeError(f"failed to unpause Factorio: {text!r}")
    game_control = getattr(instance, "game_control", None)
    if game_control is not None and hasattr(game_control, "_is_paused"):
        game_control._is_paused = False
    return {"verified": True, "status": "unpaused"}


def _pause(instance: Any) -> dict[str, Any]:
    response = instance.rcon_client.send_command(
        "/sc game.tick_paused=true; "
        "rcon.print(game.tick_paused and 'true' or 'false')"
    )
    text = "" if response is None else str(response).strip().lower()
    if text != "true":
        raise RuntimeError(f"failed to pause Factorio: {text!r}")
    game_control = getattr(instance, "game_control", None)
    if game_control is not None and hasattr(game_control, "_is_paused"):
        game_control._is_paused = True
    return {"verified": True, "status": "paused"}


def _quiesce(instance: Any) -> dict[str, Any]:
    command = """/sc local seen={}
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
  for _,key in ipairs(drop) do value[key]=nil removed=removed+1 end
end
scrub(storage)
storage.__lua_script_checksums={}
rcon.print(helpers.table_to_json({ok=true,removed_functions=removed}))
"""
    response = instance.rcon_client.send_command(command)
    payload = json.loads("" if response is None else str(response))
    if payload.get("ok") is not True:
        raise RuntimeError(f"FLE storage quiesce failed: {payload!r}")
    return payload


def _server_save(instance: Any, name: str) -> str:
    response = instance.rcon_client.send_command(f"/server-save {name}")
    text = "" if response is None else str(response).strip()
    if "Saving the map" not in text:
        raise RuntimeError(f"server save failed: {text!r}")
    return text


def run_shadow() -> dict[str, Any]:
    snapshot = _snapshot()
    knowledge = _game_knowledge()
    policy = PersistentUCBPolicy(POLICY_PATH)
    decision = decide(snapshot, policy)
    state = compact_state(snapshot, decision.graph)
    priority = decision.selected
    executable, context, feasibility = _resolve_live_candidate(
        snapshot,
        decision,
        knowledge,
    )
    now = utc_now()
    transition = F5TypedTransition(
        transition_id=f"f5d-shadow-{now}",
        state=state,
        candidate_options=tuple(row.to_dict() for row in decision.candidates),
        memory_retrieval={
            "policy_state": decision.policy_snapshot,
            "source": "persistent_ucb",
            "priority_selected": (
                None if priority is None else priority.to_dict()
            ),
            "hard_feasibility": feasibility,
            "currently_executable": (
                None if executable is None else executable.to_dict()
            ),
        },
        selected_option=None if priority is None else priority.to_dict(),
        expected_effect=(
            None if priority is None else priority.action.prediction.to_dict()
        ),
        authority_level="A0",
        execution_trace={
            "status": "shadow",
            "executed": False,
            "reason": "F5-D policy selection has no mutation authority",
        },
        postconditions={"measured": False},
        capability_delta={"changed": False, "regressed": []},
        resource_cost={"world_mutation": False},
        rollback={"required": False},
        reward_components={"eligible": False, "reward": None},
        next_state=state,
        metadata={
            "observed_at": now,
            "code_revision": code_revision(),
            "continuous_authority": False,
            "policy_may_self_grant_authority": False,
            "resolved_plan": (
                None
                if context is None
                else (
                    context["link"].to_dict()
                    if isinstance(context.get("link"), MaterialLinkPlan)
                    else {
                        "source": context.get("source"),
                        "targets": context.get("targets"),
                    }
                )
            ),
        },
    )
    digest = append_transition(TRAJECTORY_PATH, transition)
    return {
        "status": "shadow_decision_recorded",
        "transition_id": transition.transition_id,
        "payload_sha256": digest,
        "state": state,
        "priority_selected": (
            None if priority is None else priority.to_dict()
        ),
        "currently_executable": (
            None if executable is None else executable.to_dict()
        ),
        "hard_feasibility": feasibility,
        "candidate_count": len(decision.candidates),
        "refusals": list(decision.refusals),
    }


def run_execute_one() -> dict[str, Any]:
    preflight = _execution_preflight()
    snapshot_before = _snapshot()
    knowledge = _game_knowledge()
    policy = PersistentUCBPolicy(POLICY_PATH)
    decision = decide(snapshot_before, policy)
    priority = decision.selected
    selected, context, feasibility = _resolve_live_candidate(
        snapshot_before,
        decision,
        knowledge,
    )
    if selected is None or context is None:
        raise RuntimeError("F5-D has no hard-feasible live candidate")

    commit = str(preflight["revision"]["commit"])
    run_id = (
        "f5d-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + commit[:12]
    )
    option, action = _option_requests(
        run_id=run_id,
        commit=commit,
        selected=selected,
    )

    kind=str(context["kind"])
    source: dict[str, Any] | None=None
    targets: list[dict[str, Any]]=[]
    link: MaterialLinkPlan | None=None
    if kind=="refuel":
        source=dict(context["source"])
        targets=[dict(row) for row in context["targets"]]
        composed=compose_autonomous_refuel_option(
            option,
            action_request=action,
            source=source,
            targets=targets,
            dose=REFUEL_DOSE,
            source_reserve=REFUEL_SOURCE_RESERVE,
        )
    elif kind=="material_link":
        raw_link=context.get("link")
        if not isinstance(raw_link,MaterialLinkPlan):
            raise TypeError("resolved material-link context is invalid")
        link=raw_link
        composed=compose_material_link_option(
            option,
            action_request=action,
            link=link,
        )
    else:
        raise RuntimeError(f"unsupported resolved F5-D context {kind!r}")

    if not composed.ready or composed.plan is None:
        refusal = (
            None if composed.refusal is None else composed.refusal.to_dict()
        )
        raise RuntimeError(f"F5-D autonomous Option refused: {refusal}")
    plan = composed.plan

    env = None
    instance = None
    executor = None
    control = None
    autosave_restore = _configured_autosave_interval()
    execution_payload: dict[str, Any] = {}
    cleanup: dict[str, Any] = {}
    accepted = False
    measurement: dict[str, Any] = {}
    intervention: dict[str, Any] = {}
    next_snapshot = snapshot_before
    rollback: dict[str, Any] = {}

    with FactorioWorldLease(run_id=run_id, arena=ARENA, owner=OWNER) as lease:
        try:
            control = _open_control_rcon()
            cleanup["autosave_suspend"] = _set_autosave_interval(control, 0)

            env = attach_live_factorio_environment()
            instance = env.unwrapped.instance
            cleanup["unpause"] = _ensure_unpaused(instance)
            enforce_minimum_eval_timeout(env, minimum_seconds=180)
            bind_safe_score_tool(env)
            bind_tick_accurate_sleep_tool(env)

            attached = _snapshot()
            if _factory_fingerprint(attached) != _factory_fingerprint(
                snapshot_before
            ):
                raise RuntimeError("FLE attachment changed WORLD before F5-D A2")

            checkpoint = GameState.from_instance(instance)
            executor = TransactionalFLEExecutor(
                env,
                runtime_context=lambda: {
                    "run_id": run_id,
                    "arena": ARENA,
                    "stage": "autonomous_maintenance",
                    "progress": "F5-D",
                },
            )
            executor.game_state = checkpoint
            namespace = instance.namespace

            ledger = PersistentOptionGrantLedger(LEDGER_PATH)
            bridge = F5BoundedAuthorityBridge(
                ledger=ledger,
                lease_attestor=lease.active_attestation,
            )
            scope, grant = bridge.issue_a2_grant(
                plan,
                experiment_id=run_id,
                reason=(
                    "F5-D autonomous policy selected a measured hard-feasible "
                    "action; authority remains external one-shot A2"
                ),
                ttl_seconds=GRANT_TTL_SECONDS,
            )
            validation = bridge.validate_a2(
                plan,
                grant=grant,
                scope=scope,
            )
            if not validation.allowed:
                raise RuntimeError(
                    "F5-D A2 validation refused: "
                    + json.dumps(validation.to_dict(), sort_keys=True)
                )

            execution = bridge.execute_a2(
                plan,
                grant=grant,
                scope=scope,
                executor=executor,
                measure=lambda prepared: _measure(namespace, prepared),
                tick_source=env,
                use_checkpoint_for_action=False,
            )
            execution_payload = execution.to_dict()
            result = execution.result
            accepted = (
                execution.executed
                and result is not None
                and result.status.value == "accepted"
                and result.changed_world is True
            )
            measurement = _measure(namespace, plan.prepared)
            intervention = executor.intervention_snapshot()
            rollback = executor.rollback_integrity_snapshot() or {}
            next_snapshot = _snapshot()
        finally:
            if instance is not None:
                try:
                    cleanup["pause"] = _pause(instance)
                    cleanup["quiesce"] = _quiesce(instance)
                    cleanup["save"] = _server_save(
                        instance,
                        (
                            "cortex-f5d-autonomy"
                            if accepted
                            else "cortex-f5d-autonomy-rollback"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    cleanup["persistence_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
            if env is not None:
                try:
                    env.close()
                except Exception as exc:  # noqa: BLE001
                    cleanup["env_close_error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }

            restore_client = control
            try:
                if restore_client is None:
                    restore_client = _open_control_rcon()
                cleanup["autosave_restore"] = _set_autosave_interval(
                    restore_client,
                    autosave_restore,
                )
            finally:
                if restore_client is not None:
                    close = getattr(restore_client, "close", None)
                    if callable(close):
                        try:
                            close()
                        except OSError:
                            pass

    graph_after = build_factory_graph(next_snapshot.get("entities") or [])
    state_before = compact_state(snapshot_before, decision.graph)
    state_after = compact_state(next_snapshot, graph_after)
    manual_actions = _manual_logistics_actions(intervention) if accepted else 0
    rewards = reward_components(
        state_before,
        state_after,
        manual_logistics_actions=manual_actions,
    )

    before_entities={
        tuple(row) for row in _factory_fingerprint(snapshot_before)
    }
    after_entities={
        tuple(row) for row in _factory_fingerprint(next_snapshot)
    }
    baseline_preserved=before_entities.issubset(after_entities)
    learning_eligible=accepted and baseline_preserved
    if learning_eligible:
        policy.update(selected.symptom, selected.action.key, rewards["total"])

    if kind=="refuel":
        resource_cost={
            "external_resource_injection":False,
            "manual_logistics_by_agent":manual_actions,
            "source_coal_before":None if source is None else source["coal_before"],
            "coal_requested":REFUEL_DOSE*len(targets),
            "intervention_counts":intervention,
        }
        autonomous_effect="fuel_recovery" if accepted else None
        plan_metadata={"source":source,"targets":targets}
    else:
        assert link is not None
        resource_cost={
            "external_resource_injection":False,
            "manual_logistics_by_agent":manual_actions,
            "construction_items":dict(link.construction_items),
            "plate_requirements":dict(link.plate_requirements),
            "bootstrap":dict(link.bootstrap),
            "intervention_counts":intervention,
        }
        autonomous_effect="persistent_material_link" if accepted else None
        plan_metadata={"material_link":link.to_dict()}

    regressed=[] if baseline_preserved else ["baseline_entity_missing"]
    transition = F5TypedTransition(
        transition_id=run_id,
        state=state_before,
        candidate_options=tuple(row.to_dict() for row in decision.candidates),
        memory_retrieval={
            "source": "persistent_ucb",
            "policy_state_before": decision.policy_snapshot,
            "priority_selected": (
                None if priority is None else priority.to_dict()
            ),
            "hard_feasibility":feasibility,
            "supported_action_keys": sorted(SUPPORTED_LIVE_ACTIONS),
        },
        selected_option=selected.to_dict(),
        expected_effect=selected.action.prediction.to_dict(),
        authority_level="A2",
        execution_trace={
            "executed": bool(execution_payload),
            "accepted": accepted,
            "authority": execution_payload,
            "cleanup": cleanup,
        },
        postconditions={
            **measurement,
            "accepted":accepted,
            "baseline_entities_preserved":baseline_preserved,
        },
        capability_delta={
            "promoted": [],
            "regressed": regressed,
            "autonomous_effect": autonomous_effect,
        },
        resource_cost=resource_cost,
        rollback={"required": not accepted, "integrity": rollback},
        reward_components={
            **rewards,
            "eligible_for_learning": learning_eligible,
            "policy_updated": learning_eligible,
        },
        next_state=state_after,
        metadata={
            "observed_at": utc_now(),
            "preflight": preflight,
            **plan_metadata,
            "continuous_authority": False,
            "policy_may_self_grant_authority": False,
            "priority_action_key": (
                None if priority is None else priority.action.key
            ),
            "executed_action_key": selected.action.key,
        },
    )
    digest = append_transition(TRAJECTORY_PATH, transition)
    return {
        "status": (
            "accepted_and_learned"
            if learning_eligible
            else ("accepted_not_learned" if accepted else "rejected_no_learning")
        ),
        "run_id": run_id,
        "payload_sha256": digest,
        "priority_selected": (
            None if priority is None else priority.to_dict()
        ),
        "executed": selected.to_dict(),
        "hard_feasibility":feasibility,
        "measurement": measurement,
        "reward_components": rewards,
        "baseline_entities_preserved":baseline_preserved,
        "policy_updated": learning_eligible,
        "state_before": state_before,
        "state_after": state_after,
        "cleanup": cleanup,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-one", action="store_true")
    args = parser.parse_args()
    result = run_execute_one() if args.execute_one else run_shadow()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
