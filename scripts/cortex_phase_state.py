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
        "phase2_checkpoint":(
            "F2-C"
            if phase2_structural
            else ("F2-B" if phase2_parity else ("F2-A" if phase2_started else None))
        ),
        "phase2_parity":{
            "path":str(phase2_parity_path),
            "exists":phase2_parity,
        },
        "phase2_structural":{
            "path":str(phase2_structural_path),
            "exists":phase2_structural,
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
