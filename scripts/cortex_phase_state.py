#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
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


def _git_blob_sha256(
    state_root: Path,
    commit: str | None,
    relative_path: str,
) -> str | None:
    if not commit or not relative_path:
        return None
    try:
        done=subprocess.run(
            [
                "git",
                "-c",
                f"safe.directory={state_root}",
                "-C",
                str(state_root),
                "show",
                f"{commit}:{relative_path}",
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError,subprocess.SubprocessError):
        return None
    if done.returncode!=0:
        return None
    return hashlib.sha256(done.stdout).hexdigest()


def _jsonl_row(path: Path,index: int) -> dict[str, Any] | None:
    if index < 0 or not path.exists():
        return None
    try:
        for current,line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if current != index:
                continue
            value=json.loads(line)
            return value if isinstance(value,dict) else None
    except (OSError,json.JSONDecodeError,TypeError):
        return None
    return None


def _ledger_digests_match(
    path: Path,
    expected: dict[str, Any],
) -> tuple[bool,str | None,int | None]:
    if not path.exists() or not expected:
        return False,None,None
    connection: sqlite3.Connection | None=None
    try:
        connection=sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=10.0,
        )
        quick_check=str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check!="ok":
            return False,quick_check,None
        count=int(
            connection.execute(
                "SELECT COUNT(*) FROM executive_episodes"
            ).fetchone()[0]
        )
        for episode_id,digest in expected.items():
            if not isinstance(episode_id,str) or not episode_id:
                return False,quick_check,count
            if not isinstance(digest,str) or not digest:
                return False,quick_check,count
            row=connection.execute(
                """
                SELECT payload_sha256
                FROM executive_episodes
                WHERE episode_id=?
                """,
                (episode_id,),
            ).fetchone()
            if row is None or row[0]!=digest:
                return False,quick_check,count
        return True,quick_check,count
    except (sqlite3.Error,OSError,TypeError,ValueError):
        return False,None,None
    finally:
        if connection is not None:
            connection.close()


