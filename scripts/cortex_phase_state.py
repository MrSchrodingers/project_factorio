#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _seed_record(
    *,
    state_root: Path,
    protocol_id: str,
    mode: str,
    seed: int,
) -> dict[str, Any]:
    seed_dir=state_root/"baseline_runs"/protocol_id/mode/str(seed)
    manifest_path=seed_dir/"manifest.json"
    result_path=seed_dir/"result.json"
    if not manifest_path.exists():
        return {"seed":seed,"state":"pending","valid":None}

    try:
        manifest=_load(manifest_path)
    except (OSError,json.JSONDecodeError,TypeError) as exc:
        return {
            "seed":seed,
            "state":"invalid",
            "valid":False,
            "errors":[f"manifest:{type(exc).__name__}"],
        }

    state=str(manifest.get("status") or "unknown")
    if state == "running":
        active={}
        active_path=seed_dir/"runs"/"active_run.json"
        if active_path.exists():
            try:
                active=_load(active_path)
            except (OSError,json.JSONDecodeError,TypeError):
                active={}
        return {
            "seed":seed,
            "state":"running",
            "valid":None,
            "run_id":active.get("run_id"),
            "stage":active.get("stage"),
            "run_status":active.get("status"),
            "updated_at":active.get("updated_at"),
            "started_at":manifest.get("started_at"),
            "commit":(manifest.get("release") or {}).get("commit"),
        }

    if not result_path.exists():
        return {
            "seed":seed,
            "state":state,
            "valid":False,
            "errors":["missing result.json"],
            "commit":(manifest.get("release") or {}).get("commit"),
        }

    try:
        result=_load(result_path)
    except (OSError,json.JSONDecodeError,TypeError) as exc:
        return {
            "seed":seed,
            "state":"invalid",
            "valid":False,
            "errors":[f"result:{type(exc).__name__}"],
        }

    errors=[]
    release=manifest.get("release") or {}
    revision=result.get("code_revision") or {}
    if manifest.get("returncode") != 0:
        errors.append(f"returncode={manifest.get('returncode')}")
    if revision.get("dirty") is not False:
        errors.append("result revision not clean")
    if revision.get("commit") != release.get("commit"):
        errors.append("result/manifest commit mismatch")
    challenger=result.get("challenger") or {}
    fitness=challenger.get("fitness") or {}

    return {
        "seed":seed,
        "state":state,
        "valid":not errors and state=="completed",
        "errors":errors,
        "run_id":challenger.get("run_id"),
        "commit":revision.get("commit") or release.get("commit"),
        "completed_stage_count":result.get("completed_stage_count"),
        "bottleneck":result.get("bottleneck"),
        "closed_loop_autonomy":fitness.get("closed_loop_autonomy"),
        "autonomy_score":fitness.get("autonomy_score"),
        "finished_at":manifest.get("finished_at"),
    }


