#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    phase2_runner_independence_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_RUNNER_INDEPENDENCE.md"
    )
    phase2_runner_independence=phase2_runner_independence_path.exists()
    phase2_options_path=(
        state_root / "docs" / "CORTEX_PHASE2_OPTIONS.md"
    )
    phase2_options=phase2_options_path.exists()
    phase2_option_execution_path=(
        state_root / "docs" / "CORTEX_PHASE2_OPTION_EXECUTION_BOUNDARY.md"
    )
    phase2_option_execution=phase2_option_execution_path.exists()
    phase2_persistent_authority_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_PERSISTENT_OPTION_AUTHORITY.md"
    )
    phase2_persistent_authority=phase2_persistent_authority_path.exists()
    phase2_authority_dry_run_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f2g4a_option_authority_dry_run.json"
    )
    phase2_authority_dry_run=phase2_authority_dry_run_path.exists()
    phase2_authority_dry_run_payload: dict[str, Any]={}
    phase2_authority_dry_run_error: str | None=None
    if phase2_authority_dry_run:
        try:
            phase2_authority_dry_run_payload=_load(
                phase2_authority_dry_run_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_authority_dry_run_error=f"{type(exc).__name__}: {exc}"
    phase2_persistent_authority_valid=(
        phase2_persistent_authority
        and phase2_authority_dry_run
        and phase2_authority_dry_run_error is None
        and phase2_authority_dry_run_payload.get("status")=="pass"
        and phase2_authority_dry_run_payload.get(
            "factorio_world_mutation"
        ) is False
        and phase2_authority_dry_run_payload.get(
            "continuous_authority"
        ) is False
        and phase2_authority_dry_run_payload.get(
            "live_option_execute_authorized"
        ) is False
    )

    phase2_live_option_canary_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f2g4b_option_live_canary.json"
    )
    phase2_live_option_canary=phase2_live_option_canary_path.exists()
    phase2_live_option_canary_payload: dict[str, Any]={}
    phase2_live_option_canary_error: str | None=None
    if phase2_live_option_canary:
        try:
            phase2_live_option_canary_payload=_load(
                phase2_live_option_canary_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_live_option_canary_error=f"{type(exc).__name__}: {exc}"

    phase2_live_option_doc_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_LIVE_OPTION_CANARY.md"
    )
    phase2_live_option_doc=phase2_live_option_doc_path.exists()

    phase2_live_temporal_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f2g4b_temporal_audit.json"
    )
    phase2_live_temporal_audit=phase2_live_temporal_audit_path.exists()
    phase2_live_temporal_audit_payload: dict[str, Any]={}
    phase2_live_temporal_audit_error: str | None=None
    if phase2_live_temporal_audit:
        try:
            phase2_live_temporal_audit_payload=_load(
                phase2_live_temporal_audit_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_live_temporal_audit_error=f"{type(exc).__name__}: {exc}"

    live_result=phase2_live_option_canary_payload.get(
        "option_execution_result"
    )
    if not isinstance(live_result,dict):
        live_result={}
    live_action=live_result.get("action_result")
    if not isinstance(live_action,dict):
        live_action={}
    live_postconditions=live_action.get("postconditions")
    if not isinstance(live_postconditions,list):
        live_postconditions=[]
    live_hard_postconditions_satisfied=bool(live_postconditions) and all(
        isinstance(row,dict)
        and (
            row.get("hard") is not True
            or row.get("state")=="satisfied"
        )
        for row in live_postconditions
    )
    live_grant=phase2_live_option_canary_payload.get("grant")
    if not isinstance(live_grant,dict):
        live_grant={}
    live_scope=live_grant.get("scope")
    if not isinstance(live_scope,dict):
        live_scope={}
    live_lease=phase2_live_option_canary_payload.get("world_lease")
    if not isinstance(live_lease,dict):
        live_lease={}
    live_lease_before=phase2_live_option_canary_payload.get(
        "world_lease_before_execute"
    )
    if not isinstance(live_lease_before,dict):
        live_lease_before={}
    live_ledger_before=phase2_live_option_canary_payload.get(
        "ledger_entry_before"
    )
    if not isinstance(live_ledger_before,dict):
        live_ledger_before={}
    live_ledger_after=phase2_live_option_canary_payload.get(
        "ledger_entry_after"
    )
    if not isinstance(live_ledger_after,dict):
        live_ledger_after={}
    live_final=phase2_live_option_canary_payload.get("measurement_final")
    if not isinstance(live_final,dict):
        live_final={}
    live_evolution_after_lease=phase2_live_option_canary_payload.get(
        "evolution_after_lease"
    )
    if not isinstance(live_evolution_after_lease,dict):
        live_evolution_after_lease={}
    live_evolution_before_execute=phase2_live_option_canary_payload.get(
        "evolution_before_execute"
    )
    if not isinstance(live_evolution_before_execute,dict):
        live_evolution_before_execute={}
    live_artifact_sha256=(
        _sha256(phase2_live_option_canary_path)
        if phase2_live_option_canary
        and phase2_live_option_canary_error is None
        else None
    )
    temporal_claim=phase2_live_temporal_audit_payload.get("temporal_claim")
    if not isinstance(temporal_claim,dict):
        temporal_claim={}

    live_scope_id=live_scope.get("world_lease_id")
    live_lease_scope_id=live_lease.get("scope_id")
    live_lease_before_scope_id=live_lease_before.get("scope_id")
    live_grant_id=live_grant.get("grant_id")
    live_ledger_after_grant_id=live_ledger_after.get("grant_id")
    live_consumed_at=live_ledger_after.get("consumed_at")
    live_processor_output=live_final.get("processor_output")
    live_coverage=live_final.get("physical_processing_coverage")
    live_producers=live_final.get("producers_reaching_processor")
    live_seed=phase2_live_option_canary_payload.get("seed")

    phase2_live_option_canary_valid=(
        phase2_live_option_doc
        and phase2_live_option_canary
        and phase2_live_option_canary_error is None
        and phase2_live_temporal_audit
        and phase2_live_temporal_audit_error is None
        and phase2_live_option_canary_payload.get("status")=="completed"
        and phase2_live_option_canary_payload.get("confirmatory_seed") is False
        and isinstance(live_seed,int)
        and live_seed not in range(20261101,20261111)
        and phase2_live_option_canary_payload.get("automatic_retry") is False
        and phase2_live_option_canary_payload.get("option_execution_attempts")==1
        and phase2_live_option_canary_payload.get("continuous_authority") is False
        and phase2_live_option_canary_payload.get("failure") is None
        and phase2_live_option_canary_payload.get("functional_accept") is True
        and phase2_live_option_canary_payload.get("transaction_committed") is True
        and live_result.get("status")=="accepted"
        and live_result.get("changed_world") is True
        and live_action.get("status")=="accepted"
        and live_hard_postconditions_satisfied
        and isinstance(live_processor_output,(int,float))
        and not isinstance(live_processor_output,bool)
        and float(live_processor_output)>0
        and isinstance(live_coverage,(int,float))
        and not isinstance(live_coverage,bool)
        and float(live_coverage)>0
        and isinstance(live_producers,(int,float))
        and not isinstance(live_producers,bool)
        and float(live_producers)>0
        and live_scope.get("max_executions")==1
        and isinstance(live_scope_id,str)
        and bool(live_scope_id)
        and live_scope_id==live_lease_scope_id
        and live_scope_id==live_lease_before_scope_id
        and isinstance(live_lease.get("lease_id"),str)
        and bool(live_lease.get("lease_id"))
        and live_lease.get("lease_id")==live_lease_before.get("lease_id")
        and live_lease_before.get("status")=="active"
        and live_ledger_before.get("consumed_at") is None
        and live_ledger_before.get("consume_result") is None
        and isinstance(live_consumed_at,str)
        and bool(live_consumed_at)
        and live_ledger_after.get("consume_result")
        =="reserved_before_runtime_mutation"
        and live_grant_id==live_ledger_after_grant_id
        and live_result.get("grant_consumed_at")==live_consumed_at
        and live_result.get("grant_consume_result")
        =="reserved_before_runtime_mutation"
        and live_result.get("plan_digest")==live_grant.get("plan_digest")
        and live_evolution_after_lease
        =={"active":"inactive","enabled":"disabled"}
        and live_evolution_before_execute
        =={"active":"inactive","enabled":"disabled"}
        and phase2_live_temporal_audit_payload.get("live_artifact_sha256")
        ==live_artifact_sha256
        and phase2_live_temporal_audit_payload.get("live_run_id")
        ==phase2_live_option_canary_payload.get("run_id")
        and phase2_live_temporal_audit_payload.get("classification")
        =="functional_accept_tick_epoch_reset_explained"
        and temporal_claim.get("old_artifact_rewritten") is False
        and temporal_claim.get("second_live_canary_executed") is False
    )
    phase2_baseline_only_doc_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE2_BASELINE_ONLY_ENFORCEMENT.md"
    )
    phase2_baseline_only_doc=phase2_baseline_only_doc_path.exists()
    phase2_baseline_only_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f2g5_baseline_only_enforcement.json"
    )
    phase2_baseline_only_audit=phase2_baseline_only_audit_path.exists()
    phase2_baseline_only_audit_payload: dict[str, Any]={}
    phase2_baseline_only_audit_error: str | None=None
    if phase2_baseline_only_audit:
        try:
            phase2_baseline_only_audit_payload=_load(
                phase2_baseline_only_audit_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase2_baseline_only_audit_error=f"{type(exc).__name__}: {exc}"
    baseline_only_checks=phase2_baseline_only_audit_payload.get("checks")
    if not isinstance(baseline_only_checks,dict):
        baseline_only_checks={}
    baseline_only_revision=phase2_baseline_only_audit_payload.get(
        "code_revision"
    )
    if not isinstance(baseline_only_revision,dict):
        baseline_only_revision={}
    phase2_baseline_only_valid=(
        phase2_baseline_only_doc
        and phase2_baseline_only_audit
        and phase2_baseline_only_audit_error is None
        and phase2_baseline_only_audit_payload.get("status")=="pass"
        and baseline_only_revision.get("dirty") is False
        and isinstance(baseline_only_revision.get("commit"),str)
        and bool(baseline_only_revision.get("commit"))
        and phase2_baseline_only_audit_payload.get("legacy_runner_role")
        =="baseline"
        and phase2_baseline_only_audit_payload.get(
            "fail_closed_before_environment_creation"
        ) is True
        and phase2_baseline_only_audit_payload.get(
            "cortex_imports_legacy_runner"
        ) is False
        and phase2_baseline_only_audit_payload.get("world_mutation") is False
        and phase2_baseline_only_audit_payload.get("factorio_rcon_used") is False
        and bool(baseline_only_checks)
        and all(value is True for value in baseline_only_checks.values())
    )
    phase2_exit_gate_valid=(
        phase2_live_option_canary_valid
        and phase2_options
        and phase2_option_execution
        and phase2_baseline_only_valid
    )

    phase3_executive_doc_path=(
        state_root / "docs" / "CORTEX_PHASE3_EXECUTIVE_SHADOW_KERNEL.md"
    )
    phase3_executive_doc=phase3_executive_doc_path.exists()
    phase3_executive_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f3a_executive_shadow_replay.json"
    )
    phase3_executive_audit=phase3_executive_audit_path.exists()
    phase3_executive_payload: dict[str, Any]={}
    phase3_executive_error: str | None=None
    if phase3_executive_audit:
        try:
            phase3_executive_payload=_load(phase3_executive_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase3_executive_error=f"{type(exc).__name__}: {exc}"

    phase3_revision=phase3_executive_payload.get("code_revision")
    if not isinstance(phase3_revision,dict):
        phase3_revision={}
    phase3_observed=phase3_executive_payload.get("observed_evidence")
    if not isinstance(phase3_observed,dict):
        phase3_observed={}
    phase3_counterfactual=phase3_executive_payload.get(
        "counterfactual_expansion"
    )
    if not isinstance(phase3_counterfactual,dict):
        phase3_counterfactual={}
    phase3_policy_replays=phase3_executive_payload.get("policy_replays")
    if not isinstance(phase3_policy_replays,dict):
        phase3_policy_replays={}
    phase3_checks=phase3_executive_payload.get("checks")
    if not isinstance(phase3_checks,dict):
        phase3_checks={}
    phase3_repairs_path=state_root / "runs" / "repairs.jsonl"
    phase3_repairs_sha=(
        _sha256(phase3_repairs_path)
        if phase3_repairs_path.exists()
        else None
    )
    phase3_candidate_count=phase3_counterfactual.get("candidate_count")
    phase3_executive_valid=(
        phase2_exit_gate_valid
        and phase3_executive_doc
        and phase3_executive_audit
        and phase3_executive_error is None
        and phase3_executive_payload.get("schema_version")
        =="cortex_f3a_executive_shadow_replay_v1"
        and phase3_executive_payload.get("status")=="pass"
        and phase3_revision.get("dirty") is False
        and isinstance(phase3_revision.get("commit"),str)
        and bool(phase3_revision.get("commit"))
        and phase3_executive_payload.get("authority")=="shadow"
        and phase3_executive_payload.get("world_mutation") is False
        and phase3_executive_payload.get("factorio_rcon_used") is False
        and phase3_executive_payload.get("fle_environment_created") is False
        and phase3_executive_payload.get("world_lease_acquired") is False
        and phase3_executive_payload.get("execution_grant_created") is False
        and phase3_executive_payload.get("continuous_authority") is False
        and phase3_observed.get("source")=="runs/repairs.jsonl"
        and phase3_repairs_sha is not None
        and phase3_observed.get("source_sha256")==phase3_repairs_sha
        and phase3_observed.get("symptom")
        =="producer_output_unprocessed:output_buffered_not_processed"
        and phase3_counterfactual.get("observed_in_world") is False
        and isinstance(phase3_candidate_count,int)
        and not isinstance(phase3_candidate_count,bool)
        and phase3_candidate_count>=2
        and set(phase3_policy_replays)
        =={"prefer_build","prefer_reroute"}
        and bool(phase3_checks)
        and all(value is True for value in phase3_checks.values())
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

    if phase3_executive_valid:
        action=(
            "F3-A active in SHADOW; implement verification, credit assignment, "
            "and experiment ledger"
        )
    elif blocked_running:
        action=f"monitor seed {exploratory['running'][0]}"
    elif blocked_invalid:
        action="investigate invalid seed(s): "+",".join(
            str(seed) for seed in exploratory["invalid"]
        )
    elif blocked_release:
        action="halt: scientific release provenance mismatch"
    elif exploratory["next_seed"] is not None:
        action=f"run seed {exploratory['next_seed']}"
    elif (
        exploratory_complete
        and statistical_report_exists
        and phase2_exit_gate_valid
    ):
        action="F2 complete; F3 ready but not started"
    elif exploratory_complete and statistical_report_exists and phase2_started:
        action="F2 active; follow docs/CORTEX_HANDOFF.md"
    elif exploratory_complete and statistical_report_exists:
        action="F1 complete; follow docs/CORTEX_HANDOFF.md"
    else:
        action="exploratory series complete; produce statistical report"

    phase2_checkpoint=None
    for checkpoint,enabled in (
        ("F2-G5",phase2_baseline_only_valid),
        ("F2-G4B",phase2_live_option_canary_valid),
        ("F2-G4A",phase2_persistent_authority_valid),
        ("F2-G3",phase2_option_execution),
        ("F2-G2",phase2_options),
        ("F2-G1",phase2_runner_independence),
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
            "F3"
            if phase3_executive_valid
            else (
                "F2"
                if exploratory_complete
                and statistical_report_exists
                and phase2_started
                else (
                    "F1"
                    if exploratory_complete and statistical_report_exists
                    else "F1-B"
                )
            )
        ),
        "phase_status":(
            "active"
            if phase3_executive_valid
            else (
                "complete"
                if phase2_exit_gate_valid
                else (
                    "active"
                    if (
                        phase2_started
                        and exploratory_complete
                        and statistical_report_exists
                    )
                    else (
                        "complete"
                        if exploratory_complete and statistical_report_exists
                        else "active"
                    )
                )
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
        "phase3_checkpoint":(
            "F3-A" if phase3_executive_valid else None
        ),
        "phase3_executive_shadow_kernel":{
            "document_path":str(phase3_executive_doc_path),
            "document_exists":phase3_executive_doc,
            "audit_path":str(phase3_executive_audit_path),
            "audit_exists":phase3_executive_audit,
            "validated":phase3_executive_valid,
            "status":phase3_executive_payload.get("status"),
            "run_id":phase3_executive_payload.get("run_id"),
            "code_commit":phase3_revision.get("commit"),
            "authority":phase3_executive_payload.get("authority"),
            "world_mutation":phase3_executive_payload.get("world_mutation"),
            "factorio_rcon_used":phase3_executive_payload.get(
                "factorio_rcon_used"
            ),
            "fle_environment_created":phase3_executive_payload.get(
                "fle_environment_created"
            ),
            "world_lease_acquired":phase3_executive_payload.get(
                "world_lease_acquired"
            ),
            "execution_grant_created":phase3_executive_payload.get(
                "execution_grant_created"
            ),
            "continuous_authority":phase3_executive_payload.get(
                "continuous_authority"
            ),
            "observed_evidence":phase3_observed,
            "counterfactual_expansion":phase3_counterfactual,
            "checks":phase3_checks,
            "read_error":phase3_executive_error,
        },
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
        "phase2_runner_independence":{
            "path":str(phase2_runner_independence_path),
            "exists":phase2_runner_independence,
        },
        "phase2_options":{
            "path":str(phase2_options_path),
            "exists":phase2_options,
        },
        "phase2_option_execution_boundary":{
            "path":str(phase2_option_execution_path),
            "exists":phase2_option_execution,
        },
        "phase2_persistent_option_authority":{
            "path":str(phase2_persistent_authority_path),
            "exists":phase2_persistent_authority,
            "validated":phase2_persistent_authority_valid,
            "dry_run_path":str(phase2_authority_dry_run_path),
            "dry_run_exists":phase2_authority_dry_run,
            "dry_run_status":phase2_authority_dry_run_payload.get("status"),
            "dry_run_run_id":phase2_authority_dry_run_payload.get("run_id"),
            "dry_run_code_commit":(
                phase2_authority_dry_run_payload.get("code_revision") or {}
            ).get("commit"),
            "world_mutation":phase2_authority_dry_run_payload.get(
                "factorio_world_mutation"
            ),
            "continuous_authority":phase2_authority_dry_run_payload.get(
                "continuous_authority"
            ),
            "live_option_execute_authorized":(
                phase2_authority_dry_run_payload.get(
                    "live_option_execute_authorized"
                )
            ),
            "read_error":phase2_authority_dry_run_error,
        },
        "phase2_exit_gate":{
            "validated":phase2_exit_gate_valid,
            "generic_option_api":phase2_options,
            "universal_transactional_execution":phase2_option_execution,
            "live_functional_chain":phase2_live_option_canary_valid,
            "legacy_runner_baseline_only":phase2_baseline_only_valid,
        },
        "phase2_baseline_only_enforcement":{
            "document_path":str(phase2_baseline_only_doc_path),
            "document_exists":phase2_baseline_only_doc,
            "audit_path":str(phase2_baseline_only_audit_path),
            "audit_exists":phase2_baseline_only_audit,
            "validated":phase2_baseline_only_valid,
            "status":phase2_baseline_only_audit_payload.get("status"),
            "code_commit":baseline_only_revision.get("commit"),
            "legacy_runner_role":phase2_baseline_only_audit_payload.get(
                "legacy_runner_role"
            ),
            "fail_closed_before_environment_creation":(
                phase2_baseline_only_audit_payload.get(
                    "fail_closed_before_environment_creation"
                )
            ),
            "cortex_imports_legacy_runner":(
                phase2_baseline_only_audit_payload.get(
                    "cortex_imports_legacy_runner"
                )
            ),
            "world_mutation":phase2_baseline_only_audit_payload.get(
                "world_mutation"
            ),
            "factorio_rcon_used":phase2_baseline_only_audit_payload.get(
                "factorio_rcon_used"
            ),
            "checks":baseline_only_checks,
            "read_error":phase2_baseline_only_audit_error,
        },
        "phase2_live_option_canary":{
            "path":str(phase2_live_option_canary_path),
            "exists":phase2_live_option_canary,
            "document_path":str(phase2_live_option_doc_path),
            "document_exists":phase2_live_option_doc,
            "validated":phase2_live_option_canary_valid,
            "status":phase2_live_option_canary_payload.get("status"),
            "run_id":phase2_live_option_canary_payload.get("run_id"),
            "seed":live_seed,
            "confirmatory_seed":phase2_live_option_canary_payload.get(
                "confirmatory_seed"
            ),
            "code_commit":(
                phase2_live_option_canary_payload.get("code_revision") or {}
            ).get("commit"),
            "automatic_retry":phase2_live_option_canary_payload.get(
                "automatic_retry"
            ),
            "option_execution_attempts":(
                phase2_live_option_canary_payload.get(
                    "option_execution_attempts"
                )
            ),
            "continuous_authority":phase2_live_option_canary_payload.get(
                "continuous_authority"
            ),
            "grant_id":live_grant_id,
            "grant_scope":live_scope,
            "world_lease":live_lease,
            "world_lease_before_execute":live_lease_before,
            "ledger_consumed_at":live_consumed_at,
            "ledger_consume_result":live_ledger_after.get(
                "consume_result"
            ),
            "transaction_committed":phase2_live_option_canary_payload.get(
                "transaction_committed"
            ),
            "rollback_observed":phase2_live_option_canary_payload.get(
                "rollback_observed"
            ),
            "action_status":live_result.get("status"),
            "changed_world":live_result.get("changed_world"),
            "functional_accept":phase2_live_option_canary_payload.get(
                "functional_accept"
            ),
            "sustained_operation":phase2_live_option_canary_payload.get(
                "sustained_operation"
            ),
            "sustainability_classification":(
                phase2_live_option_canary_payload.get(
                    "sustainability_classification"
                )
            ),
            "tick_measurement_status":live_result.get(
                "tick_measurement_status"
            ),
            "observed_ticks":live_result.get("observed_ticks"),
            "candidate_after":live_final,
            "temporal_audit_path":str(phase2_live_temporal_audit_path),
            "temporal_audit_exists":phase2_live_temporal_audit,
            "temporal_audit_classification":(
                phase2_live_temporal_audit_payload.get("classification")
            ),
            "live_artifact_sha256":live_artifact_sha256,
            "read_error":phase2_live_option_canary_error,
            "temporal_audit_read_error":phase2_live_temporal_audit_error,
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
                blocked_running
                or blocked_invalid
                or blocked_release
                or phase2_exit_gate_valid
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
