#!/usr/bin/env python3
"""F2-G4B one-shot live Option canary.

Without --execute this script performs a read-only preflight and never creates a
Factorio environment, acquires a lease, issues a grant, or writes the canonical
artifact.

With --execute it may create one non-confirmatory controlled fixture. The
functional processing-chain mutation is authorized only through
OptionExecutionBoundary backed by the persistent one-shot grant ledger.
There is no retry loop and no scheduler.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import ActionAuthority, ActionProvenance
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.grant_ledger import (
    OptionExecutionGrant,
    OptionExecutionScope,
    PersistentOptionGrantLedger,
)
from factorio_ai_lab.cortex.live_canary import (
    available_inventory,
    build_measurement_probe,
    delivery_power_capability,
    patch_center,
    resource_survey_from_overview,
    structural_targets,
    validate_canary_seed,
    world_rows,
)
from factorio_ai_lab.cortex.option_execute import OptionExecutionBoundary
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    OptionKind,
    OptionRequest,
    compose_processing_chain_option,
)
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.instrumentation.runtime import runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    fast_reposition,
    list_environments,
)
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.repair_loop import (
    INTENT_PLACE_PROCESSING,
    TOOL_PLACEMENT,
    Prediction,
    RepairAction,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog
from factorio_ai_lab.runtime import WORLD_LEASE_STATE, FactorioWorldLease

DEFAULT_SEED = 424242
DEFAULT_BOOTSTRAP_SETTLE_SECONDS = 12
DEFAULT_STRUCTURAL_SETTLE_SECONDS = 10
DEFAULT_GRANT_TTL_SECONDS = 300
DEFAULT_ARTIFACT = (
    RUNS_DIR / "audits" / "cortex_f2g4b_option_live_canary.json"
)
DEFAULT_LEDGER = RUNS_DIR / "authority" / "cortex_option_grants.sqlite3"
ARENA = "cortex_f2g4b_option_live_canary"
OWNER = "run_cortex_option_live_canary"
SCHEMA_VERSION = "cortex_f2g4b_option_live_canary_v1"

ServiceStateReader = Callable[[], Mapping[str, str]]
PhaseStateReader = Callable[[], Mapping[str, Any]]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def phase_state() -> dict[str, Any]:
    path = RUNS_DIR / "cortex_phase_state.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _assert_g4a_checkpoint(
    reader: PhaseStateReader = phase_state,
) -> dict[str, Any]:
    state = dict(reader())
    authority = state.get("phase2_persistent_option_authority")
    if not isinstance(authority, dict):
        authority = {}
    if (
        state.get("phase") != "F2"
        or state.get("phase2_checkpoint") != "F2-G4A"
        or authority.get("validated") is not True
        or authority.get("world_mutation") is not False
        or authority.get("continuous_authority") is not False
        or authority.get("live_option_execute_authorized") is not False
    ):
        raise RuntimeError(
            "G4B requires published validated F2-G4A phase-state with "
            "no live execution authority"
        )
    return {
        "phase": state.get("phase"),
        "phase2_checkpoint": state.get("phase2_checkpoint"),
        "g4a_validated": True,
        "g4a_dry_run_run_id": authority.get("dry_run_run_id"),
        "g4a_dry_run_code_commit": authority.get("dry_run_code_commit"),
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def evolution_service_state() -> dict[str, str]:
    def run(*args: str) -> str:
        done = subprocess.run(
            ["systemctl", *args, "factorio-ai-evolution"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        text = (done.stdout or done.stderr or "").strip()
        return text.splitlines()[0] if text else f"returncode:{done.returncode}"

    return {
        "active": run("is-active"),
        "enabled": run("is-enabled"),
    }


def validate_canary_parameters(
    *,
    bootstrap_settle_seconds: int,
    structural_settle_seconds: int,
    grant_ttl_seconds: int,
) -> None:
    for label, value in (
        ("bootstrap_settle_seconds", bootstrap_settle_seconds),
        ("structural_settle_seconds", structural_settle_seconds),
        ("grant_ttl_seconds", grant_ttl_seconds),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")


def read_world_lease_state(path: Path = WORLD_LEASE_STATE) -> dict[str, Any]:
    if not path.exists():
        return {"status": "absent"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "cannot establish G4B WorldLease state: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise TypeError("cannot establish G4B WorldLease state: not an object")
    return payload


def _assert_evolution_off(
    reader: ServiceStateReader = evolution_service_state,
) -> dict[str, str]:
    state = dict(reader())
    active = str(state.get("active") or "")
    enabled = str(state.get("enabled") or "")
    if active != "inactive" or enabled != "disabled":
        raise RuntimeError(
            "G4B requires factorio-ai-evolution inactive+disabled; "
            f"got active={active!r} enabled={enabled!r}"
        )
    return {"active": active, "enabled": enabled}


def preflight_live_canary(
    *,
    seed: int,
    artifact: Path,
    revision: Mapping[str, Any],
    service_state_reader: ServiceStateReader = evolution_service_state,
    phase_state_reader: PhaseStateReader = phase_state,
    lease_state_path: Path = WORLD_LEASE_STATE,
) -> dict[str, Any]:
    """Read-only preflight. It does not create or modify the official artifact."""

    validate_canary_seed(seed)
    commit = revision.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        raise RuntimeError("G4B requires an exact code revision")
    if revision.get("dirty") is not False:
        raise RuntimeError("G4B requires a clean committed source tree")
    if artifact.exists():
        raise FileExistsError(
            "canonical G4B artifact already exists; inspect it before any "
            "new authorization attempt"
        )
    phase = _assert_g4a_checkpoint(phase_state_reader)
    lease_state = read_world_lease_state(lease_state_path)
    if str(lease_state.get("status") or "") == "active":
        raise RuntimeError(
            "G4B preflight found an active persisted Factorio WorldLease: "
            + json.dumps(lease_state, sort_keys=True, default=str)[:1200]
        )
    evolution = _assert_evolution_off(service_state_reader)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "preflight_pass",
        "seed": int(seed),
        "confirmatory_seed": False,
        "code_revision": dict(revision),
        "artifact": str(artifact),
        "phase_state": phase,
        "evolution": evolution,
        "world_lease_state": lease_state,
        "world_mutation": False,
        "grant_issued": False,
        "continuous_authority": False,
        "live_option_execute_authorized": False,
        "recorded_at": utc_now(),
    }


def _build_action_request(
    *,
    run_id: str,
    commit: str,
    targets: list[str],
):
    repair = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        prediction=Prediction(
            "physical_factory_graph.producers_reaching_processor",
            "increase",
        ),
        provides=("material",),
        targets=tuple(targets),
        arguments={"producers": targets},
    )
    return request_from_repair_action(
        repair,
        action_id=f"{run_id}:processing-action",
        provenance=ActionProvenance(
            requested_by="F2-G4B-live-option-canary",
            source_component="scripts.run_cortex_option_live_canary",
            code_revision=commit,
            run_id=run_id,
        ),
    )


def _build_option_request(
    *,
    run_id: str,
    commit: str,
    structural_settle_seconds: int,
) -> OptionRequest:
    requested_ticks = int(structural_settle_seconds) * int(TICKS_PER_SECOND)
    return OptionRequest(
        option_id=f"{run_id}:processing-chain",
        kind=OptionKind.ESTABLISH_PROCESSING_CHAIN,
        goal=(
            "establish one functional iron processing chain through the "
            "generic Cortex Option boundary"
        ),
        provenance=ActionProvenance(
            requested_by="F2-G4B-live-option-canary",
            source_component="scripts.run_cortex_option_live_canary",
            code_revision=commit,
            run_id=run_id,
        ),
        budget=OptionBudget(requested_ticks=requested_ticks),
        authority=ActionAuthority.SHADOW,
    )


def _fixture_bootstrap(
    *,
    env: Any,
    executor: TransactionalFLEExecutor,
    bootstrap_settle_seconds: int,
) -> dict[str, Any]:
    namespace = env.unwrapped.instance.namespace
    discovery = executor.execute(
        """