def build_phase_state(
    *,
    state_root: Path,
    protocol_path: Path,
) -> dict[str, Any]:
    protocol=_load(protocol_path)
    protocol_id=str(protocol.get("schema_version") or "baseline")
    scientific_release_commit=protocol.get("scientific_release_commit")
    modes={}
    release_commits=set()

    for mode in ("exploratory","confirmatory"):
        raw=protocol.get(f"{mode}_seeds") or []
        seeds=[int(value) for value in raw]
        records=[
            _seed_record(
                state_root=state_root,
                protocol_id=protocol_id,
                mode=mode,
                seed=seed,
            )
            for seed in seeds
        ]
        for record in records:
            commit=record.get("commit")
            if isinstance(commit,str) and commit:
                release_commits.add(commit)
        running=[row["seed"] for row in records if row["state"]=="running"]
        valid=[row["seed"] for row in records if row["valid"] is True]
        invalid=[row["seed"] for row in records if row["valid"] is False]
        pending=[row["seed"] for row in records if row["state"]=="pending"]
        next_seed=None if running else (pending[0] if pending else None)
        modes[mode]={
            "configured":len(seeds),
            "valid_completed":len(valid),
            "running":running,
            "invalid":invalid,
            "pending":pending,
            "next_seed":next_seed,
            "seeds":records,
        }

    dashboard_deployment=None
    path=state_root/"runs"/"dashboard_deployment.json"
    if path.exists():
        try:
            dashboard_deployment=_load(path)
        except (OSError,json.JSONDecodeError,TypeError):
            dashboard_deployment={"status":"unreadable"}

    protocol_commit=(
        str(scientific_release_commit)
        if isinstance(scientific_release_commit,str) and scientific_release_commit
        else None
    )
    mismatched_commits=sorted(
        commit
        for commit in release_commits
        if protocol_commit is None or commit != protocol_commit
    )
    exploratory=modes["exploratory"]
    blocked_invalid=bool(exploratory["invalid"])
    blocked_running=bool(exploratory["running"])
    blocked_release=bool(mismatched_commits) or protocol_commit is None
    exploratory_complete=(
        exploratory["valid_completed"] == exploratory["configured"]
        and not exploratory["running"]
        and not exploratory["pending"]
        and not exploratory["invalid"]
    )
    statistical_report_path=(
        state_root / "docs" / "CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md"
    )
    statistical_report_exists=statistical_report_path.exists()
    phase2_ontology_path=(
        state_root / "docs" / "CORTEX_PHASE2_ACTION_ONTOLOGY.md"
    )
    phase2_started=phase2_ontology_path.exists()
    phase2_parity_path=state_root / "docs" / "CORTEX_PHASE2_LEGACY_PARITY.md"
    phase2_parity=phase2_parity_path.exists()
    phase2_structural_path=(
        state_root / "docs" / "CORTEX_PHASE2_STRUCTURAL_PLANNING.md"
    )
    phase2_structural=phase2_structural_path.exists()
    phase2_preparation_path=(
        state_root / "docs" / "CORTEX_PHASE2_STRUCTURAL_PREPARATION.md"
    )
    phase2_preparation=phase2_preparation_path.exists()
    phase2_execution_path=(
        state_root / "docs" / "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md"
    )
    phase2_execution=phase2_execution_path.exists()
    phase2_functional_path=(
        state_root / "docs" / "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md"
    )
    phase2_functional=phase2_functional_path.exists()
    phase2_functional_composition_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md"
    )
    phase2_functional_composition=phase2_functional_composition_path.exists()
    phase2_delivery_actuator_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_DELIVERY_ACTUATOR_DEPENDENCY.md"
    )
    phase2_delivery_actuator=phase2_delivery_actuator_path.exists()
    phase2_delivery_actuator_runner_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_DELIVERY_ACTUATOR_RUNNER.md"
    )
    phase2_delivery_actuator_runner=(
        phase2_delivery_actuator_runner_path.exists()
    )
    phase2_delivery_actuator_canary_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f2f4c_structural_canary.json"
    )
    phase2_delivery_actuator_canary=(
        phase2_delivery_actuator_canary_path.exists()
    )
    phase2_delivery_actuator_canary_payload: dict[str, Any]={}
    phase2_delivery_actuator_canary_error: str | None=None
    if phase2_delivery_actuator_canary:
        try:
            phase2_delivery_actuator_canary_payload=_load(
                phase2_delivery_actuator_canary_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_delivery_actuator_canary_error=(
                f"{type(exc).__name__}: {exc}"
            )
    phase2_functional_canary_path=(
        state_root / "runs" / "audits" / "cortex_f2f_structural_canary.json"
    )
    phase2_functional_canary=phase2_functional_canary_path.exists()
    phase2_functional_canary_payload: dict[str, Any]={}
    phase2_functional_canary_error: str | None=None
    if phase2_functional_canary:
        try:
            phase2_functional_canary_payload=_load(
                phase2_functional_canary_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_functional_canary_error=(
                f"{type(exc).__name__}: {exc}"
            )

    phase2_canary_path=(
        state_root / "runs" / "audits" / "cortex_f2e_structural_canary.json"
    )
    phase2_canary=phase2_canary_path.exists()
    phase2_canary_payload: dict[str, Any] = {}
    phase2_canary_error: str | None = None
    if phase2_canary:
        try:
            phase2_canary_payload=_load(phase2_canary_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_canary_error=f"{type(exc).__name__}: {exc}"
    phase2_action_result=phase2_canary_payload.get("action_result")
    if not isinstance(phase2_action_result,dict):
        phase2_action_result={}
    phase2_action_measurements=phase2_action_result.get("measurements")
    if not isinstance(phase2_action_measurements,dict):
        phase2_action_measurements={}
    phase2_code_revision=phase2_canary_payload.get("code_revision")
    if not isinstance(phase2_code_revision,dict):
        phase2_code_revision={}

    action_result=phase2_canary_payload.get("action_result")
    if not isinstance(action_result,dict):
        action_result={}
    action_measurements=action_result.get("measurements")
    if not isinstance(action_measurements,dict):
        action_measurements={}
    candidate_after=action_measurements.get("candidate_after")
    if not isinstance(candidate_after,dict):
        candidate_after={}
    action_refusal=action_result.get("refusal")
    if not isinstance(action_refusal,dict):
        action_refusal={}

    canary_classification=None
    if phase2_canary:
        if action_result.get("status")=="accepted":
            canary_classification="functional_accept"
        elif (
            action_result.get("status")=="rejected"
            and phase2_canary_payload.get("rollback_observed") is True
            and candidate_after.get("processor_status")=="no_fuel"
        ):
            canary_classification="functional_dependency_missing"
        elif (
            action_result.get("status")=="rejected"
            and phase2_canary_payload.get("rollback_observed") is True
        ):
            canary_classification="rejected_with_rollback"
        elif phase2_canary_payload.get("status")=="failed":
            canary_classification="experiment_failed"

    functional_action_result=phase2_functional_canary_payload.get(
        "action_result"
    )
    if not isinstance(functional_action_result,dict):
        functional_action_result={}
    functional_measurements=functional_action_result.get("measurements")
    if not isinstance(functional_measurements,dict):
        functional_measurements={}
    functional_candidate_after=functional_measurements.get("candidate_after")
    if not isinstance(functional_candidate_after,dict):
        functional_candidate_after={}
    functional_refusal=functional_action_result.get("refusal")
    if not isinstance(functional_refusal,dict):
        functional_refusal={}
    functional_code_revision=phase2_functional_canary_payload.get(
        "code_revision"
    )
    if not isinstance(functional_code_revision,dict):
        functional_code_revision={}
    functional_dependency=phase2_functional_canary_payload.get(
        "functional_dependency"
    )
    if not isinstance(functional_dependency,dict):
        functional_dependency={}
    dependency_row=functional_dependency.get("dependency")
    if not isinstance(dependency_row,dict):
        dependency_row={}
    fuel_row=dependency_row.get("fuel")
    if not isinstance(fuel_row,dict):
        fuel_row={}

    functional_canary_classification=None
    if phase2_functional_canary:
        if functional_action_result.get("status")=="accepted":
            functional_canary_classification="functional_accept"
        elif (
            functional_action_result.get("status")=="rejected"
            and phase2_functional_canary_payload.get("rollback_observed") is True
            and functional_candidate_after.get("processor_status")=="no_ingredients"
        ):
            functional_canary_classification=(
                "processor_input_missing_with_rollback"
            )
        elif (
            functional_action_result.get("status")=="rejected"
            and phase2_functional_canary_payload.get("rollback_observed") is True
        ):
            functional_canary_classification="rejected_with_rollback"
        elif phase2_functional_canary_payload.get("status")=="failed":
            functional_canary_classification="experiment_failed"

    delivery_canary_action_result=(
        phase2_delivery_actuator_canary_payload.get("action_result")
    )
    if not isinstance(delivery_canary_action_result,dict):
        delivery_canary_action_result={}
    delivery_canary_measurements=delivery_canary_action_result.get(
        "measurements"
    )
    if not isinstance(delivery_canary_measurements,dict):
        delivery_canary_measurements={}
    delivery_canary_candidate=delivery_canary_measurements.get(
        "candidate_after"
    )
    if not isinstance(delivery_canary_candidate,dict):
        delivery_canary_candidate={}
    delivery_canary_refusal=delivery_canary_action_result.get("refusal")
    if not isinstance(delivery_canary_refusal,dict):
        delivery_canary_refusal={}
    delivery_canary_revision=(
        phase2_delivery_actuator_canary_payload.get("code_revision")
    )
    if not isinstance(delivery_canary_revision,dict):
        delivery_canary_revision={}
    delivery_dependency=(
        phase2_delivery_actuator_canary_payload.get(
            "delivery_actuator_dependency"
        )
    )
    if not isinstance(delivery_dependency,dict):
        delivery_dependency={}
    delivery_dependency_row=delivery_dependency.get("dependency")
    if not isinstance(delivery_dependency_row,dict):
        delivery_dependency_row={}
    actuator_fuel_dependency=delivery_dependency_row.get(
        "fuel_dependency"
    )
    if not isinstance(actuator_fuel_dependency,dict):
        actuator_fuel_dependency={}
    actuator_fuel=actuator_fuel_dependency.get("fuel")
    if not isinstance(actuator_fuel,dict):
        actuator_fuel={}
    delivery_power=(
        phase2_delivery_actuator_canary_payload.get(
            "delivery_power_capability"
        )
    )
    if not isinstance(delivery_power,dict):
        delivery_power={}
    delivery_final_measurement=(
        phase2_delivery_actuator_canary_payload.get("measurement_final")
    )
    if not isinstance(delivery_final_measurement,dict):
        delivery_final_measurement={}

    delivery_canary_classification=None
    if phase2_delivery_actuator_canary:
        if delivery_canary_action_result.get("status")=="accepted":
            delivery_canary_classification="functional_accept"
        elif (
            delivery_canary_action_result.get("status")=="rejected"
            and phase2_delivery_actuator_canary_payload.get(
                "rollback_observed"
            ) is True
            and delivery_canary_candidate.get("processor_status")
            == "no_ingredients"
        ):
            delivery_canary_classification=(
                "material_delivery_failed_with_rollback"
            )
        elif (
            delivery_canary_action_result.get("status")=="rejected"
            and phase2_delivery_actuator_canary_payload.get(
                "rollback_observed"
            ) is True
            and delivery_canary_candidate.get("processor_status")
            == "no_fuel"
        ):
            delivery_canary_classification=(
                "energy_dependency_failed_with_rollback"
            )
        elif (
            delivery_canary_action_result.get("status")=="rejected"
            and phase2_delivery_actuator_canary_payload.get(
                "rollback_observed"
            ) is True
        ):
            delivery_canary_classification="rejected_with_rollback"
        elif phase2_delivery_actuator_canary_payload.get("status")=="failed":
            delivery_canary_classification="experiment_failed"

    delivery_sustained_operation=None
    delivery_sustainability_classification="not_evaluated"
    if delivery_canary_classification=="functional_accept":
        final_status=delivery_final_measurement.get("processor_status")
        if final_status=="no_fuel":
            delivery_sustained_operation=False
            delivery_sustainability_classification=(
                "functional_accept_terminal_no_fuel"
            )
        else:
            delivery_sustainability_classification=(
                "functional_accept_sustainability_not_proven"
            )

    if blocked_running:
        action=f"monitor seed {exploratory['running'][0]}"
    elif blocked_invalid:
        action="investigate invalid seed(s): "+",".join(
            str(seed) for seed in exploratory["invalid"]
        )
    elif blocked_release:
        action="halt: scientific release provenance mismatch"
    elif exploratory["next_seed"] is not None:
        action=f"run seed {exploratory['next_seed']}"
    elif exploratory_complete and statistical_report_exists and phase2_started:
        action="F2 active; follow docs/CORTEX_HANDOFF.md"
    elif exploratory_complete and statistical_report_exists:
        action="F1 complete; follow docs/CORTEX_HANDOFF.md"
    else:
        action="exploratory series complete; produce statistical report"

    phase2_checkpoint=None
    for checkpoint,enabled in (
        ("F2-F4C",phase2_delivery_actuator_canary),
        ("F2-F4B",phase2_delivery_actuator_runner),
        ("F2-F4A",phase2_delivery_actuator),
        ("F2-F3",phase2_functional_canary),
        ("F2-F2",phase2_functional_composition),
        ("F2-F1",phase2_functional),
        ("F2-E2",phase2_canary),
        ("F2-E1",phase2_execution),
        ("F2-D",phase2_preparation),
        ("F2-C",phase2_structural),
        ("F2-B",phase2_parity),
        ("F2-A",phase2_started),
    ):
        if enabled:
            phase2_checkpoint=checkpoint
            break

    return {
        "schema_version":"cortex_phase_state_v1",
        "generated_at":datetime.now(UTC).isoformat(),
        "phase":(
            "F2"
            if exploratory_complete and statistical_report_exists and phase2_started
            else ("F1" if exploratory_complete and statistical_report_exists else "F1-B")
        ),
        "phase_status":(
            "active"
            if phase2_started and exploratory_complete and statistical_report_exists
            else (
                "complete"
                if exploratory_complete and statistical_report_exists
                else "active"
            )
        ),
        "protocol":protocol_id,
        "scientific_release_commit":protocol_commit,
        "baseline_release_commits":sorted(release_commits),
        "release_mismatches":mismatched_commits,
        "baseline_release_consistent":not blocked_release,
        "global_champion_exists":(state_root/"runs"/"evolution_champion.json").exists(),
        "evolution_service_required_state":"inactive",
        "dashboard_deployment":dashboard_deployment,
        "exploratory_complete":exploratory_complete,
        "statistical_report":{
            "path":str(statistical_report_path),
            "exists":statistical_report_exists,
        },
        "phase2_ontology":{
            "path":str(phase2_ontology_path),
            "exists":phase2_started,
        },
        "phase2_checkpoint":phase2_checkpoint,
        "phase2_parity":{
            "path":str(phase2_parity_path),
            "exists":phase2_parity,
        },
        "phase2_structural":{
            "path":str(phase2_structural_path),
            "exists":phase2_structural,
        },
        "phase2_preparation":{
            "path":str(phase2_preparation_path),
            "exists":phase2_preparation,
        },
        "phase2_execution":{
            "path":str(phase2_execution_path),
            "exists":phase2_execution,
        },
        "phase2_functional":{
            "path":str(phase2_functional_path),
            "exists":phase2_functional,
        },
        "phase2_functional_composition":{
            "path":str(phase2_functional_composition_path),
            "exists":phase2_functional_composition,
        },
        "phase2_delivery_actuator":{
            "path":str(phase2_delivery_actuator_path),
            "exists":phase2_delivery_actuator,
        },
        "phase2_delivery_actuator_runner":{
            "path":str(phase2_delivery_actuator_runner_path),
            "exists":phase2_delivery_actuator_runner,
        },
        "phase2_delivery_actuator_canary":{
            "path":str(phase2_delivery_actuator_canary_path),
            "exists":phase2_delivery_actuator_canary,
            "status":phase2_delivery_actuator_canary_payload.get(
                "status"
            ),
            "run_id":phase2_delivery_actuator_canary_payload.get(
                "run_id"
            ),
            "authority":phase2_delivery_actuator_canary_payload.get(
                "authority"
            ),
            "continuous_authority":(
                phase2_delivery_actuator_canary_payload.get(
                    "continuous_authority"
                )
            ),
            "code_commit":delivery_canary_revision.get("commit"),
            "transaction_committed":(
                phase2_delivery_actuator_canary_payload.get(
                    "transaction_committed"
                )
            ),
            "rollback_observed":(
                phase2_delivery_actuator_canary_payload.get(
                    "rollback_observed"
                )
            ),
            "action_status":delivery_canary_action_result.get("status"),
            "refusal":delivery_canary_refusal.get("code"),
            "classification":delivery_canary_classification,
            "processor_status":delivery_canary_candidate.get(
                "processor_status"
            ),
            "processor_output":delivery_canary_candidate.get(
                "processor_output"
            ),
            "functional_accept":(
                delivery_canary_classification=="functional_accept"
            ),
            "sustained_operation":delivery_sustained_operation,
            "sustainability_classification":(
                delivery_sustainability_classification
            ),
            "candidate_after":delivery_canary_candidate,
            "measurement_before":(
                phase2_delivery_actuator_canary_payload.get(
                    "measurement_before"
                )
            ),
            "measurement_final":(
                phase2_delivery_actuator_canary_payload.get(
                    "measurement_final"
                )
            ),
            "postconditions":delivery_canary_action_result.get(
                "postconditions"
            ),
            "delivery_dependency_ready":delivery_dependency.get("ready"),
            "actuator":delivery_dependency_row.get("actuator"),
            "actuator_fuel":actuator_fuel.get("name"),
            "power_available":delivery_power.get("available"),
            "power_status":delivery_power.get("status"),
            "failure":phase2_delivery_actuator_canary_payload.get(
                "failure"
            ),
            "read_error":phase2_delivery_actuator_canary_error,
        },
        "phase2_functional_canary":{
            "path":str(phase2_functional_canary_path),
            "exists":phase2_functional_canary,
            "status":phase2_functional_canary_payload.get("status"),
            "run_id":phase2_functional_canary_payload.get("run_id"),
            "authority":phase2_functional_canary_payload.get("authority"),
            "continuous_authority":phase2_functional_canary_payload.get(
                "continuous_authority"
            ),
            "code_commit":functional_code_revision.get("commit"),
            "transaction_committed":phase2_functional_canary_payload.get(
                "transaction_committed"
            ),
            "rollback_observed":phase2_functional_canary_payload.get(
                "rollback_observed"
            ),
            "action_status":functional_action_result.get("status"),
            "refusal":functional_refusal.get("code"),
            "classification":functional_canary_classification,
            "processor_status":functional_candidate_after.get(
                "processor_status"
            ),
            "processor_output":functional_candidate_after.get(
                "processor_output"
            ),
            "candidate_after":functional_candidate_after,
            "measurement_before":phase2_functional_canary_payload.get(
                "measurement_before"
            ),
            "measurement_final":phase2_functional_canary_payload.get(
                "measurement_final"
            ),
            "postconditions":functional_action_result.get("postconditions"),
            "functional_dependency_ready":functional_dependency.get("ready"),
            "fuel":fuel_row.get("name"),
            "fuel_units":dependency_row.get("units_needed"),
            "fuel_carried_only":dependency_row.get("carried_only"),
            "failure":phase2_functional_canary_payload.get("failure"),
            "read_error":phase2_functional_canary_error,
        },
        "phase2_canary":{
            "path":str(phase2_canary_path),
            "exists":phase2_canary,
            "status":phase2_canary_payload.get("status"),
            "run_id":phase2_canary_payload.get("run_id"),
            "authority":phase2_canary_payload.get("authority"),
            "continuous_authority":phase2_canary_payload.get(
                "continuous_authority"
            ),
            "code_commit":phase2_code_revision.get("commit"),
            "transaction_committed":phase2_canary_payload.get(
                "transaction_committed"
            ),
            "rollback_observed":phase2_canary_payload.get(
                "rollback_observed"
            ),
            "action_status":action_result.get("status"),
            "refusal":action_refusal.get("code"),
            "classification":canary_classification,
            "processor_status":candidate_after.get("processor_status"),
            "processor_output":candidate_after.get("processor_output"),
            "failure":phase2_canary_payload.get("failure"),
            "candidate_after":phase2_action_measurements.get(
                "candidate_after"
            ),
            "measurement_before":phase2_canary_payload.get(
                "measurement_before"
            ),
            "measurement_final":phase2_canary_payload.get(
                "measurement_final"
            ),
            "postconditions":phase2_action_result.get("postconditions"),
            "read_error":phase2_canary_error,
        },
        "modes":modes,
        "resume":{
            "action":action,
            "do_not_start_another_seed":(
                blocked_running or blocked_invalid or blocked_release
            ),
        },
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("/srv/factorio-ai-lab/configs/cortex_baseline_v1.json"),
    )
    parser.add_argument("--write",action="store_true")
    args=parser.parse_args()

    state=build_phase_state(
        state_root=args.state_root.resolve(),
        protocol_path=args.protocol.resolve(),
    )
    if args.write:
        path=args.state_root.resolve()/"runs"/"cortex_phase_state.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix(".tmp")
        temp.write_text(json.dumps(state,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        temp.replace(path)
        print(path)
    else:
        print(json.dumps(state,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
