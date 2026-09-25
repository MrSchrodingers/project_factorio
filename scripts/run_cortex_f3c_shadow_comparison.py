#!/usr/bin/env python3
"""Canonical F3-C paired SHADOW comparison against the legacy runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.executive import (
    BeliefState,
    ExecutiveGoal,
    GoalDiagnosis,
    GoalKind,
    GoalStack,
    ScoreTablePolicy,
    run_shadow_executive,
)
from factorio_ai_lab.learning.repair_loop import (
    CAUSE_CHAIN_REACHES_NO_SINK,
    CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
    DEFICIT_DEAD_OUTPUT_CHAIN,
    DEFICIT_OUTPUT_UNPROCESSED,
    Deficit,
    Diagnosis,
    RepairObservation,
    select_action,
)

SCHEMA_VERSION = "cortex_f3c_paired_shadow_comparison_v1"
SYMPTOM_OUTPUT_UNPROCESSED = (
    "producer_output_unprocessed:output_buffered_not_processed"
)
SYMPTOM_CHAIN_NO_SINK = (
    "producer_chain_reaches_no_sink:chain_reaches_no_sink"
)
SUPPORTED = {
    SYMPTOM_OUTPUT_UNPROCESSED: (
        DEFICIT_OUTPUT_UNPROCESSED,
        CAUSE_OUTPUT_BUFFERED_NOT_PROCESSED,
    ),
    SYMPTOM_CHAIN_NO_SINK: (
        DEFICIT_DEAD_OUTPUT_CHAIN,
        CAUSE_CHAIN_REACHES_NO_SINK,
    ),
}
LEGACY_POLICY_SCORES = {
    "placement:place_processing_for_buffered_output": 3.0,
    "rebuild:reroute_producer_logistics": 2.0,
    "placement:connect_producer_to_consumer": 1.0,
}
FIXED_RULE_BASIS = "fixed_rule_no_history"

REBUILD_POLICY_SCORES = {
    "rebuild:reroute_producer_logistics": 3.0,
    "placement:place_processing_for_buffered_output": 2.0,
    "placement:connect_producer_to_consumer": 1.0,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git_revision(root: Path) -> dict[str, Any]:
    commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    branch = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={root}",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        cwd=root,
        text=True,
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "-c", f"safe.directory={root}", "status", "--porcelain"],
            cwd=root,
            text=True,
        ).strip()
    )
    return {"commit": commit, "branch": branch, "dirty": dirty}


def _paired_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    selected: list[tuple[int, dict[str, Any]]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            continue
        if row.get("symptom") not in SUPPORTED:
            continue
        if row.get("choice_basis") != FIXED_RULE_BASIS:
            continue
        if row.get("executed") is not False:
            continue
        action_key = row.get("action_key")
        targets = row.get("targets")
        if not isinstance(action_key, str) or not action_key:
            continue
        if not isinstance(targets, list) or not targets:
            continue
        selected.append((index, row))
    return selected


def _observed_identity(
    row_index: int,
    row: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "row_index": row_index,
        "run_id": row.get("run_id"),
        "generation": row.get("generation"),
        "stage": row.get("stage"),
        "symptom": row.get("symptom"),
        "action_key": row.get("action_key"),
        "choice_basis": row.get("choice_basis"),
        "executed": row.get("executed"),
        "targets": row.get("targets"),
        "outcome": row.get("outcome"),
    }
    return {
        **identity,
        "identity_sha256": _sha256_text(_canonical_json(identity)),
    }


def _diagnosis_for_row(
    *,
    goal_id: str,
    row: dict[str, Any],
) -> GoalDiagnosis:
    symptom = str(row["symptom"])
    deficit_kind, cause = SUPPORTED[symptom]
    targets = tuple(str(value) for value in row["targets"])
    return GoalDiagnosis(
        goal_id=goal_id,
        diagnosis=Diagnosis(
            deficit=Deficit(
                kind=deficit_kind,
                source="historical_repair_ledger",
                severity=float(len(targets)),
                entities=targets,
                measurement={
                    "observed_run_id": row.get("run_id"),
                    "observed_generation": row.get("generation"),
                    "observed_stage": row.get("stage"),
                    "observed_legacy_action_key": row.get("action_key"),
                },
            ),
            cause=cause,
            basis="observed_historical_symptom",
        ),
        basis="runs/repairs.jsonl paired shadow comparison",
    )


def build_replay(
    *,
    root: Path,
    repairs_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("F3-C canonical replay requires a clean source tree")

    source_sha = _sha256(repairs_path)
    selected = _paired_rows(repairs_path)
    legacy_policy = ScoreTablePolicy(
        policy_id="f3c-fixed-legacy-preference",
        scores=LEGACY_POLICY_SCORES,
    )
    rebuild_policy = ScoreTablePolicy(
        policy_id="f3c-fixed-rebuild-preference",
        scores=REBUILD_POLICY_SCORES,
    )
    observation = RepairObservation(graph={})
    pairs: list[dict[str, Any]] = []

    for row_index, row in selected:
        pair_id = f"f3c-pair-{row_index}"
        goal = ExecutiveGoal(
            goal_id=f"{pair_id}:restore-material-flow",
            objective="restore a producer-to-processor material path",
            kind=GoalKind.REPAIR,
            priority=10.0,
        )
        stack = GoalStack((goal,))
        diagnosis = _diagnosis_for_row(goal_id=goal.goal_id, row=row)
        belief = BeliefState(
            belief_id=f"{pair_id}:belief",
            source="runs/repairs.jsonl",
            metrics={
                "observed_generation": row.get("generation"),
                "observed_stage": row.get("stage"),
                "observed_executed": row.get("executed"),
            },
            unknowns=(
                "historical structural action was not executed",
                "counterfactual outcomes are unavailable",
            ),
        )
        legacy_run = run_shadow_executive(
            run_id=f"{pair_id}:legacy-policy",
            belief=belief,
            goal_stack=stack,
            goal_diagnosis=diagnosis,
            observation=observation,
            policy=legacy_policy,
        )
        rebuild_run = run_shadow_executive(
            run_id=f"{pair_id}:rebuild-policy",
            belief=belief,
            goal_stack=stack,
            goal_diagnosis=diagnosis,
            observation=observation,
            policy=rebuild_policy,
        )
        legacy_candidates = tuple(
            candidate.action.key
            for candidate in legacy_run.generation.candidates
        )
        rebuild_candidates = tuple(
            candidate.action.key
            for candidate in rebuild_run.generation.candidates
        )
        observed_action = str(row["action_key"])
        canonical_fixed_rule = select_action(
            tuple(
                candidate.action
                for candidate in legacy_run.generation.candidates
            ),
            score=None,
        )
        canonical_fixed_rule_key = (
            None if canonical_fixed_rule is None else canonical_fixed_rule.key
        )
        pair = {
            "pair_id": pair_id,
            "observed": _observed_identity(row_index, row),
            "goal": goal.to_dict(),
            "symptom": row["symptom"],
            "candidate_action_keys": list(legacy_candidates),
            "candidate_count": len(legacy_candidates),
            "same_candidate_set_between_policies": (
                legacy_candidates == rebuild_candidates
            ),
            "legacy_action_in_candidate_set": (
                observed_action in legacy_candidates
            ),
            "canonical_fixed_rule_action": canonical_fixed_rule_key,
            "canonical_fixed_rule_agrees_with_runner": (
                canonical_fixed_rule_key == observed_action
            ),
            "legacy_policy_choice": (
                legacy_run.decision.selected_action_key
            ),
            "rebuild_policy_choice": (
                rebuild_run.decision.selected_action_key
            ),
            "legacy_policy_agrees_with_runner": (
                legacy_run.decision.selected_action_key == observed_action
            ),
            "rebuild_policy_agrees_with_runner": (
                rebuild_run.decision.selected_action_key == observed_action
            ),
            "policies_diverge": (
                legacy_run.decision.selected_action_key
                != rebuild_run.decision.selected_action_key
            ),
            "legacy_prediction_before_action": (
                None
                if legacy_run.decision.prediction_before_action is None
                else legacy_run.decision.prediction_before_action.to_dict()
            ),
            "rebuild_prediction_before_action": (
                None
                if rebuild_run.decision.prediction_before_action is None
                else rebuild_run.decision.prediction_before_action.to_dict()
            ),
            "world_mutation": False,
            "execute_authorized": False,
        }
        pairs.append(pair)

    paired_count = len(pairs)
    multiple_candidates = sum(
        1 for pair in pairs if pair["candidate_count"] >= 2
    )
    same_candidate_sets = sum(
        1 for pair in pairs if pair["same_candidate_set_between_policies"]
    )
    legacy_action_coverage = sum(
        1 for pair in pairs if pair["legacy_action_in_candidate_set"]
    )
    fixed_rule_basis = sum(
        1
        for pair in pairs
        if pair["observed"].get("choice_basis") == FIXED_RULE_BASIS
    )
    canonical_fixed_rule_agreement = sum(
        1
        for pair in pairs
        if pair["canonical_fixed_rule_agrees_with_runner"]
    )
    legacy_agreement = sum(
        1 for pair in pairs if pair["legacy_policy_agrees_with_runner"]
    )
    rebuild_agreement = sum(
        1 for pair in pairs if pair["rebuild_policy_agrees_with_runner"]
    )
    policy_divergence = sum(
        1 for pair in pairs if pair["policies_diverge"]
    )
    observed_unexecuted = sum(
        1 for pair in pairs if pair["observed"]["executed"] is not True
    )
    outcome_reward_count = sum(
        1
        for pair in pairs
        if isinstance(pair["observed"].get("outcome"), dict)
        and pair["observed"]["outcome"].get("reward") is not None
    )
    checks = {
        "paired_episode_count_positive": paired_count > 0,
        "all_pairs_fixed_rule_no_history": fixed_rule_basis == paired_count,
        "canonical_fixed_rule_matches_every_runner_choice": (
            canonical_fixed_rule_agreement == paired_count
        ),
        "all_pairs_have_multiple_candidates": multiple_candidates == paired_count,
        "candidate_sets_policy_invariant": same_candidate_sets == paired_count,
        "legacy_action_covered_in_every_pair": (
            legacy_action_coverage == paired_count
        ),
        "fixed_legacy_policy_matches_every_runner_choice": (
            legacy_agreement == paired_count
        ),
        "alternative_policy_diverges_on_at_least_one_pair": (
            policy_divergence > 0
        ),
        "all_observed_structural_actions_unexecuted": (
            observed_unexecuted == paired_count
        ),
        "no_observed_reward_used_for_structural_comparison": (
            outcome_reward_count == 0
        ),
        "shadow_only": True,
        "world_mutation_false": True,
        "execute_authorized_false": True,
    }
    status = "pass" if all(checks.values()) else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": (
            "cortex-f3c-shadow-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "authority": "shadow",
        "world_mutation": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "execution_grant_created": False,
        "continuous_authority": False,
        "source": {
            "path": str(repairs_path.relative_to(root)),
            "sha256": source_sha,
            "supported_symptoms": sorted(SUPPORTED),
        },
        "policies": {
            "legacy_preference": {
                "policy_id": legacy_policy.policy_id,
                "scores": dict(LEGACY_POLICY_SCORES),
            },
            "rebuild_preference": {
                "policy_id": rebuild_policy.policy_id,
                "scores": dict(REBUILD_POLICY_SCORES),
            },
        },
        "comparison": {
            "paired_episode_count": paired_count,
            "fixed_rule_basis_count": fixed_rule_basis,
            "canonical_fixed_rule_agreement": canonical_fixed_rule_agreement,
            "canonical_fixed_rule_agreement_rate": (
                canonical_fixed_rule_agreement / paired_count
                if paired_count
                else None
            ),
            "multiple_candidate_pairs": multiple_candidates,
            "candidate_set_policy_invariant_pairs": same_candidate_sets,
            "legacy_action_coverage": legacy_action_coverage,
            "legacy_policy_agreement": legacy_agreement,
            "legacy_policy_agreement_rate": (
                legacy_agreement / paired_count if paired_count else None
            ),
            "rebuild_policy_agreement": rebuild_agreement,
            "rebuild_policy_agreement_rate": (
                rebuild_agreement / paired_count if paired_count else None
            ),
            "policy_divergence_pairs": policy_divergence,
            "policy_divergence_rate": (
                policy_divergence / paired_count if paired_count else None
            ),
            "observed_unexecuted_pairs": observed_unexecuted,
            "observed_reward_count": outcome_reward_count,
        },
        "pairs": pairs,
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "the same observed objectives are replayed through an explicit executive interface",
                "every paired historical objective exposes multiple candidate actions",
                "legacy choices remain representable inside the Cortex candidate sets",
                "choice policy is separable from candidate generation",
                "fixed policies produce observable agreement and divergence on identical candidates",
                "stage order is not required to express the compared choices",
            ],
            "does_not_prove": [
                "the alternative policy is better",
                "counterfactual outcome correctness",
                "learned policy superiority",
                "live executive authority",
                "sustained autonomy",
            ],
        },
    }


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"canonical F3-C artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument(
        "--repairs",
        type=Path,
        default=Path("runs/repairs.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "runs/audits/cortex_f3c_paired_shadow_comparison.json"
        ),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    repairs = args.repairs if args.repairs.is_absolute() else root / args.repairs
    output = args.output if args.output.is_absolute() else root / args.output
    payload = build_replay(
        root=root,
        repairs_path=repairs,
        revision=_git_revision(root),
    )
    _write_artifact(output, payload)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