iron=nearest(Resource.IronOre)
patch=get_resource_patch(Resource.IronOre,iron,radius=30)
print({'iron':iron,'patch':patch})
""",
        accept=lambda step: (
            not bool(step.info.get("error_occurred"))
            and step.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
        purpose="infrastructure",
    )
    if not discovery.accepted or namespace.patch is None:
        raise RuntimeError("G4B fixture iron discovery was rejected")

    x, y = patch_center(namespace.patch)
    fast_reposition(env, x=x, y=y)

    bootstrap_code = f"""
drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={x!r},y={y!r}),
    direction=Direction.DOWN,
)
drill=insert_item(Prototype.Coal,drill,quantity=20)
chest=place_entity_next_to(
    Prototype.WoodenChest,
    drill.position,
    direction=Direction.DOWN,
)
g4b_iron=0
g4b_bootstrap_polls=0
for _ in range({int(bootstrap_settle_seconds)}):
    sleep(1)
    g4b_bootstrap_polls += 1
    g4b_iron=inspect_inventory(chest)[Prototype.IronOre]
    if g4b_iron > 0:
        break
g4b_bootstrap_elapsed_s=g4b_bootstrap_polls
print({{
    'iron':g4b_iron,
    'polls':g4b_bootstrap_polls,
    'elapsed_s':g4b_bootstrap_elapsed_s,
}})
"""
    bootstrap = executor.execute(
        bootstrap_code,
        accept=lambda step: (
            not bool(step.info.get("error_occurred"))
            and step.candidate_game_state is not None
            and float(getattr(namespace, "g4b_iron", 0) or 0) > 0
        ),
        use_checkpoint_for_action=False,
        purpose="infrastructure",
    )
    if not bootstrap.accepted:
        raise RuntimeError("G4B producer+buffer fixture bootstrap was rejected")

    return {
        "accepted": True,
        "iron_buffered": float(getattr(namespace, "g4b_iron", 0) or 0),
        "poll_count": int(
            getattr(namespace, "g4b_bootstrap_polls", 0) or 0
        ),
        "elapsed_s": float(
            getattr(namespace, "g4b_bootstrap_elapsed_s", 0) or 0
        ),
        "deadline_s": int(bootstrap_settle_seconds),
        "patch_center": {"x": x, "y": y},
        "world_mutation": True,
        "credit_scope": "fixture_only_not_cortex_option",
    }


def run_live_canary(
    *,
    seed: int,
    bootstrap_settle_seconds: int,
    structural_settle_seconds: int,
    grant_ttl_seconds: int,
    artifact: Path,
    ledger_path: Path,
    revision: Mapping[str, Any] | None = None,
    service_state_reader: ServiceStateReader = evolution_service_state,
    phase_state_reader: PhaseStateReader = phase_state,
) -> dict[str, Any]:
    """Execute the single G4B live canary. Caller must explicitly opt in."""

    validate_canary_parameters(
        bootstrap_settle_seconds=bootstrap_settle_seconds,
        structural_settle_seconds=structural_settle_seconds,
        grant_ttl_seconds=grant_ttl_seconds,
    )
    revision = dict(revision or code_revision())
    preflight = preflight_live_canary(
        seed=seed,
        artifact=artifact,
        revision=revision,
        service_state_reader=service_state_reader,
        phase_state_reader=phase_state_reader,
    )
    commit = str(revision["commit"])
    run_id = datetime.now(UTC).strftime("cortex-f2g4b-%Y%m%dT%H%M%SZ")
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "seed": int(seed),
        "confirmatory_seed": False,
        "authority": ActionAuthority.EXECUTE.value,
        "continuous_authority": False,
        "code_revision": revision,
        "preflight": preflight,
        "started_at": utc_now(),
        "status": "starting",
        "fixture_world_replaced": True,
        "fixture_credit_excluded": True,
        "option_execution_attempts": 0,
        "automatic_retry": False,
    }

    env = None
    observer = None
    grant: OptionExecutionGrant | None = None
    ledger: PersistentOptionGrantLedger | None = None
    scope: OptionExecutionScope | None = None

    with FactorioWorldLease(
        run_id=run_id,
        arena=ARENA,
        owner=OWNER,
    ) as lease:
        lease_attestation = lease.active_attestation()
        lease_scope = str(lease_attestation["scope_id"])
        record["world_lease"] = dict(lease_attestation)
        record["evolution_after_lease"] = _assert_evolution_off(
            service_state_reader
        )
        record["status"] = "fixture_setup"
        _atomic_json(artifact, record)

        try:
            import gym

            list_environments()
            env = gym.make("iron_ore_throughput", run_idx=0)
            executor = TransactionalFLEExecutor(env)
            executor.reset(seed=seed)
            instance = env.unwrapped.instance
            namespace = instance.namespace

            fixture = _fixture_bootstrap(
                env=env,
                executor=executor,
                bootstrap_settle_seconds=bootstrap_settle_seconds,
            )
            record["fixture"] = fixture
            record["status"] = "fixture_ready"
            _atomic_json(artifact, record)

            fle_rows = world_rows(namespace, resources=False)
            inventory = available_inventory(fle_rows)
            observer = FactorioObserver()
            snapshot = observer.snapshot()
            overview = observer.resource_overview(max_age_s=0)
            knowledge = observer.game_knowledge(max_age_s=0)
            if snapshot.get("connected") is not True:
                raise RuntimeError(
                    "canonical world snapshot unavailable after G4B fixture: "
                    + str(snapshot.get("error") or "connected=false")
                )
            if overview.get("connected") is not True:
                raise RuntimeError(
                    "canonical resource survey unavailable after G4B fixture: "
                    + str(overview.get("error") or "connected=false")
                )

            machines = [
                row
                for row in snapshot.get("entities", [])
                if isinstance(row, dict)
            ]
            graph = build_factory_graph(machines)
            targets = structural_targets(graph)
            if not targets:
                raise RuntimeError("G4B fixture produced no structural target")

            action_request = _build_action_request(
                run_id=run_id,
                commit=commit,
                targets=targets,
            )
            option_request = _build_option_request(
                run_id=run_id,
                commit=commit,
                structural_settle_seconds=structural_settle_seconds,
            )
            resources = resource_survey_from_overview(overview)
            catalog = RuntimeFactorioCatalog(knowledge)
            power = delivery_power_capability(
                dict(graph.get("metrics", {})),
                fixture_power_operation=False,
            )

            composed = compose_processing_chain_option(
                option_request,
                action_request=action_request,
                graph=graph,
                world_entities=machines,
                catalog=catalog,
                inventory=inventory,
                electric_power_available=power["available"],
                footprints=runtime_entity_footprints(instance),
                resources=resources,
            )
            if not composed.ready or composed.plan is None:
                refusal = (
                    None
                    if composed.refusal is None
                    else composed.refusal.to_dict()
                )
                raise RuntimeError(f"G4B Option composition refused: {refusal}")
            plan = composed.plan

            placement = plan.prepared.preflight.get("placement")
            position = (
                placement.get("position")
                if isinstance(placement, dict)
                else None
            )
            if not isinstance(position, dict):
                raise TypeError("G4B prepared Option has no placement position")
            fast_reposition(
                env,
                x=float(position["x"]),
                y=float(position["y"]),
            )

            sync = executor.execute(
                "print({'cortex_f2g4b_checkpoint':True})",
                accept=lambda step: (
                    not bool(step.info.get("error_occurred"))
                    and step.candidate_game_state is not None
                ),
                use_checkpoint_for_action=False,
                purpose="infrastructure",
            )
            if not sync.accepted:
                raise RuntimeError(
                    "failed to serialize pre-Option G4B checkpoint"
                )

            measure = build_measurement_probe(observer)
            before = dict(measure(plan.prepared))

            scope = OptionExecutionScope.for_plan(
                plan,
                experiment_id=run_id,
                world_lease_id=lease_scope,
            )
            grant = OptionExecutionGrant.for_plan(
                plan,
                scope=scope,
                issued_by="f2-g4b-live-canary",
                reason=(
                    "single non-confirmatory live Option canary under "
                    "persistent one-shot authority"
                ),
                ttl_seconds=grant_ttl_seconds,
            )
            ledger = PersistentOptionGrantLedger(ledger_path)
            ledger.issue(grant)
            boundary = OptionExecutionBoundary(ledger=ledger)
            validation = boundary.validate(
                plan,
                grant=grant,
                scope=scope,
            )
            if not validation.valid:
                refusal = (
                    None
                    if validation.refusal is None
                    else validation.refusal.to_dict()
                )
                raise RuntimeError(
                    f"G4B persistent authority validation refused: {refusal}"
                )

            ledger_before = ledger.get(grant.grant_id)
            if ledger_before is None:
                raise RuntimeError("G4B grant disappeared immediately after issue")
            if ledger_before.consumed_at is not None:
                raise RuntimeError("G4B fresh grant is already marked consumed")

            record.update(
                {
                    "status": "grant_issued_pending_execution",
                    "planning_instruments": {
                        "world": "FactorioObserver.snapshot",
                        "resources": "FactorioObserver.resource_overview",
                        "catalog": "FactorioObserver.game_knowledge",
                        "available": "FLE character inventory",
                        "ticks": (
                            "factorio_ai_lab.instrumentation.runtime."
                            "runtime_game_ticks"
                        ),
                    },
                    "available_inventory": inventory,
                    "graph_before": graph.get("metrics", {}),
                    "targets": targets,
                    "delivery_power_capability": power,
                    "option_plan": plan.to_dict(),
                    "scope": scope.to_dict(),
                    "grant": grant.to_dict(),
                    "grant_validation": validation.to_dict(),
                    "ledger_entry_before": ledger_before.to_dict(),
                    "measurement_before": before,
                    "live_option_execute_authorized": True,
                    "option_execution_attempts": 0,
                }
            )
            # Critical crash-recovery point: grant identity and frozen plan are
            # persisted before the one-shot boundary is allowed to consume it.
            _atomic_json(artifact, record)

            evolution_before_execute = _assert_evolution_off(
                service_state_reader
            )
            lease_before_execute = lease.active_attestation()
            if lease_before_execute["scope_id"] != scope.world_lease_id:
                raise RuntimeError(
                    "active WorldLease attestation no longer matches grant scope"
                )
            record["evolution_before_execute"] = evolution_before_execute
            record["world_lease_before_execute"] = lease_before_execute
            record["option_execution_attempts"] = 1
            _atomic_json(artifact, record)

            result = boundary.execute(
                plan,
                authority=ActionAuthority.EXECUTE,
                grant=grant,
                scope=scope,
                executor=executor,
                measure=measure,
                tick_source=env,
                use_checkpoint_for_action=True,
            )
            final = dict(measure(plan.prepared))
            ledger_after = ledger.get(grant.grant_id)
            if ledger_after is None:
                raise RuntimeError("G4B grant disappeared after execution")

            action_status = result.status.value
            functional_accept = action_status == "accepted"
            sustained_operation: bool | None = None
            sustainability = "not_evaluated"
            if functional_accept:
                if final.get("processor_status") == "no_fuel":
                    sustained_operation = False
                    sustainability = (
                        "functional_accept_terminal_no_fuel"
                    )
                else:
                    sustainability = (
                        "functional_accept_sustainability_not_proven"
                    )

            record.update(
                {
                    "status": "completed",
                    "finished_at": utc_now(),
                    "option_execution_result": result.to_dict(),
                    "measurement_final": final,
                    "ledger_entry_after": ledger_after.to_dict(),
                    "interventions": executor.intervention_snapshot(),
                    "transaction_committed": functional_accept,
                    "rollback_observed": (
                        action_status == "rejected" and final == before
                    ),
                    "functional_accept": functional_accept,
                    "sustained_operation": sustained_operation,
                    "sustainability_classification": sustainability,
                    "live_option_execute_authorized": True,
                }
            )
        except Exception as exc:  # noqa: BLE001
            record.update(
                {
                    "status": "failed",
                    "finished_at": utc_now(),
                    "failure": {
                        "type": type(exc).__name__,
                        "detail": str(exc),
                    },
                }
            )
            if ledger is not None and grant is not None:
                entry = ledger.get(grant.grant_id)
                record["ledger_entry_after_failure"] = (
                    None if entry is None else entry.to_dict()
                )
            if scope is not None:
                record["scope"] = scope.to_dict()
            if grant is not None:
                record["grant"] = grant.to_dict()
        finally:
            if observer is not None:
                observer.close()
            if env is not None:
                env.close()
            _atomic_json(artifact, record)

    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--bootstrap-settle-seconds",
        type=int,
        default=DEFAULT_BOOTSTRAP_SETTLE_SECONDS,
    )
    parser.add_argument(
        "--structural-settle-seconds",
        type=int,
        default=DEFAULT_STRUCTURAL_SETTLE_SECONDS,
    )
    parser.add_argument(
        "--grant-ttl-seconds",
        type=int,
        default=DEFAULT_GRANT_TTL_SECONDS,
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=DEFAULT_ARTIFACT,
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_LEDGER,
    )
    args = parser.parse_args()

    validate_canary_parameters(
        bootstrap_settle_seconds=args.bootstrap_settle_seconds,
        structural_settle_seconds=args.structural_settle_seconds,
        grant_ttl_seconds=args.grant_ttl_seconds,
    )
    revision = code_revision()
    if not args.execute:
        preflight = preflight_live_canary(
            seed=args.seed,
            artifact=args.artifact,
            revision=revision,
        )
        print(json.dumps(preflight, indent=2, sort_keys=True))
        return 0

    record = run_live_canary(
        seed=args.seed,
        bootstrap_settle_seconds=args.bootstrap_settle_seconds,
        structural_settle_seconds=args.structural_settle_seconds,
        grant_ttl_seconds=args.grant_ttl_seconds,
        artifact=args.artifact,
        ledger_path=args.ledger,
        revision=revision,
    )
    print(json.dumps(record, indent=2, sort_keys=True, default=str))
    return 0 if record.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