def _memory_batch_manifest(
    path: Path,
    batch_id: str,
) -> tuple[str | None,str | None,int | None,str | None]:
    if not path.exists() or not batch_id:
        return None,None,None,None
    connection: sqlite3.Connection | None=None
    try:
        connection=sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=10.0,
        )
        quick_check=str(connection.execute("PRAGMA quick_check").fetchone()[0])
        schema_row=connection.execute(
            "SELECT value FROM memory_meta WHERE key=?",
            ("schema_version",),
        ).fetchone()
        schema=None if schema_row is None else str(schema_row[0])
        rows=connection.execute(
            """
            SELECT
                o.occurrence_id,
                o.memory_id,
                i.item_digest,
                o.payload_sha256,
                o.qualified,
                o.contradiction,
                o.reward
            FROM memory_occurrences o
            JOIN memory_items i ON i.memory_id=o.memory_id
            WHERE o.batch_id=?
            ORDER BY o.occurrence_id
            """,
            (batch_id,),
        ).fetchall()
        manifest=[
            {
                "occurrence_id":str(row[0]),
                "memory_id":str(row[1]),
                "item_digest":str(row[2]),
                "payload_sha256":str(row[3]),
                "qualified":bool(row[4]),
                "contradiction":bool(row[5]),
                "reward":None if row[6] is None else float(row[6]),
            }
            for row in rows
        ]
        digest=hashlib.sha256(
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",",":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return quick_check,schema,len(manifest),digest
    except (sqlite3.Error,OSError,TypeError,ValueError):
        return None,None,None,None
    finally:
        if connection is not None:
            connection.close()


def _observed_repair_row_matches(
    path: Path,
    observed: dict[str, Any],
) -> bool:
    row_index=observed.get("row_index")
    if not isinstance(row_index,int) or isinstance(row_index,bool):
        return False
    row=_jsonl_row(path,row_index)
    if row is None:
        return False
    keys=(
        "run_id",
        "generation",
        "stage",
        "symptom",
        "action_key",
        "executed",
        "targets",
        "outcome",
    )
    return all(row.get(key)==observed.get(key) for key in keys)


def _paired_repair_row_matches(
    path: Path,
    observed: dict[str, Any],
) -> bool:
    row_index=observed.get("row_index")
    if not isinstance(row_index,int) or isinstance(row_index,bool):
        return False
    row=_jsonl_row(path,row_index)
    if row is None:
        return False
    keys=(
        "run_id",
        "generation",
        "stage",
        "symptom",
        "action_key",
        "choice_basis",
        "executed",
        "targets",
        "outcome",
    )
    return all(row.get(key)==observed.get(key) for key in keys)


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
    phase3_observed_row_match=_observed_repair_row_matches(
        phase3_repairs_path,
        phase3_observed,
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
        and isinstance(phase3_observed.get("source_sha256"),str)
        and bool(phase3_observed.get("source_sha256"))
        and phase3_observed_row_match
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

    phase3_credit_doc_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE3_VERIFICATION_CREDIT_LEDGER.md"
    )
    phase3_credit_doc=phase3_credit_doc_path.exists()
    phase3_credit_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f3b_verification_credit_replay.json"
    )
    phase3_credit_audit=phase3_credit_audit_path.exists()
    phase3_credit_payload: dict[str, Any]={}
    phase3_credit_error: str | None=None
    if phase3_credit_audit:
        try:
            phase3_credit_payload=_load(phase3_credit_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase3_credit_error=f"{type(exc).__name__}: {exc}"

    phase3_credit_revision=phase3_credit_payload.get("code_revision")
    if not isinstance(phase3_credit_revision,dict):
        phase3_credit_revision={}
    phase3_credit_verification=phase3_credit_payload.get("verification")
    if not isinstance(phase3_credit_verification,dict):
        phase3_credit_verification={}
    phase3_credit_assignment=phase3_credit_payload.get("credit")
    if not isinstance(phase3_credit_assignment,dict):
        phase3_credit_assignment={}
    phase3_credit_ledger=phase3_credit_payload.get("ledger")
    if not isinstance(phase3_credit_ledger,dict):
        phase3_credit_ledger={}
    phase3_credit_checks=phase3_credit_payload.get("checks")
    if not isinstance(phase3_credit_checks,dict):
        phase3_credit_checks={}
    phase3_credit_episode_digests=phase3_credit_ledger.get(
        "episode_digests"
    )
    if not isinstance(phase3_credit_episode_digests,dict):
        phase3_credit_episode_digests={}
    phase3_credit_ledger_relative=phase3_credit_ledger.get("path")
    phase3_credit_ledger_path=(
        state_root / phase3_credit_ledger_relative
        if isinstance(phase3_credit_ledger_relative,str)
        and phase3_credit_ledger_relative
        else state_root
        / "runs"
        / "ledger"
        / "cortex_executive_episodes.sqlite3"
    )
    (
        phase3_credit_ledger_matches,
        phase3_credit_ledger_quick_check,
        phase3_credit_ledger_count,
    )=_ledger_digests_match(
        phase3_credit_ledger_path,
        phase3_credit_episode_digests,
    )
    phase3_selected_count=phase3_credit_verification.get(
        "selected_episode_count"
    )
    phase3_credit_valid=(
        phase3_executive_valid
        and phase3_credit_doc
        and phase3_credit_audit
        and phase3_credit_error is None
        and phase3_credit_payload.get("schema_version")
        =="cortex_f3b_verification_credit_replay_v1"
        and phase3_credit_payload.get("status")=="pass"
        and phase3_credit_revision.get("dirty") is False
        and isinstance(phase3_credit_revision.get("commit"),str)
        and bool(phase3_credit_revision.get("commit"))
        and phase3_credit_payload.get("authority")=="shadow"
        and phase3_credit_payload.get("world_mutation") is False
        and phase3_credit_payload.get("factorio_rcon_used") is False
        and phase3_credit_payload.get("fle_environment_created") is False
        and phase3_credit_payload.get("world_lease_acquired") is False
        and phase3_credit_payload.get("execution_grant_created") is False
        and phase3_credit_payload.get("continuous_authority") is False
        and isinstance(phase3_selected_count,int)
        and not isinstance(phase3_selected_count,bool)
        and phase3_selected_count>0
        and phase3_credit_verification.get("matches_recorded")
        ==phase3_selected_count
        and phase3_credit_verification.get("unmeasured")==0
        and phase3_credit_assignment.get("eligible")
        ==phase3_selected_count
        and phase3_credit_assignment.get("ineligible")==0
        and phase3_credit_ledger.get("quick_check")=="ok"
        and phase3_credit_ledger.get("episode_count")
        ==phase3_selected_count
        and len(phase3_credit_episode_digests)==phase3_selected_count
        and phase3_credit_ledger_matches
        and phase3_credit_ledger_quick_check=="ok"
        and isinstance(phase3_credit_ledger_count,int)
        and phase3_credit_ledger_count>=phase3_selected_count
        and bool(phase3_credit_checks)
        and all(value is True for value in phase3_credit_checks.values())
    )

    phase3_comparison_doc_path=(
        state_root
        / "docs"
        / "CORTEX_PHASE3_PAIRED_SHADOW_COMPARISON.md"
    )
    phase3_comparison_doc=phase3_comparison_doc_path.exists()
    phase3_comparison_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f3c_paired_shadow_comparison.json"
    )
    phase3_comparison_audit=phase3_comparison_audit_path.exists()
    phase3_comparison_payload: dict[str, Any]={}
    phase3_comparison_error: str | None=None
    if phase3_comparison_audit:
        try:
            phase3_comparison_payload=_load(phase3_comparison_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase3_comparison_error=f"{type(exc).__name__}: {exc}"

    phase3_comparison_revision=phase3_comparison_payload.get("code_revision")
    if not isinstance(phase3_comparison_revision,dict):
        phase3_comparison_revision={}
    phase3_comparison=phase3_comparison_payload.get("comparison")
    if not isinstance(phase3_comparison,dict):
        phase3_comparison={}
    phase3_comparison_pairs=phase3_comparison_payload.get("pairs")
    if not isinstance(phase3_comparison_pairs,list):
        phase3_comparison_pairs=[]
    phase3_comparison_checks=phase3_comparison_payload.get("checks")
    if not isinstance(phase3_comparison_checks,dict):
        phase3_comparison_checks={}
    phase3_pair_rows_match=bool(phase3_comparison_pairs) and all(
        isinstance(pair,dict)
        and isinstance(pair.get("observed"),dict)
        and _paired_repair_row_matches(
            phase3_repairs_path,
            pair["observed"],
        )
        for pair in phase3_comparison_pairs
    )
    phase3_paired_count=phase3_comparison.get("paired_episode_count")
    phase3_policy_divergence=phase3_comparison.get(
        "policy_divergence_pairs"
    )
    phase3_comparison_valid=(
        phase3_credit_valid
        and phase3_comparison_doc
        and phase3_comparison_audit
        and phase3_comparison_error is None
        and phase3_comparison_payload.get("schema_version")
        =="cortex_f3c_paired_shadow_comparison_v1"
        and phase3_comparison_payload.get("status")=="pass"
        and phase3_comparison_revision.get("dirty") is False
        and isinstance(phase3_comparison_revision.get("commit"),str)
        and bool(phase3_comparison_revision.get("commit"))
        and phase3_comparison_payload.get("authority")=="shadow"
        and phase3_comparison_payload.get("world_mutation") is False
        and phase3_comparison_payload.get("factorio_rcon_used") is False
        and phase3_comparison_payload.get("fle_environment_created") is False
        and phase3_comparison_payload.get("world_lease_acquired") is False
        and phase3_comparison_payload.get("execution_grant_created") is False
        and phase3_comparison_payload.get("continuous_authority") is False
        and isinstance(phase3_paired_count,int)
        and not isinstance(phase3_paired_count,bool)
        and phase3_paired_count>=2
        and len(phase3_comparison_pairs)==phase3_paired_count
        and phase3_pair_rows_match
        and phase3_comparison.get("fixed_rule_basis_count")
        ==phase3_paired_count
        and phase3_comparison.get("canonical_fixed_rule_agreement")
        ==phase3_paired_count
        and phase3_comparison.get("multiple_candidate_pairs")
        ==phase3_paired_count
        and phase3_comparison.get(
            "candidate_set_policy_invariant_pairs"
        )==phase3_paired_count
        and phase3_comparison.get("legacy_action_coverage")
        ==phase3_paired_count
        and phase3_comparison.get("legacy_policy_agreement")
        ==phase3_paired_count
        and isinstance(phase3_policy_divergence,int)
        and not isinstance(phase3_policy_divergence,bool)
        and phase3_policy_divergence>0
        and phase3_comparison.get("observed_unexecuted_pairs")
        ==phase3_paired_count
        and phase3_comparison.get("observed_reward_count")==0
        and bool(phase3_comparison_checks)
        and all(value is True for value in phase3_comparison_checks.values())
    )
    phase3_exit_gate_valid=phase3_comparison_valid

    phase4_memory_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_MEMORY_SUBSTRATE.md"
    )
    phase4_memory_doc=phase4_memory_doc_path.exists()
    phase4_memory_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f4a_memory_substrate_migration.json"
    )
    phase4_memory_audit=phase4_memory_audit_path.exists()
    phase4_memory_payload: dict[str, Any]={}
    phase4_memory_error: str | None=None
    if phase4_memory_audit:
        try:
            phase4_memory_payload=_load(phase4_memory_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_memory_error=f"{type(exc).__name__}: {exc}"

    phase4_memory_revision=phase4_memory_payload.get("code_revision")
    if not isinstance(phase4_memory_revision,dict):
        phase4_memory_revision={}
    phase4_memory_store=phase4_memory_payload.get("store")
    if not isinstance(phase4_memory_store,dict):
        phase4_memory_store={}
    phase4_memory_snapshot=phase4_memory_store.get("snapshot")
    if not isinstance(phase4_memory_snapshot,dict):
        phase4_memory_snapshot={}
    phase4_memory_batch=phase4_memory_store.get("batch_manifest")
    if not isinstance(phase4_memory_batch,dict):
        phase4_memory_batch={}
    phase4_memory_working=phase4_memory_payload.get("working_memory")
    if not isinstance(phase4_memory_working,dict):
        phase4_memory_working={}
    phase4_memory_checks=phase4_memory_payload.get("checks")
    if not isinstance(phase4_memory_checks,dict):
        phase4_memory_checks={}
    phase4_memory_relative=phase4_memory_store.get("path")
    phase4_memory_path=(
        state_root / phase4_memory_relative
        if isinstance(phase4_memory_relative,str) and phase4_memory_relative
        else state_root / "runs" / "ledger" / "cortex_cognitive_memory.sqlite3"
    )
    phase4_batch_id=phase4_memory_payload.get("run_id")
    if not isinstance(phase4_batch_id,str):
        phase4_batch_id=""
    (
        phase4_live_quick_check,
        phase4_live_schema,
        phase4_live_batch_count,
        phase4_live_manifest_sha,
    )=_memory_batch_manifest(
        phase4_memory_path,
        phase4_batch_id,
    )

    def _memory_kind_positive(kind: str) -> bool:
        row=phase4_memory_snapshot.get(kind)
        return (
            isinstance(row,dict)
            and isinstance(row.get("items"),int)
            and not isinstance(row.get("items"),bool)
            and row["items"]>0
            and isinstance(row.get("occurrences"),int)
            and not isinstance(row.get("occurrences"),bool)
            and row["occurrences"]>0
        )

    phase4_memory_valid=(
        phase3_exit_gate_valid
        and phase4_memory_doc
        and phase4_memory_audit
        and phase4_memory_error is None
        and phase4_memory_payload.get("schema_version")
        =="cortex_f4a_memory_substrate_migration_v1"
        and phase4_memory_payload.get("status")=="pass"
        and phase4_memory_revision.get("dirty") is False
        and isinstance(phase4_memory_revision.get("commit"),str)
        and bool(phase4_memory_revision.get("commit"))
        and phase4_memory_payload.get("authority")=="shadow"
        and phase4_memory_payload.get("world_mutation") is False
        and phase4_memory_payload.get("factorio_rcon_used") is False
        and phase4_memory_payload.get("fle_environment_created") is False
        and phase4_memory_payload.get("world_lease_acquired") is False
        and phase4_memory_payload.get("execution_grant_created") is False
        and phase4_memory_payload.get("continuous_authority") is False
        and phase4_memory_working.get("persistent") is False
        and phase4_memory_working.get("evicted_oldest") is True
        and isinstance(phase4_memory_working.get("capacity"),int)
        and phase4_memory_working.get("size")
        ==phase4_memory_working.get("capacity")
        and all(
            _memory_kind_positive(kind)
            for kind in (
                "episodic",
                "semantic",
                "procedural",
                "counterexample",
            )
        )
        and phase4_memory_store.get("quick_check")=="ok"
        and phase4_memory_batch.get("batch_id")==phase4_batch_id
        and isinstance(phase4_memory_batch.get("occurrence_count"),int)
        and not isinstance(
            phase4_memory_batch.get("occurrence_count"),
            bool,
        )
        and phase4_memory_batch.get("occurrence_count")>0
        and isinstance(phase4_memory_batch.get("manifest_sha256"),str)
        and len(phase4_memory_batch.get("manifest_sha256"))==64
        and phase4_live_quick_check=="ok"
        and phase4_live_schema=="cortex_cognitive_memory_v1"
        and phase4_live_batch_count
        ==phase4_memory_batch.get("occurrence_count")
        and phase4_live_manifest_sha
        ==phase4_memory_batch.get("manifest_sha256")
        and bool(phase4_memory_checks)
        and all(value is True for value in phase4_memory_checks.values())
    )

    phase4_retrieval_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_MEMORY_RETRIEVAL.md"
    )
    phase4_retrieval_doc=phase4_retrieval_doc_path.exists()
    phase4_retrieval_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f4b_memory_retrieval.json"
    )
    phase4_retrieval_audit=phase4_retrieval_audit_path.exists()
    phase4_retrieval_payload: dict[str, Any]={}
    phase4_retrieval_error: str | None=None
    if phase4_retrieval_audit:
        try:
            phase4_retrieval_payload=_load(phase4_retrieval_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_retrieval_error=f"{type(exc).__name__}: {exc}"

    phase4_retrieval_revision=phase4_retrieval_payload.get("code_revision")
    if not isinstance(phase4_retrieval_revision,dict):
        phase4_retrieval_revision={}
    phase4_retrieval_source=phase4_retrieval_payload.get("source")
    if not isinstance(phase4_retrieval_source,dict):
        phase4_retrieval_source={}
    phase4_retrieval_before=phase4_retrieval_source.get("database_before")
    if not isinstance(phase4_retrieval_before,dict):
        phase4_retrieval_before={}
    phase4_retrieval_after=phase4_retrieval_source.get("database_after")
    if not isinstance(phase4_retrieval_after,dict):
        phase4_retrieval_after={}
    phase4_retrievals=phase4_retrieval_payload.get("retrievals")
    if not isinstance(phase4_retrievals,dict):
        phase4_retrievals={}
    phase4_consolidation=phase4_retrieval_payload.get("consolidation")
    if not isinstance(phase4_consolidation,dict):
        phase4_consolidation={}
    phase4_decay_probe=phase4_retrieval_payload.get("decay_probe")
    if not isinstance(phase4_decay_probe,dict):
        phase4_decay_probe={}
    phase4_retrieval_checks=phase4_retrieval_payload.get("checks")
    if not isinstance(phase4_retrieval_checks,dict):
        phase4_retrieval_checks={}
    phase4_f4a_sha=(
        _sha256(phase4_memory_audit_path)
        if phase4_memory_audit_path.exists()
        else None
    )
    phase4_expected_queries={
        "semantic-smelting-output",
        "semantic-smelting-placement-error",
        "cross-kind-fuel-procedure",
        "counterexample-electric-route-buffer",
    }
    phase4_retrieval_valid=(
        phase4_memory_valid
        and phase4_retrieval_doc
        and phase4_retrieval_audit
        and phase4_retrieval_error is None
        and phase4_retrieval_payload.get("schema_version")
        =="cortex_f4b_memory_retrieval_v1"
        and phase4_retrieval_payload.get("status")=="pass"
        and phase4_retrieval_revision.get("dirty") is False
        and isinstance(phase4_retrieval_revision.get("commit"),str)
        and bool(phase4_retrieval_revision.get("commit"))
        and phase4_retrieval_payload.get("authority")=="shadow"
        and phase4_retrieval_payload.get("world_mutation") is False
        and phase4_retrieval_payload.get("factorio_rcon_used") is False
        and phase4_retrieval_payload.get("fle_environment_created") is False
        and phase4_retrieval_payload.get("world_lease_acquired") is False
        and phase4_retrieval_payload.get("execution_grant_created") is False
        and phase4_retrieval_payload.get("continuous_authority") is False
        and phase4_retrieval_before==phase4_retrieval_after
        and phase4_retrieval_before.get("quick_check")=="ok"
        and phase4_retrieval_before.get("schema_version")
        =="cortex_cognitive_memory_v1"
        and isinstance(phase4_retrieval_before.get("item_count"),int)
        and phase4_retrieval_before.get("item_count",0)>0
        and isinstance(phase4_retrieval_before.get("occurrence_count"),int)
        and phase4_retrieval_before.get("occurrence_count",0)>0
        and isinstance(
            phase4_retrieval_before.get("manifest_sha256"),
            str,
        )
        and len(phase4_retrieval_before.get("manifest_sha256",""))==64
        and phase4_retrieval_source.get("f4a_artifact_sha256")
        ==phase4_f4a_sha
        and set(phase4_retrievals)==phase4_expected_queries
        and all(
            isinstance(row,dict)
            and isinstance(row.get("results"),list)
            and bool(row.get("results"))
            for row in phase4_retrievals.values()
        )
        and phase4_consolidation.get("repeated_semantic_items",0)>0
        and phase4_consolidation.get("semantic_duplicate_support",0)>0
        and phase4_consolidation.get("repeated_counterexample_items",0)>0
        and phase4_consolidation.get("procedural_confidence_items",0)>0
        and phase4_decay_probe.get("destructive_deletion") is False
        and bool(phase4_retrieval_checks)
        and all(value is True for value in phase4_retrieval_checks.values())
    )

    phase4_causal_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_CAUSAL_ABLATION_PROTOCOL.md"
    )
    phase4_causal_doc=phase4_causal_doc_path.exists()
    phase4_causal_manifest_path=(
        state_root / "configs" / "cortex_f4c_causal_ablation_v1.json"
    )
    phase4_causal_manifest=phase4_causal_manifest_path.exists()
    phase4_causal_manifest_payload: dict[str, Any]={}
    phase4_causal_manifest_error: str | None=None
    if phase4_causal_manifest:
        try:
            phase4_causal_manifest_payload=_load(phase4_causal_manifest_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_causal_manifest_error=f"{type(exc).__name__}: {exc}"

    phase4_causal_audit_path=(
        state_root / "runs" / "audits" / "cortex_f4c_protocol_freeze.json"
    )
    phase4_causal_audit=phase4_causal_audit_path.exists()
    phase4_causal_payload: dict[str, Any]={}
    phase4_causal_error: str | None=None
    if phase4_causal_audit:
        try:
            phase4_causal_payload=_load(phase4_causal_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_causal_error=f"{type(exc).__name__}: {exc}"

    phase4_causal_revision=phase4_causal_payload.get("code_revision")
    if not isinstance(phase4_causal_revision,dict):
        phase4_causal_revision={}
    phase4_causal_protocol=phase4_causal_payload.get("protocol")
    if not isinstance(phase4_causal_protocol,dict):
        phase4_causal_protocol={}
    phase4_causal_source=phase4_causal_payload.get("source")
    if not isinstance(phase4_causal_source,dict):
        phase4_causal_source={}
    phase4_causal_checks=phase4_causal_payload.get("checks")
    if not isinstance(phase4_causal_checks,dict):
        phase4_causal_checks={}

    phase4_causal_manifest_sha=(
        _sha256(phase4_causal_manifest_path)
        if phase4_causal_manifest_path.exists()
        else None
    )
    phase4_f4b_sha=(
        _sha256(phase4_retrieval_audit_path)
        if phase4_retrieval_audit_path.exists()
        else None
    )
    phase4_expected_confirmatory=list(range(20261101,20261111))
    phase4_manifest_partitions=phase4_causal_manifest_payload.get(
        "seed_partitions"
    )
    if not isinstance(phase4_manifest_partitions,dict):
        phase4_manifest_partitions={}
    phase4_manifest_source=phase4_causal_manifest_payload.get("source_memory")
    if not isinstance(phase4_manifest_source,dict):
        phase4_manifest_source={}

    phase4_causal_protocol_valid=(
        phase4_retrieval_valid
        and phase4_causal_doc
        and phase4_causal_manifest
        and phase4_causal_audit
        and phase4_causal_manifest_error is None
        and phase4_causal_error is None
        and phase4_causal_manifest_payload.get("schema_version")
        =="cortex_f4c_causal_ablation_protocol_v1"
        and phase4_causal_manifest_payload.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_causal_manifest_payload.get("status")=="frozen"
        and phase4_causal_manifest_payload.get("authority")=="shadow"
        and phase4_causal_manifest_payload.get("continuous_authority") is False
        and phase4_manifest_partitions.get("confirmatory_reserved")
        ==phase4_expected_confirmatory
        and phase4_manifest_source.get("manifest_sha256")
        ==phase4_retrieval_before.get("manifest_sha256")
        and phase4_causal_payload.get("schema_version")
        =="cortex_f4c_protocol_freeze_v1"
        and phase4_causal_payload.get("status")=="pass"
        and phase4_causal_payload.get("authority")=="shadow"
        and phase4_causal_payload.get("world_mutation") is False
        and phase4_causal_payload.get("factorio_rcon_used") is False
        and phase4_causal_payload.get("fle_environment_created") is False
        and phase4_causal_payload.get("world_lease_acquired") is False
        and phase4_causal_payload.get("execution_grant_created") is False
        and phase4_causal_payload.get("continuous_authority") is False
        and phase4_causal_revision.get("dirty") is False
        and isinstance(phase4_causal_revision.get("commit"),str)
        and bool(phase4_causal_revision.get("commit"))
        and phase4_causal_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_causal_protocol.get("file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_causal_protocol.get("evaluation_pair_count")==20
        and phase4_causal_protocol.get("pilot_pair_count")==8
        and phase4_causal_protocol.get("task_family_count")>=4
        and phase4_causal_protocol.get("memory_on_first")==10
        and phase4_causal_protocol.get("memory_ablated_first")==10
        and phase4_causal_protocol.get("confirmatory_reserved")
        ==phase4_expected_confirmatory
        and phase4_causal_source.get("f4b_artifact_sha256")
        ==phase4_f4b_sha
        and phase4_causal_source.get("memory_manifest_sha256")
        ==phase4_retrieval_before.get("manifest_sha256")
        and bool(phase4_causal_checks)
        and all(value is True for value in phase4_causal_checks.values())
    )

    phase4_harness_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_CAUSAL_HARNESS.md"
    )
    phase4_harness_module_path=(
        state_root / "src" / "factorio_ai_lab" / "cortex" / "causal_harness.py"
    )
    phase4_harness_validator_path=(
        state_root / "scripts" / "validate_cortex_f4c_harness.py"
    )
    phase4_harness_tests_path=(
        state_root / "tests" / "test_cortex_f4c_causal_harness.py"
    )
    phase4_harness_audit_path=(
        state_root / "runs" / "audits" / "cortex_f4c_harness_validation.json"
    )
    phase4_harness_audit=phase4_harness_audit_path.exists()
    phase4_harness_payload: dict[str,Any]={}
    phase4_harness_error: str | None=None
    if phase4_harness_audit:
        try:
            phase4_harness_payload=_load(phase4_harness_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_harness_error=f"{type(exc).__name__}: {exc}"

    phase4_harness_revision=phase4_harness_payload.get("code_revision")
    if not isinstance(phase4_harness_revision,dict):
        phase4_harness_revision={}
    phase4_harness_protocol=phase4_harness_payload.get("protocol")
    if not isinstance(phase4_harness_protocol,dict):
        phase4_harness_protocol={}
    phase4_harness_source=phase4_harness_payload.get("source")
    if not isinstance(phase4_harness_source,dict):
        phase4_harness_source={}
    phase4_harness_before=phase4_harness_source.get("database_before")
    if not isinstance(phase4_harness_before,dict):
        phase4_harness_before={}
    phase4_harness_after=phase4_harness_source.get("database_after")
    if not isinstance(phase4_harness_after,dict):
        phase4_harness_after={}
    phase4_harness_checks=phase4_harness_payload.get("checks")
    if not isinstance(phase4_harness_checks,dict):
        phase4_harness_checks={}

    phase4_harness_expected_hashes={
        "harness_module_sha256":(
            _sha256(phase4_harness_module_path)
            if phase4_harness_module_path.exists() else None
        ),
        "validator_script_sha256":(
            _sha256(phase4_harness_validator_path)
            if phase4_harness_validator_path.exists() else None
        ),
        "tests_sha256":(
            _sha256(phase4_harness_tests_path)
            if phase4_harness_tests_path.exists() else None
        ),
        "document_sha256":(
            _sha256(phase4_harness_doc_path)
            if phase4_harness_doc_path.exists() else None
        ),
    }
    phase4_harness_preflight_valid=(
        phase4_causal_protocol_valid
        and phase4_harness_doc_path.exists()
        and phase4_harness_module_path.exists()
        and phase4_harness_validator_path.exists()
        and phase4_harness_tests_path.exists()
        and phase4_harness_audit
        and phase4_harness_error is None
        and phase4_harness_payload.get("schema_version")
        =="cortex_f4c_harness_validation_v1"
        and phase4_harness_payload.get("status")=="pass"
        and phase4_harness_payload.get("mode")=="synthetic_preflight"
        and phase4_harness_payload.get("authority")=="shadow"
        and phase4_harness_payload.get("world_mutation") is False
        and phase4_harness_payload.get("factorio_rcon_used") is False
        and phase4_harness_payload.get("fle_environment_created") is False
        and phase4_harness_payload.get("world_lease_acquired") is False
        and phase4_harness_payload.get("execution_grant_created") is False
        and phase4_harness_payload.get("continuous_authority") is False
        and phase4_harness_payload.get("experimental_seed_executed") is False
        and phase4_harness_revision.get("dirty") is False
        and isinstance(phase4_harness_revision.get("commit"),str)
        and bool(phase4_harness_revision.get("commit"))
        and phase4_harness_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_harness_protocol.get("manifest_file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_harness_protocol.get("manifest_sha256")
        ==phase4_causal_protocol.get("manifest_sha256")
        and phase4_harness_before==phase4_harness_after
        and phase4_harness_before.get("manifest_sha256")
        ==phase4_retrieval_before.get("manifest_sha256")
        and phase4_harness_before.get("item_count")
        ==phase4_retrieval_before.get("item_count")
        and phase4_harness_before.get("occurrence_count")
        ==phase4_retrieval_before.get("occurrence_count")
        and all(
            phase4_harness_source.get(key)==value
            for key,value in phase4_harness_expected_hashes.items()
        )
        and bool(phase4_harness_checks)
        and all(value is True for value in phase4_harness_checks.values())
    )
    phase4_real_adapter_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_REAL_ADAPTER_VALIDATION.md"
    )
    phase4_real_adapter_map_path=(
        state_root / "docs" / "CORTEX_PHASE4_REAL_ADAPTER_MAP.md"
    )
    phase4_real_adapter_module_path=(
        state_root
        / "src"
        / "factorio_ai_lab"
        / "cortex"
        / "causal_real_adapters.py"
    )
    phase4_real_adapter_validator_path=(
        state_root / "scripts" / "validate_cortex_f4c_real_adapters.py"
    )
    phase4_real_adapter_tests_path=(
        state_root / "tests" / "test_cortex_f4c_real_adapters.py"
    )
    phase4_real_adapter_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f4c_real_adapters_validation.json"
    )
    phase4_real_adapter_audit=phase4_real_adapter_audit_path.exists()
    phase4_real_adapter_payload: dict[str,Any]={}
    phase4_real_adapter_error: str | None=None
    if phase4_real_adapter_audit:
        try:
            phase4_real_adapter_payload=_load(phase4_real_adapter_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_real_adapter_error=f"{type(exc).__name__}: {exc}"

    phase4_real_adapter_revision=phase4_real_adapter_payload.get("code_revision")
    if not isinstance(phase4_real_adapter_revision,dict):
        phase4_real_adapter_revision={}
    phase4_real_adapter_protocol=phase4_real_adapter_payload.get("protocol")
    if not isinstance(phase4_real_adapter_protocol,dict):
        phase4_real_adapter_protocol={}
    phase4_real_adapter_source=phase4_real_adapter_payload.get("source")
    if not isinstance(phase4_real_adapter_source,dict):
        phase4_real_adapter_source={}
    phase4_real_adapter_before=phase4_real_adapter_source.get("database_before")
    if not isinstance(phase4_real_adapter_before,dict):
        phase4_real_adapter_before={}
    phase4_real_adapter_after=phase4_real_adapter_source.get("database_after")
    if not isinstance(phase4_real_adapter_after,dict):
        phase4_real_adapter_after={}
    phase4_real_adapter_checks=phase4_real_adapter_payload.get("checks")
    if not isinstance(phase4_real_adapter_checks,dict):
        phase4_real_adapter_checks={}

    phase4_real_adapter_expected_hashes={
        "adapter_module_sha256":(
            _sha256(phase4_real_adapter_module_path)
            if phase4_real_adapter_module_path.exists() else None
        ),
        "validator_script_sha256":(
            _sha256(phase4_real_adapter_validator_path)
            if phase4_real_adapter_validator_path.exists() else None
        ),
        "tests_sha256":(
            _sha256(phase4_real_adapter_tests_path)
            if phase4_real_adapter_tests_path.exists() else None
        ),
        "document_sha256":(
            _sha256(phase4_real_adapter_doc_path)
            if phase4_real_adapter_doc_path.exists() else None
        ),
        "adapter_map_sha256":(
            _sha256(phase4_real_adapter_map_path)
            if phase4_real_adapter_map_path.exists() else None
        ),
    }
    phase4_real_task_adapters_valid=(
        phase4_harness_preflight_valid
        and phase4_real_adapter_doc_path.exists()
        and phase4_real_adapter_map_path.exists()
        and phase4_real_adapter_module_path.exists()
        and phase4_real_adapter_validator_path.exists()
        and phase4_real_adapter_tests_path.exists()
        and phase4_real_adapter_audit
        and phase4_real_adapter_error is None
        and phase4_real_adapter_payload.get("schema_version")
        =="cortex_f4c_real_adapters_validation_v1"
        and phase4_real_adapter_payload.get("status")=="pass"
        and phase4_real_adapter_payload.get("mode")=="real_adapter_preflight"
        and phase4_real_adapter_payload.get("authority_scope")
        =="disposable_non_protocol_only"
        and phase4_real_adapter_payload.get("live_world_mutation") is False
        and phase4_real_adapter_payload.get("factorio_rcon_used") is False
        and phase4_real_adapter_payload.get("real_fle_environment_created") is False
        and phase4_real_adapter_payload.get("world_lease_acquired") is False
        and phase4_real_adapter_payload.get("live_execution_grant_created") is False
        and phase4_real_adapter_payload.get("continuous_authority") is False
        and phase4_real_adapter_payload.get("protocol_partitions_executed") is False
        and phase4_real_adapter_payload.get("experimental_seed_executed") is False
        and phase4_real_adapter_payload.get("pilot_seed_executed") is False
        and phase4_real_adapter_payload.get("evaluation_seed_executed") is False
        and phase4_real_adapter_payload.get("confirmatory_seed_executed") is False
        and phase4_real_adapter_payload.get(
            "transactional_fle_executor_exercised"
        ) is True
        and phase4_real_adapter_revision.get("dirty") is False
        and isinstance(phase4_real_adapter_revision.get("commit"),str)
        and bool(phase4_real_adapter_revision.get("commit"))
        and phase4_real_adapter_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_real_adapter_protocol.get("manifest_file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_real_adapter_protocol.get("manifest_sha256")
        ==phase4_causal_protocol.get("manifest_sha256")
        and phase4_real_adapter_before==phase4_real_adapter_after
        and phase4_real_adapter_before.get("manifest_sha256")
        ==phase4_retrieval_before.get("manifest_sha256")
        and phase4_real_adapter_before.get("item_count")
        ==phase4_retrieval_before.get("item_count")
        and phase4_real_adapter_before.get("occurrence_count")
        ==phase4_retrieval_before.get("occurrence_count")
        and all(
            phase4_real_adapter_source.get(key)==value
            for key,value in phase4_real_adapter_expected_hashes.items()
        )
        and bool(phase4_real_adapter_checks)
        and all(value is True for value in phase4_real_adapter_checks.values())
    )
    phase4_treatment_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_TREATMENT_AND_RUNNER_GATE.md"
    )
    phase4_treatment_module_path=(
        state_root
        / "src"
        / "factorio_ai_lab"
        / "cortex"
        / "causal_treatment.py"
    )
    phase4_treatment_validator_path=(
        state_root / "scripts" / "validate_cortex_f4c_treatment.py"
    )
    phase4_treatment_tests_path=(
        state_root / "tests" / "test_cortex_f4c_treatment.py"
    )
    phase4_treatment_audit_path=(
        state_root / "runs" / "audits" / "cortex_f4c_treatment_validation.json"
    )
    phase4_treatment_audit=phase4_treatment_audit_path.exists()
    phase4_treatment_payload: dict[str,Any]={}
    phase4_treatment_error: str | None=None
    if phase4_treatment_audit:
        try:
            phase4_treatment_payload=_load(phase4_treatment_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_treatment_error=f"{type(exc).__name__}: {exc}"
    phase4_treatment_revision=phase4_treatment_payload.get("code_revision")
    if not isinstance(phase4_treatment_revision,dict):
        phase4_treatment_revision={}
    phase4_treatment_protocol=phase4_treatment_payload.get("protocol")
    if not isinstance(phase4_treatment_protocol,dict):
        phase4_treatment_protocol={}
    phase4_treatment_source=phase4_treatment_payload.get("source")
    if not isinstance(phase4_treatment_source,dict):
        phase4_treatment_source={}
    phase4_treatment_before=phase4_treatment_source.get("database_before")
    if not isinstance(phase4_treatment_before,dict):
        phase4_treatment_before={}
    phase4_treatment_after=phase4_treatment_source.get("database_after")
    if not isinstance(phase4_treatment_after,dict):
        phase4_treatment_after={}
    phase4_treatment_checks=phase4_treatment_payload.get("checks")
    if not isinstance(phase4_treatment_checks,dict):
        phase4_treatment_checks={}
    phase4_treatment_expected_hashes={
        "treatment_module_sha256":(
            _sha256(phase4_treatment_module_path)
            if phase4_treatment_module_path.exists() else None
        ),
        "validator_sha256":(
            _sha256(phase4_treatment_validator_path)
            if phase4_treatment_validator_path.exists() else None
        ),
        "tests_sha256":(
            _sha256(phase4_treatment_tests_path)
            if phase4_treatment_tests_path.exists() else None
        ),
    }
    phase4_treatment_valid=(
        phase4_real_task_adapters_valid
        and phase4_treatment_doc_path.exists()
        and phase4_treatment_module_path.exists()
        and phase4_treatment_validator_path.exists()
        and phase4_treatment_tests_path.exists()
        and phase4_treatment_audit
        and phase4_treatment_error is None
        and phase4_treatment_payload.get("schema_version")
        =="cortex_f4c_treatment_validation_v1"
        and phase4_treatment_payload.get("status")=="pass"
        and phase4_treatment_payload.get("mode")
        =="treatment_semantics_preflight"
        and phase4_treatment_revision.get("dirty") is False
        and phase4_treatment_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_treatment_protocol.get("manifest_file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_treatment_protocol.get("manifest_sha256")
        ==phase4_causal_protocol.get("manifest_sha256")
        and phase4_treatment_before==phase4_treatment_after
        and phase4_treatment_before.get("manifest_sha256")
        ==phase4_retrieval_before.get("manifest_sha256")
        and all(
            phase4_treatment_source.get(key)==value
            for key,value in phase4_treatment_expected_hashes.items()
        )
        and bool(phase4_treatment_checks)
        and all(value is True for value in phase4_treatment_checks.values())
        and (phase4_treatment_payload.get("authority") or {}).get(
            "world_mutation"
        ) is False
        and (phase4_treatment_payload.get("authority") or {}).get(
            "protocol_seed_executed"
        ) is False
    )

    phase4_pilot_runner_script_path=(
        state_root / "scripts" / "run_cortex_f4c_pilot.py"
    )
    phase4_pilot_runner_runtime_path=(
        state_root
        / "src"
        / "factorio_ai_lab"
        / "cortex"
        / "causal_pilot_runtime.py"
    )
    phase4_pilot_runner_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_PILOT_RUNNER.md"
    )
    phase4_pilot_pair_auditor_path=(
        state_root / "scripts" / "audit_cortex_f4c_pilot_pair.py"
    )
    phase4_pilot_pair_audit_tests_path=(
        state_root / "tests" / "test_cortex_f4c_pilot_audit.py"
    )
    phase4_pilot_runner_tests_path=(
        state_root / "tests" / "test_cortex_f4c_pilot_runner.py"
    )
    phase4_pilot_runner_validator_path=(
        state_root / "scripts" / "validate_cortex_f4c_pilot_runner.py"
    )
    phase4_pilot_runner_audit_path=(
        state_root / "runs" / "audits" / "cortex_f4c_pilot_runner_validation.json"
    )
    phase4_pilot_runner_audit=phase4_pilot_runner_audit_path.exists()
    phase4_pilot_runner_payload: dict[str,Any]={}
    phase4_pilot_runner_error: str | None=None
    if phase4_pilot_runner_audit:
        try:
            phase4_pilot_runner_payload=_load(phase4_pilot_runner_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_pilot_runner_error=f"{type(exc).__name__}: {exc}"
    phase4_pilot_runner_revision=phase4_pilot_runner_payload.get("code_revision")
    if not isinstance(phase4_pilot_runner_revision,dict):
        phase4_pilot_runner_revision={}
    phase4_pilot_runner_protocol=phase4_pilot_runner_payload.get("protocol")
    if not isinstance(phase4_pilot_runner_protocol,dict):
        phase4_pilot_runner_protocol={}
    phase4_pilot_runner_source=phase4_pilot_runner_payload.get("source")
    if not isinstance(phase4_pilot_runner_source,dict):
        phase4_pilot_runner_source={}
    phase4_pilot_runner_checks=phase4_pilot_runner_payload.get("checks")
    if not isinstance(phase4_pilot_runner_checks,dict):
        phase4_pilot_runner_checks={}
    phase4_pilot_runner_expected_hashes={
        "runner_sha256":(
            _sha256(phase4_pilot_runner_script_path)
            if phase4_pilot_runner_script_path.exists() else None
        ),
        "validator_sha256":(
            _sha256(phase4_pilot_runner_validator_path)
            if phase4_pilot_runner_validator_path.exists() else None
        ),
        "tests_sha256":(
            _sha256(phase4_pilot_runner_tests_path)
            if phase4_pilot_runner_tests_path.exists() else None
        ),
        "runtime_sha256":(
            _sha256(phase4_pilot_runner_runtime_path)
            if phase4_pilot_runner_runtime_path.exists() else None
        ),
        "document_sha256":(
            _sha256(phase4_pilot_runner_doc_path)
            if phase4_pilot_runner_doc_path.exists() else None
        ),
        "pair_auditor_sha256":(
            _sha256(phase4_pilot_pair_auditor_path)
            if phase4_pilot_pair_auditor_path.exists() else None
        ),
        "pair_audit_tests_sha256":(
            _sha256(phase4_pilot_pair_audit_tests_path)
            if phase4_pilot_pair_audit_tests_path.exists() else None
        ),
        "treatment_audit_sha256":(
            _sha256(phase4_treatment_audit_path)
            if phase4_treatment_audit_path.exists() else None
        ),
    }
    phase4_pilot_runner_valid=(
        phase4_treatment_valid
        and phase4_pilot_runner_script_path.exists()
        and phase4_pilot_runner_runtime_path.exists()
        and phase4_pilot_runner_doc_path.exists()
        and phase4_pilot_pair_auditor_path.exists()
        and phase4_pilot_pair_audit_tests_path.exists()
        and phase4_pilot_runner_tests_path.exists()
        and phase4_pilot_runner_validator_path.exists()
        and phase4_pilot_runner_audit
        and phase4_pilot_runner_error is None
        and phase4_pilot_runner_payload.get("schema_version")
        =="cortex_f4c_pilot_runner_validation_v1"
        and phase4_pilot_runner_payload.get("status")=="pass"
        and phase4_pilot_runner_payload.get("mode")=="pilot_runner_dry_run"
        and phase4_pilot_runner_revision.get("dirty") is False
        and phase4_pilot_runner_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_pilot_runner_protocol.get("manifest_file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_pilot_runner_protocol.get("manifest_sha256")
        ==phase4_causal_protocol.get("manifest_sha256")
        and all(
            phase4_pilot_runner_source.get(key)==value
            for key,value in phase4_pilot_runner_expected_hashes.items()
        )
        and bool(phase4_pilot_runner_checks)
        and all(value is True for value in phase4_pilot_runner_checks.values())
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "world_mutation"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "factorio_rcon_used"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "fle_environment_created"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "world_lease_acquired"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "continuous_authority"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "pilot_seed_executed"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "evaluation_seed_executed"
        ) is False
        and (phase4_pilot_runner_payload.get("authority") or {}).get(
            "confirmatory_seed_executed"
        ) is False
    )

    phase4_execution_ready=(
        phase4_harness_preflight_valid
        and phase4_real_task_adapters_valid
        and phase4_treatment_valid
        and phase4_pilot_runner_valid
    )

    phase4_pilot_raw_seeds=phase4_manifest_partitions.get("pilot")
    phase4_pilot_seeds=(
        tuple(int(value) for value in phase4_pilot_raw_seeds)
        if isinstance(phase4_pilot_raw_seeds,list)
        else ()
    )
    phase4_pilot_rows: list[dict[str,Any]]=[]
    phase4_pilot_progress_errors: list[str]=[]
    for pilot_index,pilot_seed in enumerate(phase4_pilot_seeds,start=1):
        pilot_dir=state_root/"runs"/"f4c_pilot"/str(pilot_seed)
        pair_path=pilot_dir/"pair.json"
        review_path=pilot_dir/"review.json"
        pair_exists=pair_path.exists()
        review_exists=review_path.exists()
        pair_payload: dict[str,Any]={}
        review_payload: dict[str,Any]={}
        pair_error: str | None=None
        review_error: str | None=None
        if pair_exists:
            try:
                pair_payload=_load(pair_path)
            except (OSError,json.JSONDecodeError,TypeError) as exc:
                pair_error=f"{type(exc).__name__}: {exc}"
        if review_exists:
            try:
                review_payload=_load(review_path)
            except (OSError,json.JSONDecodeError,TypeError) as exc:
                review_error=f"{type(exc).__name__}: {exc}"
        pair_sha=_sha256(pair_path) if pair_exists and pair_error is None else None
        pair_claim=pair_payload.get("claim_boundary")
        if not isinstance(pair_claim,dict):
            pair_claim={}
        pair_revision=pair_payload.get("code_revision")
        if not isinstance(pair_revision,dict):
            pair_revision={}
        pair_valid=(
            pair_exists
            and pair_error is None
            and pair_payload.get("schema_version")=="cortex_f4c_pilot_pair_v1"
            and pair_payload.get("status")=="completed"
            and pair_payload.get("seed")==pilot_seed
            and isinstance(pair_payload.get("task_id"),str)
            and pair_payload.get("live_factorio_world") is False
            and pair_payload.get("factorio_rcon_used") is False
            and pair_payload.get("fle_environment_created") is False
            and pair_payload.get("world_lease_acquired") is False
            and pair_payload.get("automatic_retry") is False
            and pair_revision.get("dirty") is False
            and pair_claim.get("pilot_only") is True
            and pair_claim.get("excluded_from_primary_f4c_inference") is True
            and pair_claim.get("evaluation_seed_executed") is False
            and pair_claim.get("confirmatory_seed_executed") is False
        )
        review_claim=review_payload.get("claim_boundary")
        if not isinstance(review_claim,dict):
            review_claim={}
        review_valid=(
            pair_valid
            and review_exists
            and review_error is None
            and review_payload.get("schema_version")
            =="cortex_f4c_pilot_pair_review_v1"
            and review_payload.get("status")=="pass"
            and review_payload.get("seed")==pilot_seed
            and review_payload.get("pair_artifact_sha256")==pair_sha
            and review_claim.get("instrumentation_only") is True
            and review_claim.get("variance_adaptation_forbidden") is True
            and review_claim.get("primary_f4c_inference") is False
            and review_claim.get("evaluation_partition_untouched") is True
            and review_claim.get("confirmatory_partition_untouched") is True
        )
        if pair_error is not None:
            phase4_pilot_progress_errors.append(
                f"{pilot_seed}:pair_read_error:{pair_error}"
            )
        if review_error is not None:
            phase4_pilot_progress_errors.append(
                f"{pilot_seed}:review_read_error:{review_error}"
            )
        if pair_exists and not pair_valid:
            phase4_pilot_progress_errors.append(
                f"{pilot_seed}:pair_invalid"
            )
        if review_exists and not review_valid:
            phase4_pilot_progress_errors.append(
                f"{pilot_seed}:review_invalid"
            )
        phase4_pilot_rows.append({
            "position":pilot_index,
            "seed":pilot_seed,
            "pair_path":str(pair_path),
            "pair_exists":pair_exists,
            "pair_valid":pair_valid,
            "pair_sha256":pair_sha,
            "pair_read_error":pair_error,
            "review_path":str(review_path),
            "review_exists":review_exists,
            "review_status":review_payload.get("status"),
            "review_valid":review_valid,
            "review_read_error":review_error,
        })

    phase4_pilot_pair_count=sum(
        1 for row in phase4_pilot_rows if row["pair_exists"]
    )
    phase4_pilot_reviewed_count=sum(
        1 for row in phase4_pilot_rows if row["review_valid"]
    )
    phase4_pilot_total=len(phase4_pilot_rows)
    phase4_pilot_first_gap=None
    for index,row in enumerate(phase4_pilot_rows):
        if not row["review_valid"]:
            phase4_pilot_first_gap=index
            break
    phase4_pilot_out_of_order: list[int]=[]
    if phase4_pilot_first_gap is not None:
        for row in phase4_pilot_rows[phase4_pilot_first_gap+1:]:
            if row["pair_exists"] or row["review_exists"]:
                phase4_pilot_out_of_order.append(int(row["seed"]))
    if phase4_pilot_out_of_order:
        phase4_pilot_progress_errors.append(
            "future_pilot_evidence_out_of_order:"
            +",".join(map(str,phase4_pilot_out_of_order))
        )

    phase4_pilot_awaiting_review_seed=None
    phase4_pilot_next_seed=None
    if phase4_pilot_first_gap is not None:
        first_gap_row=phase4_pilot_rows[phase4_pilot_first_gap]
        if first_gap_row["pair_exists"]:
            phase4_pilot_awaiting_review_seed=int(first_gap_row["seed"])
        else:
            phase4_pilot_next_seed=int(first_gap_row["seed"])
    phase4_pilot_complete=(
        phase4_pilot_total==8
        and phase4_pilot_pair_count==phase4_pilot_total
        and phase4_pilot_reviewed_count==phase4_pilot_total
    )
    phase4_pilot_progress_valid=(
        phase4_pilot_total==8
        and not phase4_pilot_progress_errors
        and not phase4_pilot_out_of_order
    )
    phase4_pilot_seed_launch_allowed=(
        phase4_execution_ready
        and phase4_pilot_progress_valid
        and phase4_pilot_awaiting_review_seed is None
        and phase4_pilot_next_seed is not None
        and not phase4_pilot_complete
    )
    if not phase4_execution_ready:
        phase4_pilot_progress_status="not_ready"
    elif not phase4_pilot_progress_valid:
        phase4_pilot_progress_status="invalid"
    elif phase4_pilot_complete:
        phase4_pilot_progress_status="complete"
    elif phase4_pilot_awaiting_review_seed is not None:
        phase4_pilot_progress_status="awaiting_review"
    elif phase4_pilot_reviewed_count>0:
        phase4_pilot_progress_status="in_progress"
    else:
        phase4_pilot_progress_status="ready"

    phase4_evaluation_doc_path=(
        state_root / "docs" / "CORTEX_PHASE4_EVALUATION_RUNNER.md"
    )
    phase4_evaluation_boundary_path=(
        state_root
        / "src"
        / "factorio_ai_lab"
        / "cortex"
        / "causal_evaluation.py"
    )
    phase4_evaluation_inference_module_path=(
        state_root
        / "src"
        / "factorio_ai_lab"
        / "cortex"
        / "causal_inference.py"
    )
    phase4_evaluation_runner_script_path=(
        state_root / "scripts" / "run_cortex_f4c_evaluation.py"
    )
    phase4_evaluation_validator_path=(
        state_root / "scripts" / "validate_cortex_f4c_evaluation_runner.py"
    )
    phase4_evaluation_pair_auditor_path=(
        state_root / "scripts" / "audit_cortex_f4c_evaluation_pair.py"
    )
    phase4_evaluation_analyzer_path=(
        state_root / "scripts" / "analyze_cortex_f4c_evaluation.py"
    )
    phase4_evaluation_tests_path=(
        state_root / "tests" / "test_cortex_f4c_evaluation.py"
    )
    phase4_evaluation_runner_tests_path=(
        state_root / "tests" / "test_cortex_f4c_evaluation_runner.py"
    )
    phase4_evaluation_runner_audit_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f4c_evaluation_runner_validation.json"
    )
    phase4_evaluation_runner_audit=phase4_evaluation_runner_audit_path.exists()
    phase4_evaluation_runner_payload: dict[str,Any]={}
    phase4_evaluation_runner_error: str | None=None
    if phase4_evaluation_runner_audit:
        try:
            phase4_evaluation_runner_payload=_load(
                phase4_evaluation_runner_audit_path
            )
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_evaluation_runner_error=f"{type(exc).__name__}: {exc}"
    phase4_evaluation_runner_revision=phase4_evaluation_runner_payload.get(
        "code_revision"
    )
    if not isinstance(phase4_evaluation_runner_revision,dict):
        phase4_evaluation_runner_revision={}
    phase4_evaluation_runner_protocol=phase4_evaluation_runner_payload.get(
        "protocol"
    )
    if not isinstance(phase4_evaluation_runner_protocol,dict):
        phase4_evaluation_runner_protocol={}
    phase4_evaluation_runner_source=phase4_evaluation_runner_payload.get(
        "source"
    )
    if not isinstance(phase4_evaluation_runner_source,dict):
        phase4_evaluation_runner_source={}
    phase4_evaluation_runner_checks=phase4_evaluation_runner_payload.get(
        "checks"
    )
    if not isinstance(phase4_evaluation_runner_checks,dict):
        phase4_evaluation_runner_checks={}
    phase4_evaluation_runner_claim=phase4_evaluation_runner_payload.get(
        "claim_boundary"
    )
    if not isinstance(phase4_evaluation_runner_claim,dict):
        phase4_evaluation_runner_claim={}
    phase4_evaluation_expected_hashes={
        "evaluation_boundary_sha256":(
            _sha256(phase4_evaluation_boundary_path)
            if phase4_evaluation_boundary_path.exists() else None
        ),
        "inference_module_sha256":(
            _sha256(phase4_evaluation_inference_module_path)
            if phase4_evaluation_inference_module_path.exists() else None
        ),
        "runner_sha256":(
            _sha256(phase4_evaluation_runner_script_path)
            if phase4_evaluation_runner_script_path.exists() else None
        ),
        "validator_sha256":(
            _sha256(phase4_evaluation_validator_path)
            if phase4_evaluation_validator_path.exists() else None
        ),
        "pair_auditor_sha256":(
            _sha256(phase4_evaluation_pair_auditor_path)
            if phase4_evaluation_pair_auditor_path.exists() else None
        ),
        "analyzer_sha256":(
            _sha256(phase4_evaluation_analyzer_path)
            if phase4_evaluation_analyzer_path.exists() else None
        ),
        "tests_sha256":(
            _sha256(phase4_evaluation_tests_path)
            if phase4_evaluation_tests_path.exists() else None
        ),
        "runner_tests_sha256":(
            _sha256(phase4_evaluation_runner_tests_path)
            if phase4_evaluation_runner_tests_path.exists() else None
        ),
        "doc_sha256":(
            _sha256(phase4_evaluation_doc_path)
            if phase4_evaluation_doc_path.exists() else None
        ),
        "treatment_module_sha256":(
            _sha256(phase4_treatment_module_path)
            if phase4_treatment_module_path.exists() else None
        ),
        "pilot_runtime_sha256":(
            _sha256(phase4_pilot_runner_runtime_path)
            if phase4_pilot_runner_runtime_path.exists() else None
        ),
        "treatment_audit_sha256":(
            _sha256(phase4_treatment_audit_path)
            if phase4_treatment_audit_path.exists() else None
        ),
        "pilot_runner_audit_sha256":(
            _sha256(phase4_pilot_runner_audit_path)
            if phase4_pilot_runner_audit_path.exists() else None
        ),
    }
    phase4_evaluation_before=phase4_evaluation_runner_source.get(
        "database_before"
    )
    if not isinstance(phase4_evaluation_before,dict):
        phase4_evaluation_before={}
    phase4_evaluation_after=phase4_evaluation_runner_source.get(
        "database_after"
    )
    if not isinstance(phase4_evaluation_after,dict):
        phase4_evaluation_after={}
    phase4_evaluation_runner_valid=(
        phase4_pilot_complete
        and phase4_evaluation_doc_path.exists()
        and phase4_evaluation_boundary_path.exists()
        and phase4_evaluation_inference_module_path.exists()
        and phase4_evaluation_runner_script_path.exists()
        and phase4_evaluation_validator_path.exists()
        and phase4_evaluation_pair_auditor_path.exists()
        and phase4_evaluation_analyzer_path.exists()
        and phase4_evaluation_tests_path.exists()
        and phase4_evaluation_runner_tests_path.exists()
        and phase4_evaluation_runner_audit
        and phase4_evaluation_runner_error is None
        and phase4_evaluation_runner_payload.get("schema_version")
        =="cortex_f4c_evaluation_runner_validation_v1"
        and phase4_evaluation_runner_payload.get("status")=="pass"
        and phase4_evaluation_runner_payload.get("mode")
        =="evaluation_runner_dry_run"
        and phase4_evaluation_runner_revision.get("dirty") is False
        and isinstance(
            phase4_evaluation_runner_revision.get("commit"),str
        )
        and bool(phase4_evaluation_runner_revision.get("commit"))
        and phase4_evaluation_runner_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_evaluation_runner_protocol.get("manifest_file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_evaluation_runner_protocol.get("manifest_sha256")
        ==phase4_causal_protocol.get("manifest_sha256")
        and phase4_evaluation_before==phase4_evaluation_after
        and phase4_evaluation_before.get("manifest_sha256")
        ==phase4_retrieval_before.get("manifest_sha256")
        and phase4_evaluation_before.get("item_count")
        ==phase4_retrieval_before.get("item_count")
        and phase4_evaluation_before.get("occurrence_count")
        ==phase4_retrieval_before.get("occurrence_count")
        and all(
            phase4_evaluation_runner_source.get(key)==value
            for key,value in phase4_evaluation_expected_hashes.items()
        )
        and bool(phase4_evaluation_runner_checks)
        and all(
            value is True
            for value in phase4_evaluation_runner_checks.values()
        )
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("world_mutation") is False
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("factorio_rcon_used") is False
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("fle_environment_created") is False
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("world_lease_acquired") is False
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("continuous_authority") is False
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("evaluation_seed_executed") is False
        and (
            phase4_evaluation_runner_payload.get("authority") or {}
        ).get("confirmatory_seed_executed") is False
        and phase4_evaluation_runner_claim.get(
            "heldout_specs_executed_in_validation"
        ) is False
        and phase4_evaluation_runner_claim.get(
            "pilot_outcomes_used_for_adaptation"
        ) is False
        and phase4_evaluation_runner_claim.get(
            "inference_code_frozen_before_outcomes"
        ) is True
    )

    phase4_evaluation_raw_seeds=phase4_manifest_partitions.get("evaluation")
    phase4_evaluation_seeds=(
        tuple(int(value) for value in phase4_evaluation_raw_seeds)
        if isinstance(phase4_evaluation_raw_seeds,list)
        else ()
    )
    phase4_evaluation_rows: list[dict[str,Any]]=[]
    phase4_evaluation_progress_errors: list[str]=[]
    phase4_evaluation_hard_fail_seeds: list[int]=[]
    for evaluation_index,evaluation_seed in enumerate(
        phase4_evaluation_seeds,start=1
    ):
        evaluation_dir=(
            state_root/"runs"/"f4c_evaluation"/str(evaluation_seed)
        )
        pair_path=evaluation_dir/"pair.json"
        review_path=evaluation_dir/"review.json"
        pair_exists=pair_path.exists()
        review_exists=review_path.exists()
        pair_payload: dict[str,Any]={}
        review_payload: dict[str,Any]={}
        pair_error: str | None=None
        review_error: str | None=None
        if pair_exists:
            try:
                pair_payload=_load(pair_path)
            except (OSError,json.JSONDecodeError,TypeError) as exc:
                pair_error=f"{type(exc).__name__}: {exc}"
        if review_exists:
            try:
                review_payload=_load(review_path)
            except (OSError,json.JSONDecodeError,TypeError) as exc:
                review_error=f"{type(exc).__name__}: {exc}"
        pair_sha=(
            _sha256(pair_path)
            if pair_exists and pair_error is None else None
        )
        pair_claim=pair_payload.get("claim_boundary")
        if not isinstance(pair_claim,dict):
            pair_claim={}
        pair_revision=pair_payload.get("code_revision")
        if not isinstance(pair_revision,dict):
            pair_revision={}
        pair_valid=(
            pair_exists
            and pair_error is None
            and pair_payload.get("schema_version")
            =="cortex_f4c_evaluation_pair_v1"
            and pair_payload.get("status")=="completed"
            and pair_payload.get("seed")==evaluation_seed
            and isinstance(pair_payload.get("task_id"),str)
            and pair_payload.get("live_factorio_world") is False
            and pair_payload.get("factorio_rcon_used") is False
            and pair_payload.get("fle_environment_created") is False
            and pair_payload.get("world_lease_acquired") is False
            and pair_payload.get("continuous_authority") is False
            and pair_payload.get("automatic_retry") is False
            and pair_revision.get("dirty") is False
            and pair_revision.get("commit")
            ==phase4_evaluation_runner_revision.get("commit")
            and pair_claim.get("evaluation_only") is True
            and pair_claim.get("primary_f4c_inference") is True
            and pair_claim.get("pilot_evidence_reused") is False
            and pair_claim.get("confirmatory_seed_executed") is False
        )
        review_claim=review_payload.get("claim_boundary")
        if not isinstance(review_claim,dict):
            review_claim={}
        review_status=review_payload.get("status")
        review_accepted=review_status in {"pass","technical_invalid"}
        review_valid=(
            pair_valid
            and review_exists
            and review_error is None
            and review_payload.get("schema_version")
            =="cortex_f4c_evaluation_pair_review_v1"
            and review_accepted
            and review_payload.get("seed")==evaluation_seed
            and review_payload.get("pair_artifact_sha256")==pair_sha
            and review_claim.get(
                "outcome_dependent_exclusion_forbidden"
            ) is True
            and review_claim.get("replacement_seed_forbidden") is True
            and review_claim.get("pilot_evidence_reused") is False
            and review_claim.get(
                "confirmatory_partition_untouched"
            ) is True
            and review_claim.get("primary_f4c_inference")
            is (review_status=="pass")
            and review_payload.get("valid_for_primary_inference")
            is (review_status=="pass")
        )
        if pair_error is not None:
            phase4_evaluation_progress_errors.append(
                f"{evaluation_seed}:pair_read_error:{pair_error}"
            )
        if review_error is not None:
            phase4_evaluation_progress_errors.append(
                f"{evaluation_seed}:review_read_error:{review_error}"
            )
        if pair_exists and not pair_valid:
            phase4_evaluation_progress_errors.append(
                f"{evaluation_seed}:pair_invalid"
            )
        if review_exists and review_status=="fail":
            phase4_evaluation_hard_fail_seeds.append(evaluation_seed)
            phase4_evaluation_progress_errors.append(
                f"{evaluation_seed}:review_fail"
            )
        elif review_exists and not review_valid:
            phase4_evaluation_progress_errors.append(
                f"{evaluation_seed}:review_invalid"
            )
        phase4_evaluation_rows.append({
            "position":evaluation_index,
            "seed":evaluation_seed,
            "pair_path":str(pair_path),
            "pair_exists":pair_exists,
            "pair_valid":pair_valid,
            "pair_sha256":pair_sha,
            "pair_read_error":pair_error,
            "review_path":str(review_path),
            "review_exists":review_exists,
            "review_status":review_status,
            "review_valid":review_valid,
            "valid_for_primary_inference":(
                review_status=="pass" and review_valid
            ),
            "technical_invalid":(
                review_status=="technical_invalid" and review_valid
            ),
            "delta_J":(
                review_payload.get("delta_J")
                if review_status=="pass" and review_valid
                else None
            ),
            "family":review_payload.get("family"),
            "review_read_error":review_error,
        })

    phase4_evaluation_pair_count=sum(
        1 for row in phase4_evaluation_rows if row["pair_exists"]
    )
    phase4_evaluation_reviewed_count=sum(
        1 for row in phase4_evaluation_rows if row["review_valid"]
    )
    phase4_evaluation_valid_primary_count=sum(
        1
        for row in phase4_evaluation_rows
        if row["valid_for_primary_inference"]
    )
    phase4_evaluation_technical_invalid_count=sum(
        1 for row in phase4_evaluation_rows if row["technical_invalid"]
    )
    phase4_evaluation_total=len(phase4_evaluation_rows)
    phase4_evaluation_first_gap=None
    for index,row in enumerate(phase4_evaluation_rows):
        if not row["review_valid"]:
            phase4_evaluation_first_gap=index
            break
    phase4_evaluation_out_of_order: list[int]=[]
    if phase4_evaluation_first_gap is not None:
        for row in phase4_evaluation_rows[phase4_evaluation_first_gap+1:]:
            if row["pair_exists"] or row["review_exists"]:
                phase4_evaluation_out_of_order.append(int(row["seed"]))
    if phase4_evaluation_out_of_order:
        phase4_evaluation_progress_errors.append(
            "future_evaluation_evidence_out_of_order:"
            +",".join(map(str,phase4_evaluation_out_of_order))
        )

    phase4_evaluation_awaiting_review_seed=None
    phase4_evaluation_next_seed=None
    if phase4_evaluation_first_gap is not None:
        first_gap_row=phase4_evaluation_rows[phase4_evaluation_first_gap]
        if first_gap_row["pair_exists"]:
            phase4_evaluation_awaiting_review_seed=int(
                first_gap_row["seed"]
            )
        else:
            phase4_evaluation_next_seed=int(first_gap_row["seed"])
    phase4_evaluation_complete=(
        phase4_evaluation_total==20
        and phase4_evaluation_reviewed_count==phase4_evaluation_total
        and not phase4_evaluation_hard_fail_seeds
    )
    phase4_evaluation_progress_valid=(
        phase4_evaluation_total==20
        and not phase4_evaluation_progress_errors
        and not phase4_evaluation_out_of_order
        and not phase4_evaluation_hard_fail_seeds
    )
    phase4_evaluation_seed_launch_allowed=(
        phase4_evaluation_runner_valid
        and phase4_evaluation_progress_valid
        and phase4_evaluation_awaiting_review_seed is None
        and phase4_evaluation_next_seed is not None
        and not phase4_evaluation_complete
    )
    if not phase4_evaluation_runner_valid:
        phase4_evaluation_progress_status="not_ready"
    elif not phase4_evaluation_progress_valid:
        phase4_evaluation_progress_status="invalid"
    elif phase4_evaluation_complete:
        phase4_evaluation_progress_status="complete"
    elif phase4_evaluation_awaiting_review_seed is not None:
        phase4_evaluation_progress_status="awaiting_review"
    elif phase4_evaluation_reviewed_count>0:
        phase4_evaluation_progress_status="in_progress"
    else:
        phase4_evaluation_progress_status="ready"

    phase4_inference_path=(
        state_root
        / "runs"
        / "audits"
        / "cortex_f4c_evaluation_inference.json"
    )
    phase4_inference_exists=phase4_inference_path.exists()
    phase4_inference_payload: dict[str,Any]={}
    phase4_inference_error: str | None=None
    if phase4_inference_exists:
        try:
            phase4_inference_payload=_load(phase4_inference_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase4_inference_error=f"{type(exc).__name__}: {exc}"
    phase4_inference_protocol=phase4_inference_payload.get("protocol")
    if not isinstance(phase4_inference_protocol,dict):
        phase4_inference_protocol={}
    phase4_inference_lock=phase4_inference_payload.get("semantic_lock")
    if not isinstance(phase4_inference_lock,dict):
        phase4_inference_lock={}
    phase4_inference_claim=phase4_inference_payload.get("claim_boundary")
    if not isinstance(phase4_inference_claim,dict):
        phase4_inference_claim={}
    phase4_primary_inference=phase4_inference_payload.get("primary_inference")
    if not isinstance(phase4_primary_inference,dict):
        phase4_primary_inference={}
    phase4_inference_provenance=phase4_inference_payload.get("provenance")
    if not isinstance(phase4_inference_provenance,list):
        phase4_inference_provenance=[]
    phase4_inference_provenance_valid=(
        len(phase4_inference_provenance)==phase4_evaluation_total==20
        and all(
            isinstance(prov,dict)
            and prov.get("seed")==row["seed"]
            and prov.get("pair_sha256")==row["pair_sha256"]
            and (
                (
                    state_root
                    / "runs"
                    / "f4c_evaluation"
                    / str(row["seed"])
                    / "review.json"
                ).exists()
            )
            and prov.get("review_sha256")
            ==_sha256(
                state_root
                / "runs"
                / "f4c_evaluation"
                / str(row["seed"])
                / "review.json"
            )
            and prov.get("review_status")==row["review_status"]
            for prov,row in zip(
                phase4_inference_provenance,
                phase4_evaluation_rows,
                strict=True,
            )
        )
    )
    phase4_inference_valid=(
        phase4_evaluation_complete
        and phase4_inference_exists
        and phase4_inference_error is None
        and phase4_inference_payload.get("schema_version")
        =="cortex_f4c_evaluation_inference_v1"
        and phase4_inference_payload.get("status")=="pass"
        and phase4_inference_protocol.get("protocol_id")
        =="cortex-f4c-memory-ablation-transfer-v1"
        and phase4_inference_protocol.get("manifest_file_sha256")
        ==phase4_causal_manifest_sha
        and phase4_inference_protocol.get("manifest_sha256")
        ==phase4_causal_protocol.get("manifest_sha256")
        and phase4_inference_lock.get("treatment_module_sha256")
        ==phase4_evaluation_expected_hashes["treatment_module_sha256"]
        and phase4_inference_lock.get("pilot_runtime_sha256")
        ==phase4_evaluation_expected_hashes["pilot_runtime_sha256"]
        and phase4_inference_payload.get("evaluation_pair_count")==20
        and phase4_inference_provenance_valid
        and phase4_inference_claim.get("pilot_rows_included") is False
        and phase4_inference_claim.get("confirmatory_rows_included") is False
        and phase4_inference_claim.get("outcome_dependent_exclusion") is False
        and phase4_inference_claim.get("replacement_seeds_used") is False
        and phase4_inference_claim.get("fixed_sample_stopping_rule") is True
        and phase4_primary_inference.get("valid_pair_count")
        ==phase4_evaluation_valid_primary_count
        and phase4_primary_inference.get("decision")
        in {"positive","not_positive","inconclusive"}
    )
    phase4_causal_positive=(
        phase4_inference_valid
        and phase4_primary_inference.get(
            "positive_causal_memory_result"
        ) is True
        and phase4_primary_inference.get("decision")=="positive"
    )
    phase4_exit_gate_valid=(
        phase4_memory_valid
        and phase4_retrieval_valid
        and phase4_causal_positive
    )
    phase4_any_seed_launch_allowed=(
        phase4_pilot_seed_launch_allowed
        or phase4_evaluation_seed_launch_allowed
    )

    phase5_protocol_doc_path=(
        state_root / "docs" / "CORTEX_PHASE5_AUTONOMY_PROTOCOL.md"
    )
    phase5_protocol_doc=phase5_protocol_doc_path.exists()
    phase5_manifest_path=state_root / "configs" / "cortex_f5_autonomy_v1.json"
    phase5_manifest_exists=phase5_manifest_path.exists()
    phase5_manifest: dict[str,Any]={}
    phase5_manifest_error: str | None=None
    if phase5_manifest_exists:
        try:
            phase5_manifest=_load(phase5_manifest_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase5_manifest_error=f"{type(exc).__name__}: {exc}"
    phase5_source=phase5_manifest.get("source")
    if not isinstance(phase5_source,dict):
        phase5_source={}
    phase5_authority=phase5_manifest.get("authority")
    if not isinstance(phase5_authority,dict):
        phase5_authority={}
    phase5_evolution=phase5_manifest.get("evolution")
    if not isinstance(phase5_evolution,dict):
        phase5_evolution={}
    phase5_partitions=phase5_manifest.get("seed_partitions")
    if not isinstance(phase5_partitions,dict):
        phase5_partitions={}
    phase5_derivation=phase5_partitions.get("derivation")
    if not isinstance(phase5_derivation,dict):
        phase5_derivation={}
    phase5_expected_capabilities=[
        "iron_extraction",
        "coal_self_sufficiency",
        "iron_smelting",
        "steam_power",
        "electric_mining",
        "copper_chain",
        "powered_manufacturing",
        "automation_science",
        "logistic_science",
    ]
    phase5_development=phase5_partitions.get("development")
    phase5_pilot=phase5_partitions.get("pilot")
    phase5_held_out=phase5_partitions.get("held_out")
    phase5_reserved=phase5_partitions.get("f4_confirmatory_reserved")
    phase5_partition_rows=[phase5_development,phase5_pilot,phase5_held_out]
    phase5_frozen_seeds=(
        all(isinstance(row,list) for row in phase5_partition_rows)
        and len(phase5_development)==8
        and len(phase5_pilot)==8
        and len(phase5_held_out)==12
        and all(
            isinstance(seed,int) and seed>0
            for row in phase5_partition_rows
            for seed in row
        )
    )
    phase5_all_seeds=(
        phase5_development+phase5_pilot+phase5_held_out
        if phase5_frozen_seeds
        else []
    )
    phase5_inference_sha=(
        _sha256(phase4_inference_path)
        if phase4_inference_path.exists()
        else None
    )
    phase5_protocol_id="cortex-f5-autonomous-factory-bootstrap-v1"
    phase5_expected_partitions={
        partition:[
            int.from_bytes(
                hashlib.sha256(
                    f"{phase5_protocol_id}:{partition}:{index}".encode()
                ).digest()[:4],
                "big",
            )
            %2_000_000_000
            +1
            for index in range(count)
        ]
        for partition,count in (
            ("development",8),
            ("pilot",8),
            ("held_out",12),
        )
    }
    phase5_artifacts=phase5_manifest.get("artifacts")
    if not isinstance(phase5_artifacts,dict):
        phase5_artifacts={}
    phase5_expected_artifacts={
        "cortex_f5_capability_schema_v1.json":"cortex_f5_capability_schema_v1",
        "cortex_f5_trajectory_schema_v1.json":"cortex_f5_trajectory_schema_v1",
        "cortex_f5_authority_schema_v1.json":"cortex_f5_authority_schema_v1",
        "cortex_f5_intervention_ledger_schema_v1.json":
            "cortex_f5_intervention_ledger_schema_v1",
    }
    phase5_artifact_payloads: dict[str,dict[str,Any]]={}
    phase5_artifact_errors: dict[str,str]={}
    phase5_artifact_hashes: dict[str,str | None]={}
    for artifact_name,schema_version in phase5_expected_artifacts.items():
        artifact_path=state_root/"configs"/artifact_name
        artifact_meta=phase5_artifacts.get(artifact_name)
        if not isinstance(artifact_meta,dict):
            artifact_meta={}
        try:
            payload=_load(artifact_path)
            artifact_sha=_sha256(artifact_path)
            phase5_artifact_payloads[artifact_name]=payload
            phase5_artifact_hashes[artifact_name]=artifact_sha
            if artifact_meta.get("path")!=f"configs/{artifact_name}":
                phase5_artifact_errors[artifact_name]="manifest_path_mismatch"
            elif artifact_meta.get("sha256")!=artifact_sha:
                phase5_artifact_errors[artifact_name]="manifest_sha256_mismatch"
            elif payload.get("schema_version")!=schema_version:
                phase5_artifact_errors[artifact_name]="schema_version_mismatch"
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase5_artifact_errors[artifact_name]=f"{type(exc).__name__}: {exc}"
            phase5_artifact_hashes[artifact_name]=None
    phase5_capability_schema=phase5_artifact_payloads.get(
        "cortex_f5_capability_schema_v1.json",
        {},
    )
    phase5_trajectory_schema=phase5_artifact_payloads.get(
        "cortex_f5_trajectory_schema_v1.json",
        {},
    )
    phase5_authority_schema=phase5_artifact_payloads.get(
        "cortex_f5_authority_schema_v1.json",
        {},
    )
    phase5_intervention_schema=phase5_artifact_payloads.get(
        "cortex_f5_intervention_ledger_schema_v1.json",
        {},
    )
    phase5_required_trajectory_fields=[
        "state",
        "candidate_options",
        "memory_retrieval",
        "selected_option",
        "expected_effect",
        "authority_level",
        "execution_trace",
        "postconditions",
        "capability_delta",
        "resource_cost",
        "rollback",
        "reward_components",
        "next_state",
    ]
    phase5_capability_rows=phase5_capability_schema.get("capabilities")
    if not isinstance(phase5_capability_rows,dict):
        phase5_capability_rows={}
    phase5_authority_levels_schema=phase5_authority_schema.get("levels")
    if not isinstance(phase5_authority_levels_schema,dict):
        phase5_authority_levels_schema={}
    phase5_intervention_meta=phase5_manifest.get("intervention_ledger")
    if not isinstance(phase5_intervention_meta,dict):
        phase5_intervention_meta={}
    phase5_intervention_ledger_path=(
        state_root/"runs"/"cortex_f5_intervention_ledger.json"
    )
    phase5_intervention_ledger: dict[str,Any]={}
    phase5_intervention_error: str | None=None
    try:
        phase5_intervention_ledger=_load(phase5_intervention_ledger_path)
    except (OSError,json.JSONDecodeError,TypeError) as exc:
        phase5_intervention_error=f"{type(exc).__name__}: {exc}"
    phase5_interventions=phase5_intervention_ledger.get("interventions")
    if not isinstance(phase5_interventions,list):
        phase5_interventions=[]
    phase5_physical_gate=phase5_manifest.get("physical_success_gate")
    if not isinstance(phase5_physical_gate,dict):
        phase5_physical_gate={}
    phase5_exit_gate=phase5_manifest.get("f5_exit_gate")
    if not isinstance(phase5_exit_gate,dict):
        phase5_exit_gate={}
    phase5_authority_levels=phase5_authority.get("levels")
    if not isinstance(phase5_authority_levels,dict):
        phase5_authority_levels={}
    phase5_protocol_valid=(
        phase4_exit_gate_valid
        and phase5_protocol_doc
        and phase5_manifest_exists
        and phase5_manifest_error is None
        and phase5_manifest.get("schema_version")
        =="cortex_f5_autonomy_protocol_v1"
        and phase5_manifest.get("protocol_id")
        =="cortex-f5-autonomous-factory-bootstrap-v1"
        and phase5_manifest.get("status")=="frozen"
        and phase5_manifest.get("phase")=="F5"
        and phase5_manifest.get("checkpoint")=="F5-A"
        and phase5_source.get("phase4_checkpoint")=="F4-C"
        and phase5_source.get("phase4_inference_sha256")==phase5_inference_sha
        and phase5_source.get("required_phase4_decision")=="positive"
        and phase5_authority.get("initial_level")=="A0"
        and phase5_authority.get("continuous_authority") is False
        and phase5_authority.get("world_mutation_authorized") is False
        and phase5_evolution.get("legacy_evolution_loop")=="off"
        and phase5_evolution.get("continuous_evolution")=="off"
        and phase5_manifest.get("capabilities")==phase5_expected_capabilities
        and phase5_frozen_seeds
        and len(set(phase5_all_seeds))==28
        and isinstance(phase5_reserved,list)
        and phase5_reserved==phase4_expected_confirmatory
        and not (set(phase5_all_seeds) & set(phase5_reserved))
        and phase5_derivation.get("namespace")
        =="cortex-f5-autonomous-factory-bootstrap-v1"
        and phase5_derivation.get("input")
        =="<protocol_id>:<partition>:<zero_based_index>"
        and phase5_development==phase5_expected_partitions["development"]
        and phase5_pilot==phase5_expected_partitions["pilot"]
        and phase5_held_out==phase5_expected_partitions["held_out"]
        and phase5_authority_levels.get("A0")=="observe_only"
        and phase5_authority_levels.get("A6")
        =="continuous_authority_forbidden_in_f5"
        and phase5_evolution.get(
            "policy_learning_may_rank_options_but_never_grant_authority"
        ) is True
        and not phase5_artifact_errors
        and set(phase5_capability_rows)==set(phase5_expected_capabilities)
        and all(
            isinstance(row,dict)
            and row.get("entity_existence_alone_is_sufficient") is False
            and bool(row.get("hard_postconditions"))
            for row in phase5_capability_rows.values()
        )
        and phase5_trajectory_schema.get("required_fields")
        ==phase5_required_trajectory_fields
        and phase5_trajectory_schema.get("training_runtime_decoupled") is True
        and phase5_trajectory_schema.get(
            "authority_is_observation_not_policy_output"
        ) is True
        and phase5_authority_schema.get("continuous_authority_allowed") is False
        and phase5_authority_schema.get("policy_may_self_grant_authority") is False
        and phase5_intervention_schema.get("ledger_schema_version")
        =="cortex_f5_intervention_ledger_v1"
        and phase5_intervention_schema.get("protocol_id")==phase5_protocol_id
        and phase5_intervention_schema.get("event_required_fields")
        ==[
            "at",
            "run_id",
            "kind",
            "actor",
            "reason",
            "authority_level",
            "human_intervention",
            "external_resource_injection",
            "authority_override",
        ]
        and phase5_authority_levels_schema.get("A0",{}).get("max_executions")==0
        and phase5_authority_levels_schema.get("A2",{}).get("max_executions")==1
        and phase5_authority_levels_schema.get("A6",{}).get("allowed") is False
        and phase5_intervention_error is None
        and phase5_intervention_meta.get("path")
        =="runs/cortex_f5_intervention_ledger.json"
        and phase5_intervention_meta.get("schema_version")
        =="cortex_f5_intervention_ledger_v1"
        and phase5_intervention_meta.get("schema_path")
        =="configs/cortex_f5_intervention_ledger_schema_v1.json"
        and phase5_intervention_meta.get("initializer_path")
        =="scripts/init_cortex_f5_state.py"
        and phase5_intervention_ledger.get("schema_version")
        =="cortex_f5_intervention_ledger_v1"
        and phase5_intervention_ledger.get("protocol_id")==phase5_protocol_id
        and len(phase5_interventions)
        ==phase5_intervention_meta.get("required_initial_intervention_count")
        ==0
        and all(
            phase5_physical_gate.get(key) is True
            for key in (
                "logistic_science_functional",
                "all_promoted_capabilities_alive",
                "zero_human_intervention",
                "no_external_fuel_or_material_injection",
                "sustainability_soak_required",
            )
        )
        and phase5_exit_gate.get("held_out_complete_runs_min")==10
        and phase5_exit_gate.get("held_out_total")==12
        and phase5_exit_gate.get("authority_violations_max")==0
        and phase5_exit_gate.get("paired_policy_vs_baseline_preregistered") is True
        and phase5_exit_gate.get("alpha")==0.05
        and phase5_exit_gate.get(
            "lower_confidence_bound_must_be_positive"
        ) is True
        and phase5_exit_gate.get("sesoi_preregistered") is True
    )

    phase5b_doc_path=state_root/"docs"/"CORTEX_PHASE5_BOUNDED_AUTHORITY.md"
    phase5b_doc=phase5b_doc_path.exists()
    phase5b_audit_path=(
        state_root/"runs"/"audits"/"cortex_f5b_authority_bridge.json"
    )
    phase5b_audit_exists=phase5b_audit_path.exists()
    phase5b_audit: dict[str,Any]={}
    phase5b_audit_error: str | None=None
    if phase5b_audit_exists:
        try:
            phase5b_audit=_load(phase5b_audit_path)
        except (OSError,json.JSONDecodeError,TypeError) as exc:
            phase5b_audit_error=f"{type(exc).__name__}: {exc}"
    phase5b_authority=phase5b_audit.get("authority")
    if not isinstance(phase5b_authority,dict):
        phase5b_authority={}
    phase5b_revision=phase5b_audit.get("code_revision")
    if not isinstance(phase5b_revision,dict):
        phase5b_revision={}
    phase5b_checks=phase5b_audit.get("checks")
    if not isinstance(phase5b_checks,dict):
        phase5b_checks={}
    phase5b_source_hashes=phase5b_audit.get("source_hashes")
    if not isinstance(phase5b_source_hashes,dict):
        phase5b_source_hashes={}
    phase5b_source_paths={
        "f5_authority":state_root/"src"/"factorio_ai_lab"/"cortex"/"f5_authority.py",
        "grant_ledger":state_root/"src"/"factorio_ai_lab"/"cortex"/"grant_ledger.py",
        "option_execute":state_root/"src"/"factorio_ai_lab"/"cortex"/"option_execute.py",
        "runtime_lease":state_root/"src"/"factorio_ai_lab"/"runtime.py",
        "authority_schema":(
            state_root/"configs"/"cortex_f5_authority_schema_v1.json"
        ),
        "audit_script":state_root/"scripts"/"validate_cortex_f5b_authority_bridge.py",
    }
    phase5b_current_hashes={
        key:(_sha256(path) if path.exists() else None)
        for key,path in phase5b_source_paths.items()
    }
    phase5b_commit=str(phase5b_revision.get("commit") or "")
    phase5b_relative_paths={
        key:str(path.relative_to(state_root))
        for key,path in phase5b_source_paths.items()
    }
    phase5b_commit_hashes={
        key:_git_blob_sha256(state_root,phase5b_commit,relative)
        for key,relative in phase5b_relative_paths.items()
    }
    phase5b_commit_hashes_resolved=(
        bool(phase5b_commit_hashes)
        and all(value is not None for value in phase5b_commit_hashes.values())
    )
    phase5b_source_hashes_match_commit=(
        phase5b_commit_hashes_resolved
        and phase5b_source_hashes==phase5b_commit_hashes
    )
    phase5b_source_hashes_match_current=(
        bool(phase5b_source_hashes)
        and phase5b_source_hashes==phase5b_current_hashes
    )
    phase5b_source_binding_valid=(
        phase5b_source_hashes_match_commit
        or (
            not phase5b_commit_hashes_resolved
            and phase5b_source_hashes_match_current
        )
    )
    phase5b_valid=(
        phase5_protocol_valid
        and phase5b_doc
        and phase5b_audit_exists
        and phase5b_audit_error is None
        and phase5b_audit.get("schema_version")
        =="cortex_f5b_authority_bridge_audit_v1"
        and phase5b_audit.get("status")=="pass"
        and phase5b_audit.get("checkpoint")=="F5-B"
        and phase5b_audit.get("world_mutation") is False
        and phase5b_audit.get("world_lease_acquired") is False
        and phase5b_audit.get("grant_issued") is False
        and phase5b_audit.get("option_executed_live") is False
        and phase5b_audit.get("live_canary_required_for_f5b") is False
        and phase5b_audit.get("next_checkpoint")=="F5-C"
        and phase5b_authority.get("ambient_level")=="A0"
        and phase5b_authority.get("bounded_grant_level")=="A2"
        and phase5b_authority.get("max_executions")==1
        and phase5b_authority.get("continuous_authority") is False
        and phase5b_authority.get("policy_may_self_grant_authority") is False
        and phase5b_revision.get("dirty") is False
        and isinstance(phase5b_revision.get("commit"),str)
        and bool(phase5b_revision.get("commit"))
        and bool(phase5b_checks)
        and all(value is True for value in phase5b_checks.values())
        and phase5b_source_binding_valid
        and len(phase5_interventions)==0
    )

    phase5c_attempts: list[dict[str,Any]]=[]
    phase5c_started=False
    phase5c_iron_extraction_valid=False
    phase5c_selected_seed: int | None=None
    phase5c_selected_path=(
        state_root/"runs"/"audits"/"cortex_f5c_development_missing.json"
    )
    phase5c_selected_artifact: dict[str,Any]={}
    phase5c_selected_error: str | None=None

    def f5c_artifact_path(seed: int) -> Path:
        return (
            state_root/"runs"/"audits"/
            f"cortex_f5c_development_{seed}_iron_extraction.json"
        )

    def f5c_artifact_valid(
        payload: dict[str,Any],
        *,
        seed: int,
    ) -> bool:
        preflight=payload.get("preflight")
        if not isinstance(preflight,dict):
            preflight={}
        revision=payload.get("code_revision")
        if not isinstance(revision,dict):
            revision={}
        gate=payload.get("capability_gate")
        if not isinstance(gate,dict):
            gate={}
        survival=payload.get("survival_gate")
        if not isinstance(survival,dict):
            survival={}
        trajectory=payload.get("trajectory")
        if not isinstance(trajectory,dict):
            trajectory={}
        delta=trajectory.get("capability_delta")
        if not isinstance(delta,dict):
            delta={}
        return (
            phase5b_valid
            and payload.get("schema_version")
            =="cortex_f5c_deterministic_baseline_v1"
            and payload.get("partition")=="development"
            and payload.get("seed")==seed
            and payload.get("status")=="completed"
            and payload.get("capability")=="iron_extraction"
            and payload.get("ambient_authority")=="A0"
            and payload.get("bounded_authority")=="A2"
            and payload.get("continuous_authority") is False
            and payload.get("automatic_retry") is False
            and payload.get("option_execution_attempts")==1
            and payload.get("external_resource_injection") is False
            and payload.get("human_intervention_count")==0
            and payload.get("transaction_committed") is True
            and payload.get("capability_promoted")=="iron_extraction"
            and revision.get("dirty") is False
            and preflight.get("world_mutation") is False
            and preflight.get("grant_issued") is False
            and preflight.get("option_executed_live") is False
            and bool(gate)
            and all(value is True for value in gate.values())
            and survival.get("passed") is True
            and survival.get("regressed")==[]
            and "iron_extraction" in (
                delta.get("promoted")
                if isinstance(delta.get("promoted"),list)
                else []
            )
            and len(phase5_interventions)==0
        )

    if isinstance(phase5_development,list):
        for raw_seed in phase5_development:
            if not isinstance(raw_seed,int) or isinstance(raw_seed,bool):
                continue
            seed=int(raw_seed)
            path=f5c_artifact_path(seed)
            if not path.exists():
                continue
            payload: dict[str,Any]={}
            error: str | None=None
            try:
                payload=_load(path)
            except (OSError,json.JSONDecodeError,TypeError) as exc:
                error=f"{type(exc).__name__}: {exc}"
            structurally_started=(
                error is None
                and payload.get("schema_version")
                =="cortex_f5c_deterministic_baseline_v1"
                and payload.get("partition")=="development"
                and payload.get("seed")==seed
            )
            valid=(
                structurally_started
                and f5c_artifact_valid(payload,seed=seed)
            )
            phase5c_attempts.append({
                "seed":seed,
                "artifact_path":str(path),
                "status":payload.get("status"),
                "code_commit":(
                    payload.get("code_revision",{}).get("commit")
                    if isinstance(payload.get("code_revision"),dict)
                    else None
                ),
                "started":structurally_started,
                "iron_extraction_validated":valid,
                "read_error":error,
            })
            if structurally_started:
                phase5c_started=True
                phase5c_selected_seed=seed
                phase5c_selected_path=path
                phase5c_selected_artifact=payload
                phase5c_selected_error=error
            if valid and not phase5c_iron_extraction_valid:
                phase5c_iron_extraction_valid=True
                phase5c_selected_seed=seed
                phase5c_selected_path=path
                phase5c_selected_artifact=payload
                phase5c_selected_error=error

    if phase5c_selected_seed is None:
        phase5c_selected_seed=(
            int(phase5_development[0])
            if isinstance(phase5_development,list)
            and phase5_development
            and isinstance(phase5_development[0],int)
            and not isinstance(phase5_development[0],bool)
            else None
        )
        if phase5c_selected_seed is not None:
            phase5c_selected_path=f5c_artifact_path(phase5c_selected_seed)

    phase5c_artifact_path=phase5c_selected_path
    phase5c_artifact_exists=phase5c_artifact_path.exists()
    phase5c_artifact=phase5c_selected_artifact
    phase5c_artifact_error=phase5c_selected_error
    phase5c_preflight=phase5c_artifact.get("preflight")
    if not isinstance(phase5c_preflight,dict):
        phase5c_preflight={}
    phase5c_revision=phase5c_artifact.get("code_revision")
    if not isinstance(phase5c_revision,dict):
        phase5c_revision={}
    phase5c_gate=phase5c_artifact.get("capability_gate")
    if not isinstance(phase5c_gate,dict):
        phase5c_gate={}
    phase5c_survival=phase5c_artifact.get("survival_gate")
    if not isinstance(phase5c_survival,dict):
        phase5c_survival={}
    phase5c_trajectory=phase5c_artifact.get("trajectory")
    if not isinstance(phase5c_trajectory,dict):
        phase5c_trajectory={}
    phase5c_delta=phase5c_trajectory.get("capability_delta")
    if not isinstance(phase5c_delta,dict):
        phase5c_delta={}

    phase5c_coal_attempts: list[dict[str,Any]]=[]
    phase5c_coal_valid=False
    phase5c_coal_artifact_path: Path | None=None
    phase5c_coal_artifact: dict[str,Any]={}
    phase5c_coal_artifact_error: str | None=None
    if phase5c_iron_extraction_valid and phase5c_selected_seed is not None:
        coal_pattern=(
            f"cortex_f5c_continuation_{phase5c_selected_seed}_"
            "coal_self_sufficiency_*.json"
        )
        coal_paths=sorted(
            (state_root/"runs"/"audits").glob(coal_pattern)
        )
        coal_candidates: list[
            tuple[str,Path,dict[str,Any],str | None,bool]
        ]=[]
        for coal_path in coal_paths:
            payload: dict[str,Any]={}
            error: str | None=None
            try:
                payload=_load(coal_path)
            except (OSError,json.JSONDecodeError,TypeError) as exc:
                error=f"{type(exc).__name__}: {exc}"
            preflight=payload.get("preflight")
            if not isinstance(preflight,dict):
                preflight={}
            preflight_phase=preflight.get("phase_state")
            if not isinstance(preflight_phase,dict):
                preflight_phase={}
            revision=payload.get("code_revision")
            if not isinstance(revision,dict):
                revision={}
            gate=payload.get("capability_gate")
            if not isinstance(gate,dict):
                gate={}
            survival=payload.get("survival_gate")
            if not isinstance(survival,dict):
                survival={}
            final=payload.get("measurement_final")
            if not isinstance(final,dict):
                final={}
            trajectory=payload.get("trajectory")
            if not isinstance(trajectory,dict):
                trajectory={}
            delta=trajectory.get("capability_delta")
            if not isinstance(delta,dict):
                delta={}
            started=(
                error is None
                and payload.get("schema_version")
                =="cortex_f5c_coal_self_sufficiency_v1"
                and payload.get("base_seed")==phase5c_selected_seed
                and payload.get("capability")=="coal_self_sufficiency"
            )
            valid=(
                started
                and payload.get("status")=="completed"
                and payload.get("ambient_authority")=="A0"
                and payload.get("bounded_authority")=="A2"
                and payload.get("continuous_authority") is False
                and payload.get("automatic_retry") is False
                and payload.get("option_execution_attempts")==1
                and payload.get("world_reset") is False
                and payload.get("external_resource_injection") is False
                and payload.get("human_intervention_count")==0
                and payload.get("transaction_committed") is True
                and payload.get("capability_promoted")
                =="coal_self_sufficiency"
                and revision.get("dirty") is False
                and isinstance(revision.get("commit"),str)
                and bool(revision.get("commit"))
                and preflight.get("world_mutation") is False
                and preflight.get("grant_issued") is False
                and preflight.get("option_executed_live") is False
                and preflight.get("world_reset") is False
                and preflight_phase.get("achieved_capabilities")
                ==["iron_extraction"]
                and bool(gate)
                and all(value is True for value in gate.values())
                and survival.get("previously_promoted")
                ==["iron_extraction"]
                and survival.get("iron_extraction_survives") is True
                and survival.get("regressed")==[]
                and survival.get("passed") is True
                and final.get("coal_mined") is True
                and final.get(
                    "endogenous_coal_reaches_fuel_consumer"
                ) is True
                and final.get("external_bootstrap_fuel_retired") is True
                and isinstance(
                    final.get("incumbent_iron_bootstrap_removed"),
                    (int,float),
                )
                and not isinstance(
                    final.get("incumbent_iron_bootstrap_removed"),
                    bool,
                )
                and float(
                    final.get("incumbent_iron_bootstrap_removed")
                )>0
                and final.get("incumbent_iron_bootstrap_remaining")==0
                and isinstance(final.get("coal_endogenous_growth"),(int,float))
                and not isinstance(final.get("coal_endogenous_growth"),bool)
                and float(final.get("coal_endogenous_growth"))>0
                and isinstance(
                    final.get("incumbent_iron_buffer_growth"),
                    (int,float),
                )
                and not isinstance(
                    final.get("incumbent_iron_buffer_growth"),
                    bool,
                )
                and float(final.get("incumbent_iron_buffer_growth"))>0
                and "coal_self_sufficiency" in (
                    delta.get("promoted")
                    if isinstance(delta.get("promoted"),list)
                    else []
                )
                and delta.get("regressed")==[]
                and len(phase5_interventions)==0
            )
            started_at=str(payload.get("started_at") or "")
            coal_candidates.append(
                (started_at,coal_path,payload,error,valid)
            )
        coal_candidates.sort(key=lambda row:(row[0],str(row[1])))
        for started_at,coal_path,payload,error,valid in coal_candidates:
            phase5c_coal_attempts.append({
                "artifact_path":str(coal_path),
                "status":payload.get("status"),
                "started_at":started_at or None,
                "code_commit":(
                    payload.get("code_revision",{}).get("commit")
                    if isinstance(payload.get("code_revision"),dict)
                    else None
                ),
                "validated":valid,
                "read_error":error,
            })
            if valid and not phase5c_coal_valid:
                phase5c_coal_valid=True
                phase5c_coal_artifact_path=coal_path
                phase5c_coal_artifact=payload
                phase5c_coal_artifact_error=error
            elif phase5c_coal_artifact_path is None:
                phase5c_coal_artifact_path=coal_path
                phase5c_coal_artifact=payload
                phase5c_coal_artifact_error=error

    phase5_achieved_capabilities=(
        ["iron_extraction","coal_self_sufficiency"]
        if phase5c_coal_valid
        else (
            ["iron_extraction"]
            if phase5c_iron_extraction_valid
            else []
        )
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

    if phase5c_coal_valid:
        action=(
            "F5-C coal_self_sufficiency capability promoted with incumbent "
            "iron_extraction surviving and bootstrap fuel retired; continue "
            "deterministic baseline with iron_smelting under ambient A0 and "
            "exactly one expiring A2 grant per Option"
        )
    elif phase5c_iron_extraction_valid:
        action=(
            "F5-C iron_extraction capability promoted from physical evidence; "
            "continue deterministic baseline with coal_self_sufficiency under "
            "ambient A0 and exactly one expiring A2 grant per Option"
        )
    elif phase5c_started:
        action=(
            "F5-C development attempt recorded without capability promotion; "
            "inspect the canonical counterexample and do not retry automatically"
        )
    elif phase5b_valid:
        action=(
            "F5-B bounded authority bridge PASS; implement F5-C deterministic "
            "autonomous baseline with ambient authority A0 and exactly one "
            "expiring A2 grant per transactional Option"
        )
    elif phase5_protocol_valid:
        action=(
            "F5-A protocol freeze complete; validate bounded authority bridge "
            "(F5-B) in test/shadow with WORLD observe-only, evolution OFF, and "
            "no continuous authority"
        )
    elif phase4_execution_ready:
        if not phase4_pilot_progress_valid:
            action=(
                "halt F4-C pilot progression: pilot evidence ledger is invalid "
                "or out of order; inspect canonical pair/review artifacts before "
                "any later seed"
            )
        elif phase4_pilot_awaiting_review_seed is not None:
            action=(
                "audit and PASS-review pilot seed "
                f"{phase4_pilot_awaiting_review_seed} before any later pilot; "
                "evaluation and confirmatory remain frozen"
            )
        elif not phase4_pilot_complete and phase4_pilot_next_seed is not None:
            action=(
                "F4-C pilot progress "
                f"{phase4_pilot_reviewed_count}/{phase4_pilot_total} reviewed; "
                "next controlled action is exactly one preregistered pilot pair "
                f"seed {phase4_pilot_next_seed}; evaluation and confirmatory "
                "remain frozen"
            )
        elif phase4_pilot_complete and not phase4_evaluation_runner_valid:
            action=(
                "F4-C pilot instrumentation complete 8/8 with PASS reviews; "
                "freeze pilot outcomes against adaptation and implement/validate "
                "the held-out evaluation runner before any evaluation seed"
            )
        elif not phase4_evaluation_progress_valid:
            action=(
                "halt F4-C held-out evaluation: canonical evaluation evidence "
                "is invalid, failed integrity review, or is out of order"
            )
        elif phase4_evaluation_awaiting_review_seed is not None:
            action=(
                "audit held-out evaluation seed "
                f"{phase4_evaluation_awaiting_review_seed} before any later "
                "evaluation seed; no replacement seed is allowed"
            )
        elif (
            not phase4_evaluation_complete
            and phase4_evaluation_next_seed is not None
        ):
            action=(
                "F4-C held-out evaluation progress "
                f"{phase4_evaluation_reviewed_count}/"
                f"{phase4_evaluation_total} reviewed; next controlled action "
                "is exactly evaluation seed "
                f"{phase4_evaluation_next_seed}; confirmatory remains frozen"
            )
        elif phase4_evaluation_complete and not phase4_inference_valid:
            action=(
                "F4-C held-out evaluation 20/20 reviewed; compute the single "
                "preregistered exact primary inference artifact without "
                "executing confirmatory seeds"
            )
        elif phase4_inference_valid:
            action=(
                "F4-C held-out inference complete with decision "
                f"{phase4_primary_inference.get('decision')}; confirmatory seeds "
                "remain frozen and no further experimental seed is authorized"
            )
        else:
            action=(
                "F4-C execution state has no authorized next action; halt "
                "before experimental execution"
            )
    elif phase4_treatment_valid:
        action=(
            "F4-C treatment semantics PASS but pilot runner readiness is not "
            "validated; implement and validate the protocol runner without "
            "executing any pilot/evaluation/confirmatory seed"
        )
    elif phase4_real_task_adapters_valid:
        action=(
            "F4-C real adapters PASS but causal treatment semantics are not "
            "validated; prove retrieval enters ranking/selection with the "
            "identical frozen candidate surface before any pilot seed"
        )
    elif phase4_harness_preflight_valid:
        action=(
            "F4-C paired harness synthetic preflight PASS; implement and "
            "validate real family adapters in a disposable non-protocol world; "
            "do not launch any pilot seed yet"
        )
    elif phase4_causal_protocol_valid:
        action=(
            "F4-C causal protocol frozen and eligible; implement and validate "
            "the paired evaluation harness before launching any pilot seed"
        )
    elif phase4_retrieval_valid:
        action=(
            "F4-B complete in SHADOW; F4-C requires a frozen, diverse "
            "non-confirmatory held-out memory-ablation transfer protocol"
        )
    elif phase4_memory_valid:
        action=(
            "F4-A active in SHADOW; implement hybrid retrieval, "
            "consolidation, and decay"
        )
    elif phase3_comparison_valid:
        action="F3 complete; F4 ready but not started"
    elif phase3_credit_valid:
        action=(
            "F3-B active in SHADOW; implement paired shadow comparison "
            "against the legacy runner"
        )
    elif phase3_executive_valid:
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

    if phase5_protocol_valid:
        phase_name="F5"
        phase_status="active"
    elif phase4_retrieval_valid or phase4_memory_valid:
        phase_name="F4"
        phase_status="complete" if phase4_exit_gate_valid else "active"
    elif phase3_comparison_valid:
        phase_name="F3"
        phase_status="complete"
    elif phase3_credit_valid or phase3_executive_valid:
        phase_name="F3"
        phase_status="active"
    elif exploratory_complete and statistical_report_exists and phase2_started:
        phase_name="F2"
        phase_status="complete" if phase2_exit_gate_valid else "active"
    elif exploratory_complete and statistical_report_exists:
        phase_name="F1"
        phase_status="complete"
    else:
        phase_name="F1-B"
        phase_status="active"

    return {
        "schema_version":"cortex_phase_state_v1",
        "generated_at":datetime.now(UTC).isoformat(),
        "phase":phase_name,
        "phase_status":phase_status,
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
        "phase4_checkpoint":(
            "F4-C"
            if phase4_exit_gate_valid
            else (
                "F4-B"
                if phase4_retrieval_valid
                else ("F4-A" if phase4_memory_valid else None)
            )
        ),
        "phase5_checkpoint":(
            "F5-C"
            if phase5c_started
            else (
                "F5-B"
                if phase5b_valid
                else ("F5-A" if phase5_protocol_valid else None)
            )
        ),
        "phase5_next_checkpoint":(
            "F5-C"
            if phase5b_valid
            else ("F5-B" if phase5_protocol_valid else None)
        ),
        "phase5_deterministic_baseline":{
            "artifact_path":str(phase5c_artifact_path),
            "artifact_exists":phase5c_artifact_exists,
            "attempts":phase5c_attempts,
            "attempt_count":len(phase5c_attempts),
            "started":phase5c_started,
            "iron_extraction_validated":phase5c_iron_extraction_valid,
            "seed":phase5c_artifact.get("seed",phase5c_selected_seed),
            "partition":phase5c_artifact.get("partition"),
            "status":phase5c_artifact.get("status"),
            "capability":phase5c_artifact.get("capability"),
            "capability_promoted":phase5c_artifact.get("capability_promoted"),
            "capability_gate":phase5c_gate,
            "survival_gate":phase5c_survival,
            "ambient_authority":phase5c_artifact.get("ambient_authority","A0"),
            "bounded_authority":phase5c_artifact.get("bounded_authority"),
            "option_execution_attempts":phase5c_artifact.get(
                "option_execution_attempts"
            ),
            "transaction_committed":phase5c_artifact.get(
                "transaction_committed"
            ),
            "coal_self_sufficiency":{
                "validated":phase5c_coal_valid,
                "artifact_path":(
                    None
                    if phase5c_coal_artifact_path is None
                    else str(phase5c_coal_artifact_path)
                ),
                "attempt_count":len(phase5c_coal_attempts),
                "attempts":phase5c_coal_attempts,
                "status":phase5c_coal_artifact.get("status"),
                "capability_promoted":phase5c_coal_artifact.get(
                    "capability_promoted"
                ),
                "capability_gate":phase5c_coal_artifact.get(
                    "capability_gate"
                ),
                "survival_gate":phase5c_coal_artifact.get(
                    "survival_gate"
                ),
                "measurement_final":phase5c_coal_artifact.get(
                    "measurement_final"
                ),
                "read_error":phase5c_coal_artifact_error,
            },
            "external_resource_injection":phase5c_artifact.get(
                "external_resource_injection"
            ),
            "human_intervention_count":phase5c_artifact.get(
                "human_intervention_count"
            ),
            "read_error":phase5c_artifact_error,
        },
        "phase5_authority_bridge":{
            "document_path":str(phase5b_doc_path),
            "document_exists":phase5b_doc,
            "audit_path":str(phase5b_audit_path),
            "audit_exists":phase5b_audit_exists,
            "validated":phase5b_valid,
            "ambient_authority":phase5b_authority.get("ambient_level","A0"),
            "max_bounded_grant":phase5b_authority.get("bounded_grant_level"),
            "max_executions":phase5b_authority.get("max_executions"),
            "continuous_authority":phase5b_authority.get("continuous_authority"),
            "policy_may_self_grant_authority":phase5b_authority.get(
                "policy_may_self_grant_authority"
            ),
            "world_mutation":phase5b_audit.get("world_mutation"),
            "world_lease_acquired":phase5b_audit.get("world_lease_acquired"),
            "grant_issued":phase5b_audit.get("grant_issued"),
            "option_executed_live":phase5b_audit.get("option_executed_live"),
            "code_commit":phase5b_revision.get("commit"),
            "source_hashes_match":phase5b_source_binding_valid,
            "source_hashes_match_commit":phase5b_source_hashes_match_commit,
            "source_hashes_match_current":phase5b_source_hashes_match_current,
            "audit_commit":phase5b_commit or None,
            "checks":phase5b_checks,
            "read_error":phase5b_audit_error,
        },
        "phase5_protocol":{
            "document_path":str(phase5_protocol_doc_path),
            "document_exists":phase5_protocol_doc,
            "manifest_path":str(phase5_manifest_path),
            "manifest_exists":phase5_manifest_exists,
            "manifest_sha256":(
                _sha256(phase5_manifest_path)
                if phase5_manifest_exists
                else None
            ),
            "validated":phase5_protocol_valid,
            "protocol_id":phase5_manifest.get("protocol_id"),
            "status":phase5_manifest.get("status"),
            "authority_level":phase5_authority.get("initial_level"),
            "continuous_authority":phase5_authority.get("continuous_authority"),
            "world_mutation_authorized":phase5_authority.get(
                "world_mutation_authorized"
            ),
            "legacy_evolution_loop":phase5_evolution.get(
                "legacy_evolution_loop"
            ),
            "continuous_evolution":phase5_evolution.get(
                "continuous_evolution"
            ),
            "capability_total":len(phase5_expected_capabilities),
            "capabilities":phase5_expected_capabilities,
            "achieved_capabilities":phase5_achieved_capabilities,
            "seed_partitions":{
                "development":phase5_development,
                "pilot":phase5_pilot,
                "held_out":phase5_held_out,
                "f4_confirmatory_reserved":phase5_reserved,
            },
            "source_f4_inference_sha256":phase5_source.get(
                "phase4_inference_sha256"
            ),
            "artifacts":{
                "hashes":phase5_artifact_hashes,
                "errors":phase5_artifact_errors,
            },
            "intervention_ledger":{
                "path":str(phase5_intervention_ledger_path),
                "count":len(phase5_interventions),
                "read_error":phase5_intervention_error,
            },
            "read_error":phase5_manifest_error,
        },
        "phase4_next_checkpoint":(
            None
            if phase4_exit_gate_valid
            else (
                "F4-C"
                if phase4_retrieval_valid
                else ("F4-B" if phase4_memory_valid else "F4-A")
            )
        ),
        "phase4_causal_protocol":{
            "document_path":str(phase4_causal_doc_path),
            "document_exists":phase4_causal_doc,
            "manifest_path":str(phase4_causal_manifest_path),
            "manifest_exists":phase4_causal_manifest,
            "manifest_file_sha256":phase4_causal_manifest_sha,
            "audit_path":str(phase4_causal_audit_path),
            "audit_exists":phase4_causal_audit,
            "validated":phase4_causal_protocol_valid,
            "eligible":phase4_causal_protocol_valid,
            "harness_preflight_validated":phase4_harness_preflight_valid,
            "real_task_adapters_validated":phase4_real_task_adapters_valid,
            "execution_ready":phase4_execution_ready,
            "status":phase4_causal_payload.get("status"),
            "protocol_id":phase4_causal_protocol.get("protocol_id"),
            "manifest_sha256":phase4_causal_protocol.get("manifest_sha256"),
            "code_commit":phase4_causal_revision.get("commit"),
            "evaluation_pair_count":phase4_causal_protocol.get(
                "evaluation_pair_count"
            ),
            "pilot_pair_count":phase4_causal_protocol.get("pilot_pair_count"),
            "task_family_count":phase4_causal_protocol.get("task_family_count"),
            "counterbalancing":{
                "memory_on_first":phase4_causal_protocol.get("memory_on_first"),
                "memory_ablated_first":phase4_causal_protocol.get(
                    "memory_ablated_first"
                ),
            },
            "confirmatory_reserved":phase4_causal_protocol.get(
                "confirmatory_reserved"
            ),
            "source_memory_manifest_sha256":phase4_causal_source.get(
                "memory_manifest_sha256"
            ),
            "checks":phase4_causal_checks,
            "manifest_read_error":phase4_causal_manifest_error,
            "audit_read_error":phase4_causal_error,
        },
        "phase4_causal_harness":{
            "document_path":str(phase4_harness_doc_path),
            "audit_path":str(phase4_harness_audit_path),
            "audit_exists":phase4_harness_audit,
            "preflight_validated":phase4_harness_preflight_valid,
            "mode":phase4_harness_payload.get("mode"),
            "execution_ready":phase4_execution_ready,
            "real_task_adapters_validated":phase4_real_task_adapters_valid,
            "code_commit":phase4_harness_revision.get("commit"),
            "source_memory_manifest_sha256":phase4_harness_before.get(
                "manifest_sha256"
            ),
            "checks":phase4_harness_checks,
            "read_error":phase4_harness_error,
        },
        "phase4_real_adapters":{
            "document_path":str(phase4_real_adapter_doc_path),
            "adapter_map_path":str(phase4_real_adapter_map_path),
            "audit_path":str(phase4_real_adapter_audit_path),
            "audit_exists":phase4_real_adapter_audit,
            "validated":phase4_real_task_adapters_valid,
            "mode":phase4_real_adapter_payload.get("mode"),
            "authority_scope":phase4_real_adapter_payload.get("authority_scope"),
            "live_world_mutation":phase4_real_adapter_payload.get(
                "live_world_mutation"
            ),
            "factorio_rcon_used":phase4_real_adapter_payload.get(
                "factorio_rcon_used"
            ),
            "code_commit":phase4_real_adapter_revision.get("commit"),
            "source_memory_manifest_sha256":phase4_real_adapter_before.get(
                "manifest_sha256"
            ),
            "checks":phase4_real_adapter_checks,
            "read_error":phase4_real_adapter_error,
        },
        "phase4_treatment":{
            "document_path":str(phase4_treatment_doc_path),
            "audit_path":str(phase4_treatment_audit_path),
            "audit_exists":phase4_treatment_audit,
            "validated":phase4_treatment_valid,
            "policy_version":phase4_treatment_payload.get("policy_version"),
            "checks":phase4_treatment_checks,
            "read_error":phase4_treatment_error,
        },
        "phase4_pilot_runner":{
            "script_path":str(phase4_pilot_runner_script_path),
            "runtime_path":str(phase4_pilot_runner_runtime_path),
            "document_path":str(phase4_pilot_runner_doc_path),
            "pair_auditor_path":str(phase4_pilot_pair_auditor_path),
            "validator_path":str(phase4_pilot_runner_validator_path),
            "audit_path":str(phase4_pilot_runner_audit_path),
            "audit_exists":phase4_pilot_runner_audit,
            "validated":phase4_pilot_runner_valid,
            "checks":phase4_pilot_runner_checks,
            "read_error":phase4_pilot_runner_error,
        },
        "phase4_pilot_progress":{
            "status":phase4_pilot_progress_status,
            "total":phase4_pilot_total,
            "pair_count":phase4_pilot_pair_count,
            "reviewed_count":phase4_pilot_reviewed_count,
            "complete":phase4_pilot_complete,
            "valid":phase4_pilot_progress_valid,
            "seed_launch_allowed":phase4_pilot_seed_launch_allowed,
            "next_seed":phase4_pilot_next_seed,
            "awaiting_review_seed":phase4_pilot_awaiting_review_seed,
            "out_of_order_seeds":phase4_pilot_out_of_order,
            "errors":phase4_pilot_progress_errors,
            "rows":phase4_pilot_rows,
            "claim_boundary":{
                "instrumentation_only":True,
                "variance_adaptation_forbidden":True,
                "primary_f4c_inference":False,
                "evaluation_partition_frozen":True,
                "confirmatory_partition_frozen":True,
            },
        },
        "phase4_evaluation_runner":{
            "document_path":str(phase4_evaluation_doc_path),
            "runner_path":str(phase4_evaluation_runner_script_path),
            "validator_path":str(phase4_evaluation_validator_path),
            "pair_auditor_path":str(phase4_evaluation_pair_auditor_path),
            "analyzer_path":str(phase4_evaluation_analyzer_path),
            "audit_path":str(phase4_evaluation_runner_audit_path),
            "audit_exists":phase4_evaluation_runner_audit,
            "validated":phase4_evaluation_runner_valid,
            "code_commit":phase4_evaluation_runner_revision.get("commit"),
            "checks":phase4_evaluation_runner_checks,
            "read_error":phase4_evaluation_runner_error,
        },
        "phase4_evaluation_progress":{
            "status":phase4_evaluation_progress_status,
            "total":phase4_evaluation_total,
            "pair_count":phase4_evaluation_pair_count,
            "reviewed_count":phase4_evaluation_reviewed_count,
            "valid_primary_count":phase4_evaluation_valid_primary_count,
            "technical_invalid_count":phase4_evaluation_technical_invalid_count,
            "complete":phase4_evaluation_complete,
            "valid":phase4_evaluation_progress_valid,
            "seed_launch_allowed":phase4_evaluation_seed_launch_allowed,
            "next_seed":phase4_evaluation_next_seed,
            "awaiting_review_seed":phase4_evaluation_awaiting_review_seed,
            "hard_fail_seeds":phase4_evaluation_hard_fail_seeds,
            "out_of_order_seeds":phase4_evaluation_out_of_order,
            "errors":phase4_evaluation_progress_errors,
            "rows":phase4_evaluation_rows,
            "claim_boundary":{
                "primary_f4c_inference":True,
                "pilot_evidence_reused":False,
                "replacement_seed_forbidden":True,
                "confirmatory_partition_frozen":True,
            },
        },
        "phase4_evaluation_inference":{
            "path":str(phase4_inference_path),
            "exists":phase4_inference_exists,
            "validated":phase4_inference_valid,
            "decision":phase4_primary_inference.get("decision"),
            "sample_sufficient":phase4_primary_inference.get(
                "sample_sufficient"
            ),
            "valid_pair_count":phase4_primary_inference.get(
                "valid_pair_count"
            ),
            "family_valid_counts":phase4_primary_inference.get(
                "family_valid_counts"
            ),
            "mean_delta_J":phase4_primary_inference.get("mean_delta_J"),
            "median_delta_J":phase4_primary_inference.get("median_delta_J"),
            "paired_cohens_dz":phase4_primary_inference.get(
                "paired_cohens_dz"
            ),
            "one_sided_exact_p":phase4_primary_inference.get(
                "one_sided_exact_p"
            ),
            "confidence_set_95pct":phase4_primary_inference.get(
                "confidence_set_95pct"
            ),
            "sesoi_delta_J":phase4_primary_inference.get("sesoi_delta_J"),
            "positive_causal_memory_result":phase4_primary_inference.get(
                "positive_causal_memory_result"
            ),
            "read_error":phase4_inference_error,
        },
        "phase4_exit_gate":{
            "memory_substrate":phase4_memory_valid,
            "hybrid_retrieval_consolidation_decay":phase4_retrieval_valid,
            "causal_memory_ablation_transfer":phase4_causal_positive,
            "validated":phase4_exit_gate_valid,
        },
        "phase4_blocker":{
            "code":(
                None
                if phase4_exit_gate_valid
                else (
                    (
                        "causal_transfer_pilot_progress_invalid"
                        if not phase4_pilot_progress_valid
                        else (
                            "causal_transfer_pilot_review_pending"
                            if phase4_pilot_awaiting_review_seed is not None
                            else (
                                (
                                    "causal_transfer_pilot_in_progress"
                                    if phase4_pilot_reviewed_count>0
                                    else "causal_transfer_pilot_not_executed"
                                )
                                if not phase4_pilot_complete
                                else (
                                    "causal_transfer_evaluation_runner_not_validated"
                                    if not phase4_evaluation_runner_valid
                                    else (
                                        "causal_transfer_evaluation_progress_invalid"
                                        if not phase4_evaluation_progress_valid
                                        else (
                                            "causal_transfer_evaluation_review_pending"
                                            if phase4_evaluation_awaiting_review_seed
                                            is not None
                                            else (
                                                "causal_transfer_evaluation_in_progress"
                                                if not phase4_evaluation_complete
                                                and phase4_evaluation_reviewed_count>0
                                                else (
                                                    "causal_transfer_evaluation_not_executed"
                                                    if not phase4_evaluation_complete
                                                    else (
                                                        "causal_transfer_inference_not_computed"
                                                        if not phase4_inference_valid
                                                        else (
                                                            "causal_transfer_inconclusive"
                                                            if phase4_primary_inference.get(
                                                                "decision"
                                                            )=="inconclusive"
                                                            else "causal_transfer_effect_not_established"
                                                        )
                                                    )
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                    if phase4_execution_ready
                    else (
                        "causal_transfer_pilot_runner_not_validated"
                        if phase4_treatment_valid
                        else (
                            "causal_transfer_treatment_not_validated"
                            if phase4_real_task_adapters_valid
                            else (
                                "causal_transfer_real_task_adapters_not_validated"
                                if phase4_harness_preflight_valid
                                else (
                                    "causal_transfer_evaluation_harness_not_validated"
                                    if phase4_causal_protocol_valid
                                    else "causal_transfer_protocol_not_frozen"
                                )
                            )
                        )
                    )
                )
            ),
            "status":(
                "clear"
                if phase4_exit_gate_valid
                else ("blocked" if phase4_retrieval_valid else "not_reached")
            ),
            "detail":(
                "F4-C causal memory transfer PASS under the frozen held-out "
                "primary inference; confirmatory seeds remain frozen."
                if phase4_exit_gate_valid
                else (
                    (
                        "Pilot evidence ledger is invalid or out of order."
                        if not phase4_pilot_progress_valid
                        else (
                            "Pilot pair review is pending before any later seed."
                            if phase4_pilot_awaiting_review_seed is not None
                            else (
                                "F4-C pilot instrumentation is still in progress."
                                if not phase4_pilot_complete
                                else (
                                    "Pilot 8/8 is frozen instrumentation-only; "
                                    "held-out evaluation runner is not validated."
                                    if not phase4_evaluation_runner_valid
                                    else (
                                        "Held-out evaluation evidence is invalid, "
                                        "hard-failed, or out of fixed order."
                                        if not phase4_evaluation_progress_valid
                                        else (
                                            "Held-out evaluation pair review is "
                                            "pending before any later fixed seed."
                                            if phase4_evaluation_awaiting_review_seed
                                            is not None
                                            else (
                                                (
                                                    "F4-C held-out evaluation is "
                                                    f"{phase4_evaluation_reviewed_count}/"
                                                    f"{phase4_evaluation_total} reviewed; "
                                                    f"next fixed seed is "
                                                    f"{phase4_evaluation_next_seed}."
                                                )
                                                if not phase4_evaluation_complete
                                                else (
                                                    "All 20 held-out pairs are "
                                                    "reviewed; preregistered primary "
                                                    "inference has not been computed."
                                                    if not phase4_inference_valid
                                                    else (
                                                        "Held-out evaluation is "
                                                        "inconclusive under the "
                                                        "minimum valid-pair rule."
                                                        if phase4_primary_inference.get(
                                                            "decision"
                                                        )=="inconclusive"
                                                        else (
                                                            "Held-out primary "
                                                            "inference completed but "
                                                            "the preregistered positive "
                                                            "causal-memory rule was not "
                                                            "satisfied."
                                                        )
                                                    )
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                    if phase4_execution_ready
                    else (
                        (
                            "Memory treatment semantics passed but pilot runner "
                            "readiness is not validated."
                        )
                        if phase4_treatment_valid
                        else (
                            (
                            "F4-C pre-experimental causal gates remain incomplete. "
                            "Confirmatory seeds remain frozen."
                        )
                            if phase4_retrieval_valid
                            else None
                        )
                    )
                )
            ),
        },
        "phase4_memory_retrieval":{
            "document_path":str(phase4_retrieval_doc_path),
            "document_exists":phase4_retrieval_doc,
            "audit_path":str(phase4_retrieval_audit_path),
            "audit_exists":phase4_retrieval_audit,
            "validated":phase4_retrieval_valid,
            "status":phase4_retrieval_payload.get("status"),
            "run_id":phase4_retrieval_payload.get("run_id"),
            "code_commit":phase4_retrieval_revision.get("commit"),
            "authority":phase4_retrieval_payload.get("authority"),
            "world_mutation":phase4_retrieval_payload.get("world_mutation"),
            "factorio_rcon_used":phase4_retrieval_payload.get(
                "factorio_rcon_used"
            ),
            "fle_environment_created":phase4_retrieval_payload.get(
                "fle_environment_created"
            ),
            "world_lease_acquired":phase4_retrieval_payload.get(
                "world_lease_acquired"
            ),
            "execution_grant_created":phase4_retrieval_payload.get(
                "execution_grant_created"
            ),
            "continuous_authority":phase4_retrieval_payload.get(
                "continuous_authority"
            ),
            "source":phase4_retrieval_source,
            "retrievals":phase4_retrievals,
            "consolidation":phase4_consolidation,
            "decay_probe":phase4_decay_probe,
            "checks":phase4_retrieval_checks,
            "f4a_artifact_hash_matches":(
                phase4_retrieval_source.get("f4a_artifact_sha256")
                ==phase4_f4a_sha
            ),
            "database_read_only_replay":(
                phase4_retrieval_before==phase4_retrieval_after
            ),
            "read_error":phase4_retrieval_error,
        },
        "phase4_memory_substrate":{
            "document_path":str(phase4_memory_doc_path),
            "document_exists":phase4_memory_doc,
            "audit_path":str(phase4_memory_audit_path),
            "audit_exists":phase4_memory_audit,
            "validated":phase4_memory_valid,
            "status":phase4_memory_payload.get("status"),
            "run_id":phase4_memory_payload.get("run_id"),
            "code_commit":phase4_memory_revision.get("commit"),
            "authority":phase4_memory_payload.get("authority"),
            "world_mutation":phase4_memory_payload.get("world_mutation"),
            "factorio_rcon_used":phase4_memory_payload.get(
                "factorio_rcon_used"
            ),
            "fle_environment_created":phase4_memory_payload.get(
                "fle_environment_created"
            ),
            "world_lease_acquired":phase4_memory_payload.get(
                "world_lease_acquired"
            ),
            "execution_grant_created":phase4_memory_payload.get(
                "execution_grant_created"
            ),
            "continuous_authority":phase4_memory_payload.get(
                "continuous_authority"
            ),
            "working_memory":phase4_memory_working,
            "store":phase4_memory_store,
            "memory_path":str(phase4_memory_path),
            "live_quick_check":phase4_live_quick_check,
            "live_schema":phase4_live_schema,
            "live_batch_occurrence_count":phase4_live_batch_count,
            "live_batch_manifest_sha256":phase4_live_manifest_sha,
            "batch_manifest_matches":(
                phase4_live_batch_count
                ==phase4_memory_batch.get("occurrence_count")
                and phase4_live_manifest_sha
                ==phase4_memory_batch.get("manifest_sha256")
            ),
            "checks":phase4_memory_checks,
            "read_error":phase4_memory_error,
        },
        "phase3_checkpoint":(
            "F3-C"
            if phase3_comparison_valid
            else (
                "F3-B"
                if phase3_credit_valid
                else ("F3-A" if phase3_executive_valid else None)
            )
        ),
        "phase3_exit_gate":{
            "executive_shadow_kernel":phase3_executive_valid,
            "verification_credit_ledger":phase3_credit_valid,
            "paired_shadow_comparison":phase3_comparison_valid,
            "same_goal_multiple_choices_observable":phase3_comparison_valid,
            "validated":phase3_exit_gate_valid,
        },
        "phase3_paired_shadow_comparison":{
            "document_path":str(phase3_comparison_doc_path),
            "document_exists":phase3_comparison_doc,
            "audit_path":str(phase3_comparison_audit_path),
            "audit_exists":phase3_comparison_audit,
            "validated":phase3_comparison_valid,
            "status":phase3_comparison_payload.get("status"),
            "run_id":phase3_comparison_payload.get("run_id"),
            "code_commit":phase3_comparison_revision.get("commit"),
            "authority":phase3_comparison_payload.get("authority"),
            "world_mutation":phase3_comparison_payload.get("world_mutation"),
            "factorio_rcon_used":phase3_comparison_payload.get(
                "factorio_rcon_used"
            ),
            "fle_environment_created":phase3_comparison_payload.get(
                "fle_environment_created"
            ),
            "world_lease_acquired":phase3_comparison_payload.get(
                "world_lease_acquired"
            ),
            "execution_grant_created":phase3_comparison_payload.get(
                "execution_grant_created"
            ),
            "continuous_authority":phase3_comparison_payload.get(
                "continuous_authority"
            ),
            "comparison":phase3_comparison,
            "pairs_still_match_observed_rows":phase3_pair_rows_match,
            "checks":phase3_comparison_checks,
            "read_error":phase3_comparison_error,
        },
        "phase3_verification_credit_ledger":{
            "document_path":str(phase3_credit_doc_path),
            "document_exists":phase3_credit_doc,
            "audit_path":str(phase3_credit_audit_path),
            "audit_exists":phase3_credit_audit,
            "validated":phase3_credit_valid,
            "status":phase3_credit_payload.get("status"),
            "run_id":phase3_credit_payload.get("run_id"),
            "code_commit":phase3_credit_revision.get("commit"),
            "authority":phase3_credit_payload.get("authority"),
            "world_mutation":phase3_credit_payload.get("world_mutation"),
            "factorio_rcon_used":phase3_credit_payload.get(
                "factorio_rcon_used"
            ),
            "fle_environment_created":phase3_credit_payload.get(
                "fle_environment_created"
            ),
            "world_lease_acquired":phase3_credit_payload.get(
                "world_lease_acquired"
            ),
            "execution_grant_created":phase3_credit_payload.get(
                "execution_grant_created"
            ),
            "continuous_authority":phase3_credit_payload.get(
                "continuous_authority"
            ),
            "verification":phase3_credit_verification,
            "credit":phase3_credit_assignment,
            "ledger":phase3_credit_ledger,
            "ledger_path":str(phase3_credit_ledger_path),
            "ledger_persisted_digests_match":phase3_credit_ledger_matches,
            "ledger_live_quick_check":phase3_credit_ledger_quick_check,
            "ledger_live_count":phase3_credit_ledger_count,
            "checks":phase3_credit_checks,
            "read_error":phase3_credit_error,
        },
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
            "observed_row_still_matches":phase3_observed_row_match,
            "current_source_sha256":phase3_repairs_sha,
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
                or (
                    phase2_exit_gate_valid
                    and not phase4_any_seed_launch_allowed
                )
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
